-- 0041: collaboration sessions (master prompt part 40, Session 4 lane S).
-- A collab session is a team-owned container for many runs (agent_runs) and members
-- (humans and agent identities). It is separate from a run: archiving or deleting a
-- session never touches agent_runs rows, it only stops NEW runs from starting.
-- States: active, paused, completed, archived, deleted (soft delete with purge_after).
-- Leaving or losing access never deletes a session_members row (status + name snapshot).
-- Portable SQL (PostgreSQL + SQLite for tests): JSON is stored as TEXT.
-- Rollback: DROP TABLE session_templates, session_runs, session_members, collab_sessions.
-- Runner-safe: no dollar quoting, no transaction control, no semicolons in comments.

CREATE TABLE IF NOT EXISTS collab_sessions (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    workspace_id VARCHAR(36),
    title VARCHAR(120) NOT NULL,
    description VARCHAR(1000) NOT NULL DEFAULT '',
    owner_user_id VARCHAR(36) NOT NULL,
    leader_user_id VARCHAR(36),
    state VARCHAR(16) NOT NULL DEFAULT 'active',
    state_reason VARCHAR(500),
    version INTEGER NOT NULL DEFAULT 1,
    retention_days INTEGER NOT NULL DEFAULT 30,
    template_name VARCHAR(80),
    template_version INTEGER,
    client_request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ,
    purge_after TIMESTAMPTZ,
    purged_at TIMESTAMPTZ,
    CONSTRAINT ck_collab_sessions_state
        CHECK (state IN ('active','paused','completed','archived','deleted'))
);
CREATE INDEX IF NOT EXISTS idx_collab_sessions_team_state ON collab_sessions (team_id, state);
CREATE INDEX IF NOT EXISTS idx_collab_sessions_workspace ON collab_sessions (workspace_id);
-- Idempotent create: the same client request id from the same owner is one session.
CREATE UNIQUE INDEX IF NOT EXISTS uq_collab_sessions_request
    ON collab_sessions (team_id, owner_user_id, client_request_id)
    WHERE client_request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS session_members (
    id VARCHAR(36) PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL REFERENCES collab_sessions (id) ON DELETE CASCADE,
    kind VARCHAR(8) NOT NULL DEFAULT 'user',
    user_id VARCHAR(36),
    agent_id VARCHAR(80),
    role VARCHAR(16) NOT NULL DEFAULT 'participant',
    display_name VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    added_by VARCHAR(36),
    joined_at TIMESTAMPTZ NOT NULL,
    left_at TIMESTAMPTZ,
    CONSTRAINT ck_session_members_kind CHECK (kind IN ('user','agent')),
    CONSTRAINT ck_session_members_role CHECK (role IN ('owner','participant','observer','agent')),
    CONSTRAINT ck_session_members_status CHECK (status IN ('active','left','removed')),
    CONSTRAINT ck_session_members_identity CHECK (
        (kind = 'user' AND user_id IS NOT NULL AND agent_id IS NULL)
        OR (kind = 'agent' AND agent_id IS NOT NULL AND user_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_members_user
    ON session_members (session_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_members_agent
    ON session_members (session_id, agent_id) WHERE agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_session_members_user ON session_members (user_id, status);

-- A run belongs to at most one session (run_id is the key). Hard delete of a link is an
-- explicit unlink and is audited. parent_run_id must be another run linked to the same session.
CREATE TABLE IF NOT EXISTS session_runs (
    run_id VARCHAR(36) PRIMARY KEY REFERENCES agent_runs (id) ON DELETE CASCADE,
    session_id VARCHAR(36) NOT NULL REFERENCES collab_sessions (id) ON DELETE CASCADE,
    parent_run_id VARCHAR(36),
    linked_by VARCHAR(36) NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_runs_session ON session_runs (session_id);
CREATE INDEX IF NOT EXISTS idx_session_runs_parent ON session_runs (parent_run_id);

CREATE TABLE IF NOT EXISTS session_templates (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    name VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL,
    defaults TEXT NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_session_template_version UNIQUE (team_id, name, version)
);
