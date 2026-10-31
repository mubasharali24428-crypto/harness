-- 0072: durable context-index admission (M2-W19). One row per project that a
-- context-index job currently owns.
--
-- Why: Section 11's index admission was an in-process set
-- (``IndexService._active_projects``), so two replicas of the context service
-- — which a real multi-replica deployment runs — could both start indexing the
-- SAME project at once: double embedding spend and a torn index. A reservation
-- that lives in the shared database makes the exclusion span processes.
--
-- Semantics:
-- - ``lease_expires_at`` / ``updated_at`` are UTC ISO-8601 TEXT, never a native
--   column. The whole codebase compares these lexicographically (ISO-8601 sorts
--   as time), and mixing adapter-formatted datetimes with ``.isoformat()`` rows
--   is the trap that bit ephemeral state in Wave 7. One format everywhere.
-- - No foreign key to projects: a reservation may be minted before the first
--   chunk row exists and lingers after a project is deleted mid-index; a stale
--   lease is reaped by expiry, not by a cascade.
-- - A live lease is only ever replaced by an EXPIRED one, atomically, at acquire
--   time; a crashed replica's reservation is reclaimed once its lease lapses, so
--   admission can never deadlock on a dead owner.
--
-- Rollback: DROP TABLE project_index_reservations (admission control only, no
-- product data).

CREATE TABLE IF NOT EXISTS project_index_reservations (
    project_id      VARCHAR(36) PRIMARY KEY,
    job_id          VARCHAR(36) NOT NULL,
    worker_id       VARCHAR(64),
    lease_expires_at TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
