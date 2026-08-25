-- Mission 19 N2 (C8, pbc1 F3): the bus carries signals, not payloads.
--
-- The 0003 trigger embedded the FULL envelope — payload JSONB included — in
-- every pg_notify. Consequences:
--   * any envelope near Postgres's 8000-byte NOTIFY payload cap failed the
--     INSERT outright (the trigger fires per row);
--   * every listening process parsed the whole body of every event
--     (O(events x instances) JSON work), and the queue shipped ~payload-size
--     bytes per listener per event for bodies that were already durable in
--     agent_events.
--
-- This migration replaces the function so the wire carries a minimal wake-up
-- signal: {"seq": <global seq>, "projectId": ..., "type": ...} (~60 bytes).
-- Listeners fetch the committed row table-side (EventBus._fetch_envelopes,
-- one indexed PK batch per burst) and fan out locally. Postgres queues one
-- notification copy per listening session, so every byte removed here is
-- multiplied by the replica count.
--
-- Mechanics per repo conventions:
--   * CREATE OR REPLACE FUNCTION keeps the 0003 trigger object and its name;
--     no re-DROP needed. Same channel ('agent_events') so old and new
--     binaries interoperate during rolling restarts.
--   * json_build_object keys stay camelCase to match the envelope field the
--     listeners reconstruct.
--   * No BEGIN/COMMIT (deploy/runners/migrate.py wraps the file in one
--     transaction); plain $$ dollar quotes only.

CREATE OR REPLACE FUNCTION notify_agent_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('agent_events', json_build_object(
        'seq',       NEW.seq,
        'projectId', NEW.project_id,
        'type',      NEW.type
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
