-- 0045: collaborative discussion (part 43).
-- comments: team-scoped discussion attached to a run or a collaboration session
-- (scope_kind + scope_id, no foreign key because the scope table depends on the kind).
-- A deleted comment is a tombstone row, never a removed row. comment_revisions keeps
-- one row per version (version 1 is the original). comment_reactions is a closed set.
-- comment_mentions holds only people who could already see the scope. comment_promotions
-- links a comment to the guidance instruction an authorised user made from it (one per
-- comment). discussion_settings is per team: edit window, retention, audit sensitivity.
-- Portable SQL (PostgreSQL + SQLite for tests), JSON-free.
-- Rollback: DROP TABLE comment_promotions, comment_mentions, comment_reactions,
-- comment_revisions, comments, discussion_settings (product data only, audit_log untouched).
-- Runner-safe: no dollar quotes, no transaction control, no semicolons in comments,
-- and no now() default so the file also loads on SQLite for tests.

CREATE TABLE IF NOT EXISTS discussion_settings (
    team_id VARCHAR(36) PRIMARY KEY REFERENCES teams (id) ON DELETE CASCADE,
    edit_window_seconds INTEGER NOT NULL DEFAULT 900,
    retention_days INTEGER NOT NULL DEFAULT 365,
    audit_sensitive VARCHAR(8) NOT NULL DEFAULT 'auto',
    updated_by VARCHAR(36),
    updated_at TIMESTAMPTZ,
    CONSTRAINT discussion_settings_window_chk
        CHECK (edit_window_seconds >= 0 AND edit_window_seconds <= 86400),
    CONSTRAINT discussion_settings_retention_chk
        CHECK (retention_days >= 0 AND retention_days <= 3650),
    CONSTRAINT discussion_settings_sensitive_chk CHECK (audit_sensitive IN ('auto', 'always'))
);

CREATE TABLE IF NOT EXISTS comments (
    id VARCHAR(36) PRIMARY KEY,
    scope_kind VARCHAR(8) NOT NULL,
    scope_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    author_user_id VARCHAR(36) NOT NULL,
    author_name_snapshot VARCHAR(200) NOT NULL,
    parent_id VARCHAR(36),
    depth INTEGER NOT NULL DEFAULT 0,
    body TEXT NOT NULL,
    state VARCHAR(8) NOT NULL DEFAULT 'visible',
    ref_kind VARCHAR(16),
    ref_id VARCHAR(512),
    ref_pos INTEGER,
    request_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    edited_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    moderated_by VARCHAR(36),
    moderated_at TIMESTAMPTZ,
    moderation_reason VARCHAR(500),
    deleted_at TIMESTAMPTZ,
    deleted_by VARCHAR(36),
    purged_at TIMESTAMPTZ,
    CONSTRAINT comments_scope_chk CHECK (scope_kind IN ('run', 'session')),
    CONSTRAINT comments_state_chk CHECK (state IN ('visible', 'hidden', 'deleted')),
    CONSTRAINT comments_ref_chk CHECK (
        ref_kind IS NULL OR ref_kind IN ('plan', 'file', 'diff', 'approval', 'event')),
    CONSTRAINT comments_depth_chk CHECK (depth >= 0 AND depth <= 5)
);

CREATE INDEX IF NOT EXISTS comments_scope
    ON comments (scope_kind, scope_id, created_at, id);

CREATE INDEX IF NOT EXISTS comments_team
    ON comments (team_id, created_at);

CREATE INDEX IF NOT EXISTS comments_parent
    ON comments (parent_id);

-- A retried create with the same request id is one comment.
CREATE UNIQUE INDEX IF NOT EXISTS comments_request
    ON comments (scope_kind, scope_id, author_user_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS comment_revisions (
    comment_id VARCHAR(36) NOT NULL REFERENCES comments (id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    edited_by VARCHAR(36) NOT NULL,
    edited_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (comment_id, version)
);

CREATE TABLE IF NOT EXISTS comment_reactions (
    comment_id VARCHAR(36) NOT NULL REFERENCES comments (id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL,
    reaction VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (comment_id, user_id, reaction),
    CONSTRAINT comment_reactions_set_chk CHECK (reaction IN (
        'thumbs_up', 'thumbs_down', 'heart', 'eyes', 'celebrate', 'question', 'check'))
);

CREATE TABLE IF NOT EXISTS comment_mentions (
    comment_id VARCHAR(36) NOT NULL REFERENCES comments (id) ON DELETE CASCADE,
    mentioned_user_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (comment_id, mentioned_user_id)
);

CREATE INDEX IF NOT EXISTS comment_mentions_user
    ON comment_mentions (mentioned_user_id);

-- comment_id is the key, so a comment becomes guidance at most once.
CREATE TABLE IF NOT EXISTS comment_promotions (
    comment_id VARCHAR(36) PRIMARY KEY REFERENCES comments (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    promoted_by VARCHAR(36) NOT NULL,
    promoted_at TIMESTAMPTZ NOT NULL,
    comment_version INTEGER NOT NULL,
    event_seq BIGINT,
    instruction_id VARCHAR(36)
);
