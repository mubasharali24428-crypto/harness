-- Phase 1: durable run queue (crash-resumable execution, per-team caps).
-- Timestamps are UTC ISO-8601 TEXT (same discipline as 0005).
-- Deliberately NO FK to agent_runs: a claim may exist before the first
-- persisted event creates the agent_runs row (bus.ensure_run is lazy).
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,          -- 'queued' | 'running' | 'orphaned'
    claimed_at TEXT NOT NULL,            -- last (re)claim time
    heartbeat_at TEXT NOT NULL,
    worker_id VARCHAR(80) NOT NULL
);
CREATE INDEX idx_run_claims_state ON run_claims (state);
CREATE INDEX idx_run_claims_heartbeat ON run_claims (heartbeat_at);
