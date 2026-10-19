-- 0037: server push of collaboration events (master prompt part 11.006/11.007).
-- Each new run_collab_events row sends a slim wake-up signal on the collab_events
-- channel (run id, seq and kind only, never the payload). The identity service
-- listens, re-reads the committed rows from the table and pushes them to the
-- run's WebSocket subscribers in cursor order. Same signal-not-payload design as
-- migration 0029 for agent_events. HTTP polling (collab-history) stays the fallback.
-- Also indexes the team-scoped audit/event export.
-- Rollback: DROP TRIGGER run_collab_events_notify ON run_collab_events, DROP FUNCTION
-- notify_collab_event, DROP INDEX idx_run_collab_events_team_seq
-- (history and rows are untouched).
-- Runner-safe: plain double dollar quotes, no transaction control.

CREATE OR REPLACE FUNCTION notify_collab_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('collab_events', json_build_object(
        'seq',   NEW.seq,
        'runId', NEW.run_id,
        'kind',  NEW.kind
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS run_collab_events_notify ON run_collab_events;
CREATE TRIGGER run_collab_events_notify
    AFTER INSERT ON run_collab_events
    FOR EACH ROW EXECUTE FUNCTION notify_collab_event();

CREATE INDEX IF NOT EXISTS idx_run_collab_events_team_seq
    ON run_collab_events (team_id, seq);
