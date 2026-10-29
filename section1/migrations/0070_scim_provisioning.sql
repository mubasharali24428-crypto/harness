-- 0070: SCIM 2.0 subset (W16 slice 3) — a directory credential and the seat link
-- it maintains.
--
-- Semantics:
-- - scim_tokens is a MACHINE credential, never a user session. Only the SHA-256
--   of the bearer token is stored; the raw value exists once, in the mint
--   response. Revocation is a row update (revoked_at) and is dead on the next
--   request: no grace window, matching team_runners (0068).
-- - A team may hold at most MAX_TOKENS_PER_TEAM live tokens; that bound is
--   enforced by the API, not by a constraint, because "live" is a predicate over
--   revoked_at rather than a column.
-- - scim_user_links stores the DIRECTORY's id for a person (SCIM externalId),
--   per team. It is written only by a SCIM mutation, so an IdP that knows the
--   app user by its own id can find and update that seat. It is a sidecar: every
--   read resolves through team_members first, so a seat that no longer exists is
--   never served from a stale link, and the FK means deleting the human removes
--   the link.
-- - No group schema, no POST /Users, no password or MFA field: SCIM here reads
--   seats and narrows them. Provisioning stays invite-only.
-- Portable SQL, no dollar quotes, no transaction control, no semicolons in comments.
-- Rollback: DROP TABLE scim_user_links, scim_tokens (product data only, audit_log
-- untouched).

CREATE TABLE IF NOT EXISTS scim_tokens (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    label VARCHAR(120) NOT NULL,
    token_sha256 VARCHAR(64) NOT NULL,
    token_hint VARCHAR(8) NOT NULL,
    created_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    revoked_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT scim_tokens_label_chk CHECK (CHAR_LENGTH(label) BETWEEN 1 AND 120),
    UNIQUE (token_sha256)
);

CREATE INDEX IF NOT EXISTS scim_tokens_team ON scim_tokens (team_id, revoked_at);

CREATE TABLE IF NOT EXISTS scim_user_links (
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    external_id VARCHAR(255) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (team_id, user_id),
    CONSTRAINT scim_links_external_chk UNIQUE (team_id, external_id),
    CONSTRAINT scim_links_ext_len_chk CHECK (CHAR_LENGTH(external_id) BETWEEN 1 AND 255)
);
