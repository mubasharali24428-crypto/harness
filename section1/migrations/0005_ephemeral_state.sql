-- Phase 1: cross-process ephemeral auth state.
-- Timestamps live as UTC ISO-8601 TEXT, not TIMESTAMPTZ: the stores bind
-- pre-normalized ISO strings so the identical code path runs on PostgreSQL
-- and SQLite (unit tests), and same-zone ISO strings compare chronologically
-- as plain text. Writers: harness_identity.ephemeral / harness_github.state_store.
CREATE TABLE ws_tickets (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    role VARCHAR(20) NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_ws_tickets_expires ON ws_tickets (expires_at);

CREATE TABLE oauth_states (
    state VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_oauth_states_expires ON oauth_states (expires_at);
