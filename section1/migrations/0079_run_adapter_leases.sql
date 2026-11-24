-- Task 10: external agent adapter protocol v1 (docs/decisions/2026-11-24-external-agent-adapter.md).
-- A lease records that a human writer registered an external agent to OBSERVE a shared run.
-- It grants no authority: mode is always 'observer'; max_scope is what the agent ASKS for,
-- kept for review, never applied. Scope still widens only through the existing human
-- approval routes. digest = sha256 of the canonical registration (re-registration with a
-- different body is a different digest, visible in audit).
CREATE TABLE IF NOT EXISTS run_adapter_leases (
    id            VARCHAR(36) PRIMARY KEY,
    run_id        VARCHAR(36) NOT NULL,
    team_id       VARCHAR(36) NOT NULL,
    agent_id      VARCHAR(64) NOT NULL,
    protocol      VARCHAR(8) NOT NULL,
    capabilities  TEXT NOT NULL,
    max_scope     TEXT NOT NULL,
    pubkey_sha256 VARCHAR(64) NOT NULL,
    digest        VARCHAR(64) NOT NULL,
    mode          VARCHAR(16) NOT NULL DEFAULT 'observer' CHECK (mode = 'observer'),
    registered_by VARCHAR(64) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, agent_id)
);
