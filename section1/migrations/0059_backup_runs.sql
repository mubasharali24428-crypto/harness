-- 0059 (Part 28, session 12): successful base backups, recorded by deploy/runners/backup.sh after its
-- storage post-check passes. The orchestrator exposes the newest as
-- harness_backup_last_success_timestamp_seconds, which HarnessBackupStale alerts on.
CREATE TABLE IF NOT EXISTS backup_runs (
    id BIGSERIAL PRIMARY KEY,
    kind VARCHAR(16) NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_backup_runs_kind_finished ON backup_runs (kind, finished_at);
