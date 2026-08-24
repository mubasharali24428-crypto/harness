-- migration 0026: runs-index tuning at 100k events (ALEX-FORCE M16-X1, Squad ALPHA).
--
-- Enumerated query shapes (section4/src/harness_orchestrator/runs_index.py,
-- GET /api/v1/orchestrator/runs, after the M16-X1 rewrite):
--
--   (1) page query -- team-scoped, optionally filtered, keyset-paged:
--         SELECT r.id, r.project_id, r.state, r.created_at
--         FROM agent_runs r JOIN projects p ON p.id = r.project_id
--         WHERE p.team_id = :team_id             -- scope (always present)
--           AND [r.project_id = :project_id]     -- optional filter
--           AND [r.state = :run_state]           -- optional filter
--           AND [(r.created_at, r.id) < (:cursor_created, :cursor_run)]
--         ORDER BY r.created_at DESC, r.id DESC
--         LIMIT :limit;
--
--   (2) per-page lifecycle extremes -- ONE batched statement per marker
--       family (started: execution_started/resumed, finished:
--       engine_completed/engine_failed/pr_opened) for exactly the <= 100
--       runs on the page. Before M16-X1 this was two correlated
--       (SELECT MIN(ts) ...) probes PER ROW -- 2 x LIMIT probes per request.
--       The batched shape (see runs_index.py) groups MIN(ts) over the page's
--       run ids and marker types.
--
-- idx_agent_runs_team_created_id serves (1): equality on project_id (the
-- team->projects join key), then the exact (created_at DESC, id DESC)
-- keyset order as trailing columns, so each page is an ordered index range
-- instead of sorting every run the team ever created. The cursor predicate
-- is a pure continuation of the same ordering.
--
-- idx_agent_events_run_type_ts_covering serves (2): run_id + type as the
-- equality/IN prefix and ts as the payload column, so each grouped MIN(ts)
-- is answered by an index-only range scan -- zero heap visits on a
-- six-figure agent_events table. (Supersedes the (run_id, type) probe
-- support from 0012 for this path -- 0012 stays for other consumers.)
--
-- Both are purely additive CREATE INDEX IF NOT EXISTS statements: no frozen
-- table altered, no column touched. Runner-compatible per deploy/runners/
-- migrate.py: no dollar quotes, no transaction control (the runner wraps
-- the file in one transaction).

CREATE INDEX IF NOT EXISTS idx_agent_runs_team_created_id
    ON agent_runs (project_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_agent_events_run_type_ts_covering
    ON agent_events (run_id, type, ts);
