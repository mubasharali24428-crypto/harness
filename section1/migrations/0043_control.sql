-- 0043: simultaneous control and conflicts (part 42).
-- control_leases: the run leader (run_participants.role = leader) IS the lease
-- holder, the lease adds expiry and renewal to that role (one row per run, so
-- exclusivity is also a primary key). control_requests: FIFO queue of people
-- asking for control. control_instructions: suggestions from non-holders,
-- queued for the holder. run_intent_versions: append-only versioned run intent
-- (goal, scope paths, permissions), the primary key (run_id, version) is the
-- compare-and-swap that rejects stale edits. control_conflicts: edits that
-- collided with a newer version, or proposals from non-holders, awaiting the
-- holder's resolution. JSON values are stored as TEXT.
-- Rollback: DROP TABLE control_conflicts, run_intent_versions,
-- control_instructions, control_requests, control_leases (product data only,
-- audit_log untouched).
-- Runner-safe: no dollar quotes, no transaction control, no semicolons in
-- comments, and no now() default so the file also loads on SQLite for tests.

CREATE TABLE IF NOT EXISTS control_leases (
    run_id VARCHAR(36) PRIMARY KEY REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    holder_user_id VARCHAR(36) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    renewed_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS control_requests (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ,
    CONSTRAINT control_requests_state_chk
        CHECK (state IN ('pending', 'granted', 'declined', 'cancelled', 'expired'))
);

-- One open request per person per run (a retry is a no-op).
CREATE UNIQUE INDEX IF NOT EXISTS control_requests_one_pending
    ON control_requests (run_id, user_id) WHERE state = 'pending';

CREATE INDEX IF NOT EXISTS control_requests_queue
    ON control_requests (run_id, state, created_at);

CREATE TABLE IF NOT EXISTS control_instructions (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    author_user_id VARCHAR(36) NOT NULL,
    content TEXT NOT NULL,
    request_id VARCHAR(64),
    state VARCHAR(16) NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ,
    reason TEXT,
    event_seq BIGINT,
    CONSTRAINT control_instructions_state_chk
        CHECK (state IN ('queued', 'accepted', 'rejected'))
);

CREATE INDEX IF NOT EXISTS control_instructions_queue
    ON control_instructions (run_id, state, created_at);

-- Idempotent client retries: same (run, request_id) is queued once.
CREATE UNIQUE INDEX IF NOT EXISTS control_instructions_request
    ON control_instructions (run_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS run_intent_versions (
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    goal TEXT NOT NULL DEFAULT '',
    scope_paths TEXT NOT NULL DEFAULT '[]',
    permissions TEXT NOT NULL DEFAULT '[]',
    changed_fields TEXT NOT NULL DEFAULT '[]',
    updated_by VARCHAR(36) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    request_id VARCHAR(64),
    PRIMARY KEY (run_id, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS run_intent_versions_request
    ON run_intent_versions (run_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS control_conflicts (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    kind VARCHAR(16) NOT NULL,
    author_user_id VARCHAR(36) NOT NULL,
    base_version INTEGER NOT NULL,
    proposed TEXT NOT NULL,
    conflict_fields TEXT NOT NULL DEFAULT '[]',
    state VARCHAR(16) NOT NULL DEFAULT 'open',
    resolution VARCHAR(16),
    resolved_by VARCHAR(36),
    resolved_at TIMESTAMPTZ,
    resolved_version INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT control_conflicts_kind_chk CHECK (kind IN ('conflict', 'proposal')),
    CONSTRAINT control_conflicts_state_chk CHECK (state IN ('open', 'resolved'))
);

CREATE INDEX IF NOT EXISTS control_conflicts_run
    ON control_conflicts (run_id, state, created_at);
