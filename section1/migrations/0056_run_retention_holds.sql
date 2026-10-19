-- 0056: operator-managed run retention holds.
-- Retention workers must skip held runs. Creating/releasing holds remains a
-- privileged database operation until an audited governance API is available.
CREATE TABLE IF NOT EXISTS run_retention_holds (
    run_id VARCHAR(36) PRIMARY KEY REFERENCES agent_runs (id) ON DELETE CASCADE,
    held_by VARCHAR(36) NOT NULL,
    reason VARCHAR(500) NOT NULL CHECK (length(trim(reason)) >= 3),
    created_at TIMESTAMPTZ NOT NULL
);
