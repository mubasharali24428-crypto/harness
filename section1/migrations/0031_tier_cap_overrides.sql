-- Mission 22 lane M2 — behavior-bearing plan tiers (per-team cap overrides).
--
-- billing_plans.plan already carries the tier ('free' | 'pro' | 'team',
-- CHECK from 0010). These columns are the per-team CAP overrides on top of
-- the tier: NULL = inherit the tier default from harness_contracts.tiers
-- (single source: DEFAULT_RUNS_PER_DAY / DEFAULT_CONTEXT_INDEX_MAX_CHUNKS).
-- Additive only — no frozen table or column is altered, no backfill needed.
ALTER TABLE billing_plans ADD COLUMN runs_per_day_override INTEGER;
ALTER TABLE billing_plans ADD COLUMN context_index_max_chunks_override INTEGER;
