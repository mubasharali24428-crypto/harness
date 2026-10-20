-- Mission 19 N3 — partitioned retention (pbc1 F4+F8, 10x-plan C9).
--
-- agent_events becomes a monthly RANGE-partitioned table on ts, so retention
-- turns from row-by-row DELETE churn (deploy/runners/events_retention.py,
-- Mission 12 G1) into a metadata operation: DETACH PARTITION + DROP TABLE.
-- Replay honesty is unchanged — clients already tolerate seq gaps at the
-- retention boundary (app closes 1013 on overflow; see 0018 header).
--
-- Mechanics, per repo conventions:
--   * Append-only chain law: 0001 is never edited. The frozen shape lives on
--     inside the swapped tables exactly as 0023 rebuilt it.
--   * Name law: the table MUST exist under the canonical name `agent_events`
--     when this file finishes, whatever the starting state — including bare
--     Base.metadata.create_all Postgres databases (nightly probes, fixtures)
--     that never saw a numbered migration and carry no schema_migrations row.
--     Hence the DO block materializes/renames/swaps only when relkind says
--     'r' (ordinary table) and no-ops on 'p' (already partitioned: re-run
--     safety).
--   * Identity continuity: the swap renames the TABLE, not its sequence
--     (renamed tables keep original sequence names — 0023's own swap left
--     `agent_events_d1_new_seq_seq` in place). A freshly-created partitioned
--     parent mints a BRAND-NEW sequence starting at 1 and would collide with
--     surviving seqs on the first insert, so the parent is created with a
--     plain bigint, rewired to the PRE-EXISTING sequence discovered via
--     pg_get_serial_sequence BEFORE the rename, and setval'd past MAX(seq)
--     only AFTER the history attaches (an empty parent reports MAX=0).
--   * Triggers do NOT follow a rename and do NOT clone over same-named
--     triggers on existing partitions, so 0003's NOTIFY trigger is dropped
--     from the legacy twin and recreated on the parent (PG clones it onto
--     every partition, current and future).
--   * SQLite compat: section1 suites build schema via Base.metadata.create_all
--     and NEVER run numbered migrations (migrate() is Postgres-only — 0023's
--     DO bodies already rely on that); the SQLite test path for partitioning
--     is documented on deploy/runners/events_retention.py.
--   * Runner-compat: dollar quotes are plain $$ (split_statements toggles on
--     literal $$, proven by 0003/0023), no BEGIN/COMMIT (the runner wraps the
--     file in one transaction).
--
-- Amended 2026-10-20 (Session 11 upgrade rehearsal, section1/tests/test_upgrade_rehearsal_pg.py),
-- before any database was known to have run it: (1) the legacy bound was a fixed
-- 2026-09-01, so a database holding later events could not ATTACH; it now moves to the
-- next month start after the newest row. (2) setval(.., MAX(seq), false) handed the
-- first post-upgrade event the seq of the last old one; it is MAX+1. A database that
-- already ran the old text keeps its one repeated seq: find it with
-- SELECT seq FROM agent_events GROUP BY seq HAVING count(*) > 1.
--
-- Transitional layout: the pre-existing table (with all history) attaches as
-- partition `agent_events_legacy` covering (-infinity, 2026-09-01). Deploy/
-- runners/events_retention.py pre-creates forward month partitions on every
-- sweep, drains boundary-month stragglers with bounded DELETEs, then retires
-- whole partitions via DETACH+DROP once their upper bound passes the cutoff.
--
-- F8 half: notification_outbox gains its first pruning path (status+created_at
-- composite serving both the drain poll and the sent/failed sweep); audit_log
-- keeps its compliance-grade append-only nature but gains a (ts, seq)
-- covering index so the longer-window prune (AUDIT_RETENTION_DAYS, default
-- 365 vs 30 for events) stays an index range walk. Pruning lives in the
-- retention runner.

DO $$
DECLARE
    v_kind   text;
    v_seq    text;
    v_month  timestamp;  -- UTC first-of-month: the first month the legacy table does not own
    v_lo     timestamp;
    v_name   text;
BEGIN
    SELECT c.relkind INTO v_kind
    FROM pg_class c
    WHERE c.oid = 'agent_events'::regclass;

    IF v_kind IS NULL THEN
        -- Bare create_all-style database without the numbered chain: stand up
        -- the frozen shape first so the swap below always has something to
        -- convert. Mirrors 0001 verbatim minus FKs (parents may not exist on
        -- such databases; 0014's discipline: standalone statements).
        CREATE TABLE agent_events (
            seq BIGSERIAL NOT NULL,
            project_id VARCHAR(36) NOT NULL,
            run_id VARCHAR(36),
            type VARCHAR(60) NOT NULL,
            payload JSONB NOT NULL,
            ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            PRIMARY KEY (seq)
        );
        v_kind := 'r';
    END IF;

    IF v_kind = 'r' THEN
        -- Discover the identity sequence BEFORE stepping the old table aside.
        v_seq := pg_get_serial_sequence('agent_events', 'seq');

        ALTER TABLE agent_events RENAME TO agent_events_legacy;

        -- Partitioned parent under the canonical name, frozen shape verbatim
        -- (columns, defaults, FKs and their ondelete law from 0023). Two
        -- deliberate deviations, both forced by PG partition law and both
        -- caught live by migrate_selftest on the first runs:
        --   1. seq is plain bigint rewired to the ORIGINAL sequence below
        --      (a fresh parent mints a new sequence starting at 1).
        --   2. NO parent-level PRIMARY KEY: every unique constraint on a
        --     partitioned table must include the partition key (so (seq)
        --     alone is illegal), and ATTACH refuses a partition that would
        --     end up with TWO PKs (legacy already owns one). Uniqueness law
        --     therefore stays where it always lived — PER PARTITION: each
        --     child carries PRIMARY KEY (seq) locally (stronger than the
        --     composite (seq, ts) compromise: duplicates stay impossible
        --     inside every month, which is where writers race). Cross-month
        --     duplication is excluded by the single nextval() writer path,
        --     exactly as before partitioning.
        CREATE TABLE agent_events (
            seq bigint NOT NULL,
            project_id VARCHAR(36) NOT NULL,
            run_id VARCHAR(36),
            type VARCHAR(60) NOT NULL,
            payload JSONB NOT NULL,
            ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT fk_agent_events_project_id FOREIGN KEY(project_id)
                REFERENCES projects (id) ON DELETE CASCADE,
            CONSTRAINT fk_agent_events_run_id FOREIGN KEY(run_id)
                REFERENCES agent_runs (id) ON DELETE SET NULL
        ) PARTITION BY RANGE (ts);

        IF v_seq IS NOT NULL THEN
            EXECUTE 'ALTER TABLE agent_events ALTER COLUMN seq SET DEFAULT '
                    || 'nextval(' || quote_literal(v_seq) || ')';
            EXECUTE 'ALTER SEQUENCE ' || v_seq || ' OWNED BY agent_events.seq';
        END IF;

        -- All pre-existing history attaches as the first partition. Metadata
        -- operation; zero row copies (unlike 0023's INSERT..SELECT rebuild).
        -- No parent-level unique constraint exists, so legacy's own PK(seq)
        -- survives untouched; PG clones the two outbound FKs onto it (or
        -- adopts the equivalents 0023 already named identically).
        -- The upper bound is 2026-09-01, or the first month start after the
        -- newest surviving row when the database already holds later events
        -- (ATTACH refuses a bound a row violates). Empty or older history
        -- lands exactly on 2026-09-01, so fresh installs are unchanged.
        SELECT GREATEST(timestamp '2026-09-01',
                        date_trunc('month', MAX(ts) AT TIME ZONE 'UTC') + interval '1 month')
          INTO v_month FROM agent_events_legacy;
        EXECUTE format('ALTER TABLE agent_events ATTACH PARTITION agent_events_legacy '
                       || 'FOR VALUES FROM (MINVALUE) TO (%L)', v_month AT TIME ZONE 'UTC');

        -- First forward months; the retention runner rolls these forward on
        -- every sweep (deploy/runners/events_retention.py replicates this
        -- whole block, PK included). v_month is the first month legacy does
        -- NOT own (2026_09 and 2026_10 on a fresh install).
        FOR i IN 0..1 LOOP
            v_lo := v_month + make_interval(months => i);
            v_name := 'agent_events_' || to_char(v_lo, 'YYYY_MM');
            EXECUTE format('CREATE TABLE %I PARTITION OF agent_events '
                           || 'FOR VALUES FROM (%L) TO (%L)', v_name,
                           v_lo AT TIME ZONE 'UTC',
                           (v_lo + interval '1 month') AT TIME ZONE 'UTC');
            EXECUTE format('ALTER TABLE %I ADD PRIMARY KEY (seq)', v_name);
        END LOOP;

        -- Identity continuity: NOW the parent sees every surviving seq
        -- (legacy attached above), so headroom math is honest. setval(.., n,
        -- false) makes n the NEXT value, so n is MAX+1 (as in 0023); fresh
        -- installs get MAX(seq)=NULL -> COALESCE 0 -> 1: classic start-at-one.
        IF v_seq IS NOT NULL THEN
            EXECUTE 'SELECT setval(' || quote_literal(v_seq) || ', '
                    || '(SELECT COALESCE(MAX(seq), 0) + 1 FROM agent_events)'
                    || ', false)';
        END IF;

        -- Secondary indexes move to the PARENT so every current and future
        -- partition inherits them. Old same-named indexes sit on the renamed
        -- legacy partition and would collide, so drop first; recreating on
        -- the parent re-indexes legacy's rows too (ATTACH put it under the
        -- parent's index regime). The 0012 pair and 0018 ts probe join 0002;
        -- the 0026 covering index (runs-index MIN(ts) markers, M16-X1) is
        -- recreated last — dropping it was the one perf regression this swap
        -- nearly shipped.
        DROP INDEX IF EXISTS idx_agent_events_project_seq;
        DROP INDEX IF EXISTS idx_agent_events_run_id_type;
        DROP INDEX IF EXISTS idx_agent_events_ts;
        DROP INDEX IF EXISTS idx_agent_events_run_type_ts_covering;
        CREATE INDEX idx_agent_events_project_seq ON agent_events (project_id, seq);
        CREATE INDEX idx_agent_events_run_id_type ON agent_events (run_id, type);
        CREATE INDEX idx_agent_events_ts ON agent_events (ts);
        CREATE INDEX idx_agent_events_run_type_ts_covering ON agent_events (run_id, type, ts);

        -- 0003's LISTEN/NOTIFY trigger died with the rename (triggers bind to
        -- the physical table); Section 1 owns delivery — reattach to the new
        -- parent. Drop any same-named trigger on the legacy twin first: PG
        -- refuses to clone a parent trigger onto a partition that already
        -- carries one by that name.
        DROP TRIGGER IF EXISTS trg_notify_agent_events ON agent_events_legacy;
        CREATE TRIGGER trg_notify_agent_events
        AFTER INSERT ON agent_events
        FOR EACH ROW EXECUTE FUNCTION notify_agent_event();
    END IF;
    -- v_kind = 'p': already partitioned by a previous pass — nothing to do.
END
$$;

-- F8 pruning-path indexes. outbox: the drain poll wants (status) ordered by
-- time; the retention sweep wants (status, created_at) — one composite serves
-- both. audit_log: (ts, seq) lets the long-window prune walk the index for
-- both the cutoff probe and the seq IN() harvest (covering: zero heap visits
-- per batch).
CREATE INDEX IF NOT EXISTS idx_notification_outbox_status_created
    ON notification_outbox (status, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts_seq ON audit_log (ts, seq);
