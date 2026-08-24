-- Mission 13 Wave 3 lane H4a (H-023) — float money dies.
--
-- Every cost/spend column converges on NUMERIC(12,6): exact base-10 money,
-- no binary-float drift (0.1 + 0.2 = 0.30000000000000004 dies here).
--
-- Enumeration (grep over migrations/ + information_schema on the live catalog):
--   agent_plans.estimated_cost_usd   NUMERIC(12,6) since 0001 — already conformant,
--                                    restated idempotently so the guarantee is
--                                    explicit and re-runnable.
--   cost_ledger.cost_usd             NUMERIC(12,6) since 0001 — same.
--   teams.budget_usd                 cents-scale (12,2) from 0007 -> widened.
--   billing_plans.monthly_budget_usd cents-scale (12,2) from 0010 -> widened.
-- No other spend columns exist in the schema.
--
-- ALTER TYPE ... USING casts preserve all existing values exactly: cents-scale
-- data (e.g. 25.50) becomes 25.500000. Scale WIDENING never truncates;
-- precision stays 12 so the value domain only grows. Additive indexes
-- (uq_billing_plans_team_id et al.) are untouched. Append-only law respected:
-- 0010 keeps its original text; this migration supersedes its type.
--
-- Idempotent by construction: re-running against an already-(12,6) column is a
-- no-op recast, safe for scratch DBs whose tables were created via ORM
-- metadata before this file ran.

ALTER TABLE agent_plans
    ALTER COLUMN estimated_cost_usd TYPE NUMERIC(12, 6)
    USING estimated_cost_usd::numeric(12, 6);

ALTER TABLE cost_ledger
    ALTER COLUMN cost_usd TYPE NUMERIC(12, 6)
    USING cost_usd::numeric(12, 6);

ALTER TABLE teams
    ALTER COLUMN budget_usd TYPE NUMERIC(12, 6)
    USING budget_usd::numeric(12, 6);

ALTER TABLE billing_plans
    ALTER COLUMN monthly_budget_usd TYPE NUMERIC(12, 6)
    USING monthly_budget_usd::numeric(12, 6);
