-- 0046: notifications and user control (master prompt part 47, Session 5 lane N).
-- notification_inbox: one row per notification (or per burst of the same kind in the same
-- scope, see count and group_key). It stores ids and a closed summary_code only, never
-- comment, session or plan content. state is the user's own handling (unread, read, snoozed,
-- dismissed). Rows are never deleted by the product, dismiss and mute keep history.
-- delivery is a JSON text with the visible per-channel state. email_state, email_due_at and
-- email_attempts are the machine columns the delivery worker scans.
-- notification_preferences: one global row per user (scope_id star) plus optional workspace
-- and session rows. Email is OFF unless the user opts in. events is a JSON text per kind.
-- notification_mutes: a mute is ended (ended_at), never deleted, so history survives.
-- notification_dedupe: one row per (user, dedupe_key), the primary key makes a repeat a no-op.
-- notification_unsub_tokens: single-use email unsubscribe tokens, stored only as a hash.
-- Portable SQL (PostgreSQL and SQLite for tests): JSON is stored as TEXT.
-- Rollback: DROP TABLE notification_unsub_tokens, notification_dedupe, notification_mutes,
-- notification_preferences, notification_inbox (product data only, audit_log untouched).
-- Runner-safe: no dollar quoting, no transaction control, no semicolons in comments.

CREATE TABLE IF NOT EXISTS notification_inbox (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    kind VARCHAR(20) NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    source_id VARCHAR(80) NOT NULL,
    run_id VARCHAR(36),
    session_id VARCHAR(36),
    workspace_id VARCHAR(36),
    urgent BOOLEAN NOT NULL DEFAULT FALSE,
    dedupe_key VARCHAR(120),
    group_key VARCHAR(160) NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    summary_code VARCHAR(40) NOT NULL,
    state VARCHAR(10) NOT NULL DEFAULT 'unread',
    snoozed_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    read_at TIMESTAMPTZ,
    delivery TEXT NOT NULL DEFAULT '{}',
    email_state VARCHAR(10) NOT NULL DEFAULT 'none',
    email_due_at TIMESTAMPTZ,
    email_attempts INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT ck_notification_inbox_state
        CHECK (state IN ('unread','read','snoozed','dismissed')),
    CONSTRAINT ck_notification_inbox_email_state
        CHECK (email_state IN ('none','pending','digest','sent','failed','skipped')),
    CONSTRAINT ck_notification_inbox_count CHECK (count >= 1)
);
CREATE INDEX IF NOT EXISTS idx_notification_inbox_user
    ON notification_inbox (team_id, user_id, state, updated_at);
CREATE INDEX IF NOT EXISTS idx_notification_inbox_group
    ON notification_inbox (team_id, user_id, group_key, state);
CREATE INDEX IF NOT EXISTS idx_notification_inbox_email
    ON notification_inbox (email_state, email_due_at);

CREATE TABLE IF NOT EXISTS notification_preferences (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    scope_kind VARCHAR(12) NOT NULL DEFAULT 'global',
    scope_id VARCHAR(36) NOT NULL DEFAULT '*',
    inapp VARCHAR(3),
    email VARCHAR(10),
    events TEXT NOT NULL DEFAULT '{}',
    timezone VARCHAR(64),
    quiet_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    quiet_start VARCHAR(5),
    quiet_end VARCHAR(5),
    digest_period VARCHAR(8) NOT NULL DEFAULT 'daily',
    digest_hour INTEGER NOT NULL DEFAULT 8,
    last_digest_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_notification_preferences_scope
        UNIQUE (team_id, user_id, scope_kind, scope_id),
    CONSTRAINT ck_notification_preferences_scope
        CHECK ((scope_kind = 'global' AND scope_id = '*')
               OR (scope_kind IN ('workspace','session') AND scope_id <> '*')),
    CONSTRAINT ck_notification_preferences_inapp CHECK (inapp IS NULL OR inapp IN ('on','off')),
    CONSTRAINT ck_notification_preferences_email
        CHECK (email IS NULL OR email IN ('off','urgent','immediate','digest')),
    CONSTRAINT ck_notification_preferences_digest CHECK (digest_period IN ('daily','weekly')),
    CONSTRAINT ck_notification_preferences_hour CHECK (digest_hour >= 0 AND digest_hour <= 23)
);

CREATE TABLE IF NOT EXISTS notification_mutes (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    scope_kind VARCHAR(12) NOT NULL,
    scope_id VARCHAR(80) NOT NULL,
    muted_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    CONSTRAINT ck_notification_mutes_scope
        CHECK (scope_kind IN ('workspace','session','run','kind'))
);
CREATE INDEX IF NOT EXISTS idx_notification_mutes_user
    ON notification_mutes (team_id, user_id, ended_at);
-- One live mute per person and scope (a retry is a no-op).
CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_mutes_live
    ON notification_mutes (team_id, user_id, scope_kind, scope_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS notification_dedupe (
    team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    dedupe_key VARCHAR(120) NOT NULL,
    inbox_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (team_id, user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_notification_dedupe_created ON notification_dedupe (created_at);

CREATE TABLE IF NOT EXISTS notification_unsub_tokens (
    token_hash VARCHAR(64) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_notification_unsub_tokens_user
    ON notification_unsub_tokens (team_id, user_id);
