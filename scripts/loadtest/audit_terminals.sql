-- W17: duplicate/lost terminal-transition audit for the dry-engine load ladder.
-- Read-only. One JSON object on stdout. Run it against the ISOLATED loadtest database only,
-- after the queue has drained (run_ladder.sh does both).
--
-- Per run_id in agent_events (the durable log, the single source of truth):
--   started          execution_started rows         expected 1 (no retries in a clean dry run)
--   engine_terminal  engine_completed|engine_failed expected 1 for every started run
--   review_terminal  review_passed|review_failed    at most 1
-- lost       = started, but no engine terminal event          (a transition that never landed)
-- duplicate  = more than one engine terminal / review terminal / start (a transition landed twice)
-- conflicting= both engine_completed and engine_failed
-- unreviewed = engine_completed but no review terminal; the load journey always closes the
--              done-gate, so these are a journey that stopped (client timeout), reported not hidden.
WITH per_run AS (
  SELECT run_id,
         count(*) FILTER (WHERE type = 'execution_started')                       AS started,
         count(*) FILTER (WHERE type = 'engine_completed')                        AS completed,
         count(*) FILTER (WHERE type = 'engine_failed')                           AS failed,
         count(*) FILTER (WHERE type IN ('review_passed', 'review_failed'))       AS review_terminal,
         count(*) FILTER (WHERE type = 'review_passed')                           AS passed
  FROM agent_events
  WHERE run_id IS NOT NULL
  GROUP BY run_id
)
SELECT json_build_object(
  'runs',                 count(*),
  'started',              count(*) FILTER (WHERE started > 0),
  'engineCompleted',      count(*) FILTER (WHERE completed > 0),
  'engineFailed',         count(*) FILTER (WHERE failed > 0),
  'reviewPassed',         count(*) FILTER (WHERE passed > 0),
  'lost',                 count(*) FILTER (WHERE started > 0 AND completed + failed = 0),
  'duplicateStart',       count(*) FILTER (WHERE started > 1),
  'duplicateEngineTerminal', count(*) FILTER (WHERE completed + failed > 1),
  'duplicateReviewTerminal', count(*) FILTER (WHERE review_terminal > 1),
  'conflictingEngineTerminal', count(*) FILTER (WHERE completed > 0 AND failed > 0),
  'unreviewed',           count(*) FILTER (WHERE completed > 0 AND review_terminal = 0),
  'terminalWithoutStart', count(*) FILTER (WHERE started = 0 AND completed + failed > 0),
  'leftoverClaims',       (SELECT count(*) FROM run_claims),
  'duplicateSeq',         (SELECT count(*) FROM (SELECT seq FROM agent_events GROUP BY seq HAVING count(*) > 1) d)
)
FROM per_run;
