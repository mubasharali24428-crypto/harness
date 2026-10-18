-- 0035: shared-run collaboration (multiplayer pilot, session 2).
-- participants + one leader per run, append-only collaboration history
-- (redirects, handoffs), explicit handoffs, and high-impact approvals bound
-- to a proposal digest. Identity columns are users.id strings taken from the
-- authenticated principal, never from client input.
-- Rollback: DROP TABLE high_impact_approvals, run_handoffs,
-- run_collab_events, run_participants (product data only, audit_log untouched).
-- Runner-safe: no dollar quotes, no transaction control, no semicolons in
-- comments.

CREATE TABLE IF NOT EXISTS run_participants (
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'participant',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, user_id),
    CONSTRAINT run_participants_role_chk
        CHECK (role IN ('leader', 'participant', 'observer'))
);

-- At most one leader per run, enforced by the database (race-safe).
CREATE UNIQUE INDEX IF NOT EXISTS run_participants_one_leader
    ON run_participants (run_id) WHERE role = 'leader';

CREATE TABLE IF NOT EXISTS run_collab_events (
    seq BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    actor_user_id VARCHAR(36) NOT NULL,
    kind VARCHAR(40) NOT NULL,
    request_id VARCHAR(64),
    payload JSONB NOT NULL DEFAULT '{}',
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS run_collab_events_run_seq
    ON run_collab_events (run_id, seq);

-- Idempotent client retries: same (run, request_id) is recorded once.
CREATE UNIQUE INDEX IF NOT EXISTS run_collab_events_request
    ON run_collab_events (run_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS run_handoffs (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    from_user_id VARCHAR(36) NOT NULL,
    to_user_id VARCHAR(36) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    CONSTRAINT run_handoffs_state_chk
        CHECK (state IN ('pending', 'accepted', 'rejected', 'cancelled'))
);

-- One open handoff per run: two operators cannot both believe they own it.
CREATE UNIQUE INDEX IF NOT EXISTS run_handoffs_one_pending
    ON run_handoffs (run_id) WHERE state = 'pending';

CREATE TABLE IF NOT EXISTS high_impact_approvals (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    requester_user_id VARCHAR(36) NOT NULL,
    action VARCHAR(16) NOT NULL,
    paths JSONB NOT NULL,
    summary TEXT NOT NULL,
    diff TEXT NOT NULL DEFAULT '',
    digest VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'pending',
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    outcome TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT high_impact_state_chk CHECK (state IN (
        'pending', 'approved', 'rejected', 'invalidated', 'expired',
        'executing', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS high_impact_approvals_run
    ON high_impact_approvals (run_id, created_at);
