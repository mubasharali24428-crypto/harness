-- Sprint 2 Task 15 (C-08 seam): W31 memory-tier proposals attached to a run. PROPOSAL ONLY:
-- nothing reads this table to move memory or dispatch work; an admin reads it. violations is
-- the W31 independent validator's output for the stored proposal (empty list = valid).
CREATE TABLE IF NOT EXISTS scheduler_proposals (
    id          VARCHAR(36) PRIMARY KEY,
    run_id      VARCHAR(36) NOT NULL,
    team_id     VARCHAR(36) NOT NULL,
    schema_tag  VARCHAR(32) NOT NULL,
    proposal    TEXT NOT NULL,
    violations  TEXT NOT NULL,
    created_by  VARCHAR(64) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scheduler_proposals_run ON scheduler_proposals (run_id, created_at DESC);
