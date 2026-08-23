-- Wave 9 node B: per-team budget cap override (additive, nullable).
-- NULL = no per-team override; the orchestrator then falls back to the
-- HARNESS_TEAM_BUDGET_USD environment default (unset = unlimited).
ALTER TABLE teams ADD COLUMN budget_usd NUMERIC(12,2) NULL;
