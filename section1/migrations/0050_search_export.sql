-- 0050: search, history export and data deletion requests (master prompt part 50, Session 6 lane SE).
-- search_documents is a derived, rebuildable index over comments, run events, runs, sessions,
-- approvals and artifact metadata. It is NEVER the authority: every hit is re-authorised and its
-- snippet rebuilt from the live source when it is served (section4 search.py), so a stale row
-- can only cost a missed hit, never a leak. search_index_state holds one watermark per
-- (team, source) for the incremental indexer. export_jobs is the record of every export (who,
-- what scope, which format, size, digest, expiry, download count). The file itself lives in the
-- export store (HARNESS_EXPORT_DIR) and is deleted at expiry or by a deletion request.
-- deletion_requests: a user asks for their own authored data to be deleted, an admin decides.
-- Portable SQL (PostgreSQL + SQLite for tests): JSON is stored as TEXT, no now() defaults.
-- The optional PostgreSQL full text index is NOT created here (it is not valid on SQLite).
-- Suggested PG-only follow-up: CREATE INDEX idx_search_documents_fts ON search_documents
-- USING gin (to_tsvector('simple', title || ' ' || body)).
-- Rollback: DROP TABLE deletion_requests, export_jobs, search_index_state, search_documents
-- (derived and job data only, audit_log untouched).
-- Runner-safe: no dollar quotes, no transaction control, no semicolons in comments.

CREATE TABLE IF NOT EXISTS search_documents (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    kind VARCHAR(12) NOT NULL,
    source_id VARCHAR(100) NOT NULL,
    run_id VARCHAR(36),
    session_id VARCHAR(36),
    project_id VARCHAR(36),
    workspace_id VARCHAR(36),
    status VARCHAR(40),
    approval_state VARCHAR(16),
    title VARCHAR(300) NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    participant_ids TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT search_documents_kind_chk CHECK (
        kind IN ('run', 'session', 'comment', 'event', 'artifact', 'approval')),
    CONSTRAINT uq_search_documents_source UNIQUE (team_id, kind, source_id)
);
CREATE INDEX IF NOT EXISTS idx_search_documents_team_created
    ON search_documents (team_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_search_documents_team_run
    ON search_documents (team_id, run_id);
CREATE INDEX IF NOT EXISTS idx_search_documents_team_session
    ON search_documents (team_id, session_id);

CREATE TABLE IF NOT EXISTS search_index_state (
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    source VARCHAR(24) NOT NULL,
    watermark VARCHAR(64) NOT NULL DEFAULT '',
    last_synced_at TIMESTAMPTZ,
    docs_indexed BIGINT NOT NULL DEFAULT 0,
    last_error VARCHAR(200),
    PRIMARY KEY (team_id, source)
);

CREATE TABLE IF NOT EXISTS export_jobs (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    requested_by VARCHAR(36) NOT NULL,
    requester_role VARCHAR(16) NOT NULL,
    kind VARCHAR(20) NOT NULL,
    scope_kind VARCHAR(8) NOT NULL,
    scope_id VARCHAR(80) NOT NULL,
    format VARCHAR(8) NOT NULL,
    options TEXT NOT NULL DEFAULT '{}',
    state VARCHAR(12) NOT NULL DEFAULT 'running',
    error_code VARCHAR(40),
    record_count INTEGER NOT NULL DEFAULT 0,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256 VARCHAR(64),
    storage_ref VARCHAR(80),
    request_id VARCHAR(64),
    download_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT export_jobs_kind_chk CHECK (
        kind IN ('session_history', 'run_history', 'audit_evidence')),
    CONSTRAINT export_jobs_scope_chk CHECK (scope_kind IN ('session', 'run', 'team')),
    CONSTRAINT export_jobs_format_chk CHECK (format IN ('json', 'ndjson')),
    CONSTRAINT export_jobs_state_chk CHECK (
        state IN ('running', 'completed', 'failed', 'refused', 'expired', 'deleted'))
);
CREATE INDEX IF NOT EXISTS idx_export_jobs_team_created ON export_jobs (team_id, created_at);
-- A retried request id from the same requester is one job.
CREATE UNIQUE INDEX IF NOT EXISTS uq_export_jobs_request
    ON export_jobs (team_id, requested_by, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS deletion_requests (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    subject_user_id VARCHAR(36) NOT NULL,
    requested_by VARCHAR(36) NOT NULL,
    reason VARCHAR(500),
    state VARCHAR(12) NOT NULL DEFAULT 'pending',
    request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ,
    decision_note VARCHAR(500),
    executed_at TIMESTAMPTZ,
    result TEXT,
    CONSTRAINT deletion_requests_state_chk CHECK (
        state IN ('pending', 'approved', 'rejected', 'executed'))
);
CREATE INDEX IF NOT EXISTS idx_deletion_requests_team ON deletion_requests (team_id, created_at);
-- One open request per person: a second one while the first is undecided or unexecuted is refused.
CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_requests_open
    ON deletion_requests (team_id, subject_user_id) WHERE state IN ('pending', 'approved');
CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_requests_request
    ON deletion_requests (team_id, requested_by, request_id) WHERE request_id IS NOT NULL;
