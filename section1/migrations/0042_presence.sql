-- 0042: presence and participation (master prompt part 41, Session 4 lane P).
-- presence_records: one row per (scope, user, device); the API merges devices per user
-- and expires rows by expires_at (client heartbeats extend it). Expired rows are kept
-- up to 24 hours only to derive an approximate last-seen bucket, then purged.
-- team_presence_settings: per-team switch (full, limited, off), stale window and the
-- participant cap. No row means the defaults full, 90 seconds, 20 participants.
-- participant_prefs: per run and user mute, notification and presence sharing choices.
-- Retention: rows of runs nobody heartbeats again are not purged by the API. Run from a
-- scheduled job: DELETE FROM presence_records WHERE expires_at < now() - interval '1 day'
-- Rollback: DROP TABLE participant_prefs, team_presence_settings, presence_records
-- (product data only, audit_log and run_collab_events are untouched).
-- Portable SQL (PostgreSQL and SQLite for tests). Runner-safe: no dollar quotes, no
-- transaction control, no semicolons in comments.

CREATE TABLE IF NOT EXISTS presence_records (
    scope_kind VARCHAR(8) NOT NULL,
    scope_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    device_id VARCHAR(64) NOT NULL,
    state VARCHAR(8) NOT NULL DEFAULT 'online',
    last_seen TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scope_kind, scope_id, user_id, device_id),
    CONSTRAINT ck_presence_scope CHECK (scope_kind IN ('run', 'session')),
    CONSTRAINT ck_presence_state CHECK (state IN ('online', 'idle'))
);

CREATE INDEX IF NOT EXISTS ix_presence_records_team ON presence_records (team_id);
CREATE INDEX IF NOT EXISTS ix_presence_records_expires ON presence_records (expires_at);

CREATE TABLE IF NOT EXISTS team_presence_settings (
    team_id VARCHAR(36) PRIMARY KEY,
    mode VARCHAR(8) NOT NULL DEFAULT 'full',
    stale_seconds INTEGER NOT NULL DEFAULT 90,
    max_participants INTEGER NOT NULL DEFAULT 20,
    updated_by VARCHAR(36),
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_team_presence_mode CHECK (mode IN ('full', 'limited', 'off')),
    CONSTRAINT ck_team_presence_stale CHECK (stale_seconds BETWEEN 15 AND 3600),
    CONSTRAINT ck_team_presence_max CHECK (max_participants BETWEEN 1 AND 200)
);

CREATE TABLE IF NOT EXISTS participant_prefs (
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL,
    muted BOOLEAN NOT NULL DEFAULT FALSE,
    notify BOOLEAN NOT NULL DEFAULT TRUE,
    share_presence VARCHAR(8) NOT NULL DEFAULT 'team',
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, user_id),
    CONSTRAINT ck_participant_prefs_share CHECK (share_presence IN ('everyone', 'team', 'nobody'))
);
