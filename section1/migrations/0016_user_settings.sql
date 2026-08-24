-- M12-W2.2 lane G7 (raw-13 #14): per-user settings KV.
-- Generic (user_id, key) -> value store so preferences land without a new
-- migration each time. Known key: notify_email ("true"/"false", default
-- true when the row is absent — enforced in notifier.get_notify_email_pref,
-- not by the schema). Purely additive: no FKs beyond users, no backfills.
CREATE TABLE user_settings (
    user_id VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    key VARCHAR(80) NOT NULL,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    PRIMARY KEY (user_id, key)
);
