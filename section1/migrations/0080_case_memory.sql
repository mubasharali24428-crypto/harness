-- Sprint 2 Task 14: case-base error memory (section4 harness_orchestrator/case_memory.py).
-- One row per denial stop. status is always 'proposed' (CD-02): a case is shown to the human
-- next to a new stop; promoting its suggested rule is still the human PUT on the rules API.
-- Decay is applied on read (weight * 0.5 ** (age / half-life)); nothing rewrites rows.
-- Every read filters team_id: a case never crosses a tenant boundary.
CREATE TABLE IF NOT EXISTS case_memory (
    case_id        VARCHAR(36) PRIMARY KEY,
    team_id        VARCHAR(36) NOT NULL,
    project_id     VARCHAR(36),
    run_id         VARCHAR(36) NOT NULL,
    failure_class  VARCHAR(40) NOT NULL,
    tool           VARCHAR(40) NOT NULL,
    paths          TEXT NOT NULL,
    fingerprint    VARCHAR(64) NOT NULL,
    outcome        VARCHAR(16) NOT NULL CHECK (outcome IN ('recovered', 'denied', 'repeat')),
    weight         DOUBLE PRECISION NOT NULL,
    suggested_rule VARCHAR(400),
    status         VARCHAR(16) NOT NULL DEFAULT 'proposed' CHECK (status = 'proposed'),
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS case_memory_team_recent ON case_memory (team_id, recorded_at DESC);
