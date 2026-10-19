-- 0036: tamper-evident audit_log (master prompt part 11.004).
-- Every audit row is sealed into a SHA-256 hash chain: row_hash covers the
-- previous row's hash plus the row's own content, so editing, deleting,
-- reordering or inserting a row behind the app's back breaks verification.
-- The chain is built by the database, not by callers: audit rows are written
-- by raw SQL in section4 and by the ORM in section1, and both are covered.
--
-- Design:
--   * A deferred constraint trigger seals each new row AT COMMIT, so the chain
--     head lock is taken last and never held while a transaction waits on
--     another lock (no lock-order deadlocks with collaboration rows).
--   * chain_pos is the commit order of the chain. Verification walks chain_pos,
--     not seq, because seq is assigned before commit.
--   * audit_chain_head keeps the tail (last_pos, last_hash) so deleting the
--     newest rows is detected too.
--   * UPDATE of a sealed row is refused by trigger. DELETE stays allowed for the
--     retention sweep. Verification treats a pruned prefix as anchored at its
--     first surviving row (the earlier part cannot be re-verified once pruned).
--   * Existing rows are sealed here in seq order.
-- Verify: section1/scripts/verify_audit_chain.py
-- Rollback: DROP TRIGGER audit_log_seal ON audit_log, DROP TRIGGER
-- audit_log_no_update ON audit_log, DROP FUNCTION audit_chain_seal and
-- audit_log_guard_update and audit_row_hash, DROP TABLE audit_chain_head,
-- ALTER TABLE audit_log DROP COLUMN chain_pos, prev_hash, row_hash.
-- Runner-safe: dollar quotes are plain double dollar, no transaction control,
-- no semicolons or double dashes inside string literals.

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS chain_pos BIGINT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash VARCHAR(64);
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS row_hash VARCHAR(64);

CREATE TABLE IF NOT EXISTS audit_chain_head (
    id SMALLINT PRIMARY KEY,
    last_pos BIGINT NOT NULL,
    last_hash VARCHAR(64) NOT NULL,
    CONSTRAINT audit_chain_head_singleton CHECK (id = 1)
);

INSERT INTO audit_chain_head (id, last_pos, last_hash)
VALUES (1, 0, repeat('0', 64))
ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION audit_row_hash(
    p_prev text, p_seq bigint, p_pos bigint, p_ts timestamptz, p_actor text,
    p_kind text, p_action text, p_ttype text, p_tid text, p_detail jsonb
) RETURNS text AS $$
    SELECT encode(
        sha256(convert_to(
            concat_ws(E'\x1f',
                p_prev,
                p_seq::text,
                p_pos::text,
                floor(extract(epoch FROM p_ts) * 1000000)::bigint::text,
                coalesce(p_actor, ''),
                p_kind,
                p_action,
                p_ttype,
                p_tid,
                p_detail::text),
            'UTF8')),
        'hex')
$$ LANGUAGE sql IMMUTABLE;

DO $$
DECLARE
    r RECORD;
    prev text := repeat('0', 64);
    pos bigint := 0;
    h text;
BEGIN
    FOR r IN SELECT * FROM audit_log WHERE row_hash IS NULL ORDER BY seq LOOP
        pos := pos + 1;
        h := audit_row_hash(prev, r.seq, pos, r.ts, r.actor_member_id, r.actor_kind,
                            r.action, r.target_type, r.target_id, r.detail);
        UPDATE audit_log SET chain_pos = pos, prev_hash = prev, row_hash = h
         WHERE seq = r.seq;
        prev := h;
    END LOOP;
    IF pos > 0 THEN
        UPDATE audit_chain_head SET last_pos = pos, last_hash = prev WHERE id = 1;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_log_chain_pos
    ON audit_log (chain_pos) WHERE chain_pos IS NOT NULL;

CREATE OR REPLACE FUNCTION audit_chain_seal() RETURNS trigger AS $$
DECLARE
    h audit_chain_head%ROWTYPE;
    r audit_log%ROWTYPE;
    digest text;
BEGIN
    SELECT * INTO r FROM audit_log WHERE seq = NEW.seq;
    IF NOT FOUND OR r.row_hash IS NOT NULL THEN
        RETURN NULL;
    END IF;
    UPDATE audit_chain_head SET last_pos = last_pos + 1 WHERE id = 1 RETURNING * INTO h;
    digest := audit_row_hash(h.last_hash, r.seq, h.last_pos, r.ts, r.actor_member_id,
                             r.actor_kind, r.action, r.target_type, r.target_id, r.detail);
    UPDATE audit_log SET chain_pos = h.last_pos, prev_hash = h.last_hash, row_hash = digest
     WHERE seq = r.seq;
    UPDATE audit_chain_head SET last_hash = digest WHERE id = 1;
    RETURN NULL;
END
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_log_guard_update() RETURNS trigger AS $$
BEGIN
    IF OLD.row_hash IS NULL AND NEW.row_hash IS NOT NULL
       AND (OLD.seq, OLD.actor_member_id, OLD.actor_kind, OLD.action, OLD.target_type,
            OLD.target_id, OLD.detail, OLD.ts)
           IS NOT DISTINCT FROM
           (NEW.seq, NEW.actor_member_id, NEW.actor_kind, NEW.action, NEW.target_type,
            NEW.target_id, NEW.detail, NEW.ts)
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only (seq %)', OLD.seq;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_guard_update();

DROP TRIGGER IF EXISTS audit_log_seal ON audit_log;
CREATE CONSTRAINT TRIGGER audit_log_seal
    AFTER INSERT ON audit_log
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION audit_chain_seal();
