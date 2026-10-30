-- 0071: SIEM sinks (W16 slice 4) — an outbound copy of the audit trail a customer
-- can point at their own collector.
--
-- Semantics:
-- - One row per collector. `secret_encrypted` is a Fernet token under the salted
--   KDF (registry: crypto.ENCRYPTED_COLUMNS), and the raw HMAC key is never
--   returned after it is written — `secret_hint` is its last four characters, so
--   an admin can tell two collectors apart.
-- - `cursor_seq` is the last audit seq handed to the collector. It is deliberately
--   NOT a foreign key: retention prunes old rows, and a cursor pointing at a
--   pruned seq is the normal state of a months-old sink, not corruption. The
--   cursor moves only on a 2xx, so a crash between send and commit replays that
--   batch instead of skipping it (at-least-once; `Idempotency-Key` carries the
--   range so a collector can dedupe).
-- - `state` is 'active' or 'blocked' plus a transient 'sending' claim, and
--   `attempts`/`next_attempt_at` implement the backoff. A collector that refused
--   MAX_ATTEMPTS times goes 'blocked' and stays there until an admin PATCHes it:
--   retrying forever hides an outage, and dropping rows would lose evidence.
-- - A sink belongs to one team and its rows are read through that team's scope
--   rule, so a collector never sees another team's security events.
-- - created_by is RESTRICT, not SET NULL: an endpoint that receives this team's
--   audit trail must not outlive the account that authorized it by accident.
-- Portable SQL, no dollar quotes, no transaction control, no semicolons in comments.
-- Rollback: DROP TABLE siem_sinks (product configuration only; audit_log itself
-- is untouched and remains the system of record).

CREATE TABLE IF NOT EXISTS siem_sinks (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    label VARCHAR(120) NOT NULL,
    collector_url TEXT NOT NULL,
    secret_encrypted TEXT NOT NULL,
    secret_hint VARCHAR(8) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    state VARCHAR(12) NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    cursor_seq INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ,
    last_status_code INTEGER,
    last_error VARCHAR(40),
    delivered_rows INTEGER NOT NULL DEFAULT 0,
    last_batch_at TIMESTAMPTZ,
    created_by VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT siem_sinks_label_chk CHECK (CHAR_LENGTH(label) BETWEEN 1 AND 120),
    CONSTRAINT siem_sinks_state_chk CHECK (state IN ('active', 'blocked', 'sending'))
);

CREATE INDEX IF NOT EXISTS siem_sinks_due ON siem_sinks (state, next_attempt_at);
CREATE INDEX IF NOT EXISTS siem_sinks_team ON siem_sinks (team_id, state);
