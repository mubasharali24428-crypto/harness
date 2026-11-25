-- Sprint 5 gap-close Task 42 (owner decision D2): team memberships are SOFT-removed.
-- audit_log.actor_member_id -> team_members.id is ON DELETE RESTRICT (0023) and audit_log is
-- append-only, so a seat that ever acted can never be deleted; the old hard DELETE answered 500
-- on Postgres. Removal now keeps the row as the audit FK target and turns it into a dead seat:
--   removed_at       when it stopped being a seat (NULL = active)
--   removed_user_id  who held it, for forensics; user_id itself is set to NULL so every
--                    (team_id, user_id) lookup in every service (sessions, principal, SSO,
--                    SCIM, notifications, section4 authz) misses it without a code change.
-- Erasure (harness_identity/erasure.py) anonymises removed seats through removed_user_id too.
ALTER TABLE team_members ADD COLUMN removed_at TIMESTAMPTZ;
ALTER TABLE team_members ADD COLUMN removed_user_id VARCHAR(36);
ALTER TABLE team_members ADD CONSTRAINT fk_team_members_removed_user_id
    FOREIGN KEY (removed_user_id) REFERENCES users (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_team_members_removed_user_id ON team_members (removed_user_id);
