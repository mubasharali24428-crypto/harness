-- Mission 10 ATLAS merge wiring (lead follow-up): index for derived
-- started/finished timestamps. runs_index.py probes MIN(ts) over
-- agent_events per returned run row; frozen 0001 gives agent_events only
-- a seq PK, so every probe degraded toward a full scan as the event log
-- grew. Purely additive — no frozen table altered.
CREATE INDEX idx_agent_events_run_id_type ON agent_events (run_id, type);
