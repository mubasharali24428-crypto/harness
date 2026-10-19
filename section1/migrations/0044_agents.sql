-- 0044: agents (master prompt part 45, Session 4 lane A).
-- agent_definitions: team-owned registry. One row per (name, version), states
--   proposed -> approved -> retired (approval workflow, see docs/agents.md).
-- agent_run_pools: per-run ceiling for the agent tree (total spawned + budget),
--   the parent of every root assignment so root spawns are limited like child spawns.
-- agent_assignments: one row per spawned agent task, parent_id builds the tree.
--   Spawn limits (depth, live children, budget) are enforced by compare-and-set
--   UPDATEs on the parent row (or the run pool for roots) in section4 agent_tree.py.
-- agent_tool_calls: tool invocation SUMMARIES and outcomes only (no hidden reasoning).
-- Money is integer micro-USD (1 USD = 1000000) so the CAS arithmetic is exact.
-- Portable SQL (PostgreSQL + SQLite for tests): JSON is stored as TEXT.
-- Rollback: DROP the four tables.
-- Runner-safe: no dollar quoting, no transaction control.

CREATE TABLE IF NOT EXISTS agent_definitions (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    name VARCHAR(80) NOT NULL,
    name_key VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'proposed',
    description VARCHAR(500) NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '[]',
    tool_permissions TEXT NOT NULL DEFAULT '[]',
    permissions TEXT NOT NULL DEFAULT '["read"]',
    allowed_paths TEXT NOT NULL DEFAULT '[]',
    network_allow TEXT NOT NULL DEFAULT '[]',
    credential_refs TEXT NOT NULL DEFAULT '[]',
    model VARCHAR(120) NOT NULL,
    fallback_model VARCHAR(120),
    tool_fallbacks TEXT NOT NULL DEFAULT '{}',
    max_depth INTEGER NOT NULL DEFAULT 1,
    max_children INTEGER NOT NULL DEFAULT 3,
    max_budget_microusd BIGINT NOT NULL DEFAULT 1000000,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    approved_by VARCHAR(36),
    approved_at TIMESTAMPTZ,
    retired_by VARCHAR(36),
    retired_at TIMESTAMPTZ,
    retired_reason VARCHAR(500),
    CONSTRAINT uq_agent_definitions_version UNIQUE (team_id, name_key, version),
    CONSTRAINT ck_agent_definitions_state CHECK (state IN ('proposed','approved','retired'))
);
CREATE INDEX IF NOT EXISTS idx_agent_definitions_team ON agent_definitions (team_id, state);

CREATE TABLE IF NOT EXISTS agent_run_pools (
    run_id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    max_agents INTEGER NOT NULL,
    total_spawned INTEGER NOT NULL DEFAULT 0,
    budget_microusd BIGINT NOT NULL,
    reserved_microusd BIGINT NOT NULL DEFAULT 0,
    spent_microusd BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_assignments (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    parent_id VARCHAR(36),
    agent_id VARCHAR(36) NOT NULL,
    agent_name VARCHAR(80) NOT NULL,
    agent_version INTEGER NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    title VARCHAR(200) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'active',
    pause_reason VARCHAR(200),
    delegated_by VARCHAR(36) NOT NULL,
    scope TEXT NOT NULL DEFAULT '{}',
    max_depth INTEGER NOT NULL DEFAULT 0,
    max_children INTEGER NOT NULL DEFAULT 0,
    children_count INTEGER NOT NULL DEFAULT 0,
    budget_microusd BIGINT NOT NULL DEFAULT 0,
    reserved_microusd BIGINT NOT NULL DEFAULT 0,
    spent_microusd BIGINT NOT NULL DEFAULT 0,
    model_requested VARCHAR(120) NOT NULL,
    model_used VARCHAR(120) NOT NULL,
    fallback_reason VARCHAR(200),
    progress_percent INTEGER,
    progress_note VARCHAR(280),
    request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    CONSTRAINT ck_agent_assignments_state
        CHECK (state IN ('active','paused','completed','cancelled','failed')),
    CONSTRAINT uq_agent_assignments_request UNIQUE (run_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_assignments_run ON agent_assignments (run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_assignments_parent ON agent_assignments (parent_id);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id VARCHAR(36) PRIMARY KEY,
    assignment_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    tool VARCHAR(80) NOT NULL,
    target VARCHAR(200),
    summary VARCHAR(500) NOT NULL,
    outcome VARCHAR(16) NOT NULL,
    fallback_tool VARCHAR(80),
    cost_microusd BIGINT NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    reported_by VARCHAR(36) NOT NULL,
    request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_agent_tool_calls_outcome
        CHECK (outcome IN ('ok','error','denied','unavailable')),
    CONSTRAINT uq_agent_tool_calls_request UNIQUE (assignment_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_assignment
    ON agent_tool_calls (assignment_id, created_at);
