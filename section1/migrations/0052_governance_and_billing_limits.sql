-- Session 7 follow-up: high-impact governance stop and administrator limits.
-- Additive only; all fields default to the already enforced platform behavior.

ALTER TABLE organizations
    ADD COLUMN execution_disabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE billing_plans
    ADD COLUMN concurrent_runs_override INTEGER
        CHECK (concurrent_runs_override IS NULL OR concurrent_runs_override BETWEEN 1 AND 100);
ALTER TABLE billing_plans
    ADD COLUMN budget_warn_pct_override NUMERIC(5,2)
        CHECK (budget_warn_pct_override IS NULL OR budget_warn_pct_override BETWEEN 0 AND 99.99);
