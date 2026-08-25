-- migration 0027: cost-gate O(1) indexes + daily spend rollup (M18-C3).
--
-- Problem (m17-findings-pbc1.md F6): every budget-gate evaluation
-- (section4 budget.check_budget on each run start / reviewer call) sums
-- cost_ledger joined agent_runs -> projects -> teams over the WHOLE table
-- with no usable index beyond the seq PK. Ledger grows per model call
-- (~20K-100K rows/day at load), so run-start latency grows linearly with
-- ledger age.
--
-- Part 1 -- created_at index. Serves the month-bounded SUM in section3
-- budget_guard.RESERVE_SQL (`l.created_at >= date_trunc('month', now())`)
-- and any time-windowed spend read: turns the full scan into an ordered
-- index range over just the window.
--
-- Part 2 -- cost_ledger_daily rollup, one row per (team_id, day):
--
--   cost_ledger_daily(team_id VARCHAR(36) FK teams(id),
--                     day DATE,
--                     total_usd NUMERIC(12,6),
--                     PRIMARY KEY (team_id, day))
--
-- The frozen cost_ledger carries NO team_id column (attribution flows
-- cost_ledger.run_id -> agent_runs.project_id -> projects.team_id), so the
-- graph's literal `(team_id, created_at)` index is unrealizable without
-- denormalizing a frozen table. This PK delivers the same team-scoped O(1)
-- read shape instead: the budget gate reads <= N-days single-row lookups
-- here plus ONE indexed live SUM for today's partial day (see
-- section4 budget.py _SPEND_ROLLUP_SQL), independent of ledger size.
--
-- Feed contract: deploy/runners/cost_rollup.py recomputes CLOSED days
-- (created_at < UTC midnight, DB clock) from source-of-truth cost_ledger
-- with DELETE+INSERT inside one transaction per pass. Recompute (not +=)
-- is deliberate: release() legitimately DELETES reserved ledger rows, which
-- an additive upsert can never undo. Recompute self-heals any drift.
-- Rows for TODAY are never stored here -- the gate always sums those live,
-- so the boundary stays exact while the sidecar runs.
--
-- Backfill: the sidecar fills history on its first --once pass. Until then
-- the gate falls back to the exact legacy whole-ledger SUM (empty rollup =
-- zero coverage = fallback), so correctness never depends on feed freshness.
--
-- Both statements are purely additive: no frozen table altered, no column
-- touched. Runner-compatible per deploy/runners/migrate.py: no dollar
-- quotes, no transaction control (the runner wraps the file in one
-- transaction), and comment lines carry no semicolons.

CREATE INDEX IF NOT EXISTS idx_cost_ledger_created_at
    ON cost_ledger (created_at);

CREATE TABLE IF NOT EXISTS cost_ledger_daily (
    team_id VARCHAR(36) NOT NULL,
    day DATE NOT NULL,
    total_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    PRIMARY KEY (team_id, day),
    FOREIGN KEY(team_id) REFERENCES teams (id)
);
