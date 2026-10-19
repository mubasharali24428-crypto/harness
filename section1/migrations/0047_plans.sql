-- 0047: task statement and versioned, reviewable plans (part 44, directives 001-003).
-- task_specs: one row per run (statement, success criteria, constraints, owner) with an
-- optimistic version. task_spec_revisions keeps one row per version (version 1 is the first save).
-- plan_versions: immutable plan content per run (content and its sha256 never change after the
-- insert, only state and the approval fields move). Version 1 is usually the lazy mirror of the
-- legacy agent_plans row (source_plan_id). plan_comments: reviewer comments on one version.
-- plan_edits: a proposed structured patch against one version, decided by an approve holder
-- (accepting creates a new plan version, resulting_version). JSON columns hold JSON text so the
-- same file loads on PostgreSQL and SQLite. Portable SQL, no dollar quotes, no transaction
-- control, no semicolons in comments, no now() default.
-- Rollback: DROP TABLE plan_edits, plan_comments, plan_versions, task_spec_revisions, task_specs
-- (product data only, audit_log untouched, agent_plans untouched).

CREATE TABLE IF NOT EXISTS task_specs (
    run_id VARCHAR(36) PRIMARY KEY REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    statement TEXT NOT NULL,
    success_criteria TEXT NOT NULL DEFAULT '[]',
    constraints TEXT NOT NULL DEFAULT '[]',
    owner_user_id VARCHAR(36),
    version INTEGER NOT NULL DEFAULT 1,
    updated_by VARCHAR(36) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT task_specs_version_chk CHECK (version >= 1)
);

CREATE TABLE IF NOT EXISTS task_spec_revisions (
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    statement TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    constraints TEXT NOT NULL,
    owner_user_id VARCHAR(36),
    updated_by VARCHAR(36) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, version)
);

CREATE TABLE IF NOT EXISTS plan_versions (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    author_kind VARCHAR(8) NOT NULL,
    author_id VARCHAR(64) NOT NULL,
    state VARCHAR(12) NOT NULL DEFAULT 'proposed',
    parent_version INTEGER,
    source_plan_id VARCHAR(36),
    from_edit_id VARCHAR(36),
    request_id VARCHAR(64),
    approved_sha256 VARCHAR(64),
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT plan_versions_run_version UNIQUE (run_id, version),
    CONSTRAINT plan_versions_version_chk CHECK (version >= 1),
    CONSTRAINT plan_versions_author_chk CHECK (author_kind IN ('agent', 'user')),
    CONSTRAINT plan_versions_state_chk CHECK (
        state IN ('draft', 'proposed', 'approved', 'superseded', 'rejected'))
);

CREATE INDEX IF NOT EXISTS plan_versions_team ON plan_versions (team_id, run_id);

-- The legacy row is mirrored once.
CREATE UNIQUE INDEX IF NOT EXISTS plan_versions_source
    ON plan_versions (source_plan_id) WHERE source_plan_id IS NOT NULL;

-- A retried create with the same request id is one version.
CREATE UNIQUE INDEX IF NOT EXISTS plan_versions_request
    ON plan_versions (run_id, author_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS plan_comments (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    plan_version INTEGER NOT NULL,
    author_user_id VARCHAR(36) NOT NULL,
    author_name_snapshot VARCHAR(200) NOT NULL,
    field_path VARCHAR(64),
    body TEXT NOT NULL,
    request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS plan_comments_version
    ON plan_comments (run_id, plan_version, created_at, id);

CREATE UNIQUE INDEX IF NOT EXISTS plan_comments_request
    ON plan_comments (run_id, author_user_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS plan_edits (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    base_version INTEGER NOT NULL,
    proposed_by VARCHAR(36) NOT NULL,
    patch TEXT NOT NULL,
    rationale VARCHAR(1000),
    state VARCHAR(10) NOT NULL DEFAULT 'pending',
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ,
    decision_note VARCHAR(500),
    resulting_version INTEGER,
    request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT plan_edits_state_chk CHECK (state IN ('pending', 'accepted', 'rejected'))
);

CREATE INDEX IF NOT EXISTS plan_edits_version
    ON plan_edits (run_id, base_version, created_at, id);

CREATE UNIQUE INDEX IF NOT EXISTS plan_edits_request
    ON plan_edits (run_id, proposed_by, request_id) WHERE request_id IS NOT NULL;
