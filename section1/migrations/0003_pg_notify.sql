-- migration 0003: cross-instance live fan-out via Postgres LISTEN/NOTIFY (G-LIVE)
-- Every process owns an EventBus. The DB log is one shared table. This trigger turns each
-- insert into a notification so ALL instances can push to their local subscribers.

CREATE OR REPLACE FUNCTION notify_agent_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('agent_events', json_build_object(
        'type',      NEW.type,
        'payload',   NEW.payload,
        'ts',        to_json(NEW.ts)::text,
        'seq',       NEW.seq,
        'projectId', NEW.project_id,
        'runId',     NEW.run_id
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_agent_events ON agent_events;
CREATE TRIGGER trg_notify_agent_events
AFTER INSERT ON agent_events
FOR EACH ROW EXECUTE FUNCTION notify_agent_event();
