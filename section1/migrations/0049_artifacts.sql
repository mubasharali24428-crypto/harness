-- 0049: artifacts and version control (master prompt part 48, Session 6 lane AR).
-- artifacts: one row per stored file of a run. The bytes live in a content-addressed blob store
-- (storage_ref is a store key, never a client path). state is available, quarantined or deleted
-- (expired is computed from retention_until at read time and made real by purge_expired). scan
-- is a JSON text with the classifier verdict and reasons. media_type is the type the SERVER
-- derived, never the client's claim. A deleted row is a tombstone: bytes gone, metadata kept.
-- run_changes: one row per changed file path and per patch digest of a run, bound to the plan
-- version in force when it was recorded and to a review state. diff_sha is the sha256 of that
-- file's patch text. A new patch for the same path supersedes the old row (superseded = 1) and
-- starts at pending again, so an approval never carries over to different content.
-- artifact_lineage: explicit edges only (derived_from, evidenced_by). Edges that can be derived
-- from other tables are computed on read.
-- run_git_state: one row per run (no state shared between runs). repo, branches and shas are
-- recorded by the caller except where the API says it comes from the system. stale is 1 when the
-- recorded base sha differs from the observed head of the base branch. The unique index forbids
-- two runs of one team on the same head branch of the same repo.
-- merge_approvals: the HUMAN merge decision, a record separate from plan approval. It binds the
-- head sha and the digest of the reviewed changes. Later decisions are new rows, nothing is
-- updated or deleted.
-- JSON columns hold JSON text so the same file loads on PostgreSQL and SQLite. Portable SQL, no
-- dollar quotes, no transaction control, no semicolons in comments, no now() default.
-- Rollback: DROP TABLE merge_approvals, run_git_state, artifact_lineage, run_changes, artifacts
-- (product data only, audit_log untouched).

CREATE TABLE IF NOT EXISTS artifacts (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    name VARCHAR(200) NOT NULL,
    media_type VARCHAR(120) NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_ref VARCHAR(160) NOT NULL,
    kind VARCHAR(8) NOT NULL DEFAULT 'upload',
    state VARCHAR(12) NOT NULL DEFAULT 'available',
    scan TEXT NOT NULL DEFAULT '{}',
    request_id VARCHAR(64),
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    retention_until TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    deleted_by VARCHAR(64),
    CONSTRAINT artifacts_kind_chk CHECK (kind IN ('upload', 'output', 'diff', 'log')),
    CONSTRAINT artifacts_state_chk CHECK (state IN ('available', 'quarantined', 'deleted')),
    CONSTRAINT artifacts_size_chk CHECK (size_bytes >= 0)
);

CREATE INDEX IF NOT EXISTS artifacts_run ON artifacts (team_id, run_id, created_at);
CREATE INDEX IF NOT EXISTS artifacts_ref ON artifacts (storage_ref);
CREATE INDEX IF NOT EXISTS artifacts_retention ON artifacts (retention_until, state);

-- A retried upload with the same request id is one artifact.
CREATE UNIQUE INDEX IF NOT EXISTS artifacts_request
    ON artifacts (run_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS run_changes (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    path VARCHAR(512) NOT NULL,
    change_kind VARCHAR(8) NOT NULL,
    before_sha VARCHAR(64),
    after_sha VARCHAR(64),
    diff_sha VARCHAR(64) NOT NULL,
    flags TEXT NOT NULL DEFAULT '{}',
    plan_version INTEGER,
    review_state VARCHAR(8) NOT NULL DEFAULT 'pending',
    reviewed_by VARCHAR(64),
    reviewed_at TIMESTAMPTZ,
    superseded INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT run_changes_kind_chk CHECK (change_kind IN ('added', 'modified', 'deleted', 'renamed')),
    CONSTRAINT run_changes_review_chk CHECK (review_state IN ('pending', 'approved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS run_changes_run ON run_changes (team_id, run_id, superseded);

-- The same patch for the same path is recorded once.
CREATE UNIQUE INDEX IF NOT EXISTS run_changes_patch ON run_changes (run_id, path, diff_sha);

CREATE TABLE IF NOT EXISTS artifact_lineage (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    from_kind VARCHAR(20) NOT NULL,
    from_id VARCHAR(64) NOT NULL,
    to_kind VARCHAR(20) NOT NULL,
    to_id VARCHAR(64) NOT NULL,
    relation VARCHAR(24) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS artifact_lineage_run ON artifact_lineage (run_id);
CREATE UNIQUE INDEX IF NOT EXISTS artifact_lineage_edge
    ON artifact_lineage (run_id, from_kind, from_id, to_kind, to_id, relation);

CREATE TABLE IF NOT EXISTS run_git_state (
    run_id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    repo_full_name VARCHAR(400) NOT NULL DEFAULT '',
    base_branch VARCHAR(200),
    base_sha VARCHAR(64),
    head_branch VARCHAR(200),
    observed_head_sha VARCHAR(64),
    run_head_sha VARCHAR(64),
    stale INTEGER NOT NULL DEFAULT 0,
    checked_at TIMESTAMPTZ NOT NULL,
    updated_by VARCHAR(64) NOT NULL
);

-- One run per head branch per repo and team: concurrent runs never share a branch.
CREATE UNIQUE INDEX IF NOT EXISTS run_git_state_branch
    ON run_git_state (team_id, repo_full_name, head_branch) WHERE head_branch IS NOT NULL;

CREATE TABLE IF NOT EXISTS merge_approvals (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    decision VARCHAR(8) NOT NULL,
    head_sha VARCHAR(64),
    base_sha VARCHAR(64),
    changes_digest VARCHAR(64) NOT NULL,
    plan_version INTEGER,
    decided_by VARCHAR(64) NOT NULL,
    note VARCHAR(500),
    request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT merge_approvals_decision_chk CHECK (decision IN ('approved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS merge_approvals_run ON merge_approvals (team_id, run_id, created_at);

-- A retried decision with the same request id is one record.
CREATE UNIQUE INDEX IF NOT EXISTS merge_approvals_request
    ON merge_approvals (run_id, request_id) WHERE request_id IS NOT NULL;
