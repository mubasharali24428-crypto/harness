-- Marathon 2.0 W16 slice 2 — group-mapped SSO.
--
-- Additive columns on team_sso_configs. Both default to "off": an empty claim
-- name means the mapping is not configured, and an empty map means no role can
-- be granted by a claim. Existing teams therefore keep the email-only behaviour
-- they had, bit for bit, without a data migration.
--
-- groups_claim — the ID-token claim carrying the subject's groups (Entra and
-- Auth0 tenants often use `roles` or a namespaced URI, hence configurable).
-- group_roles — JSON array of {"group": <exact string or fnmatch pattern>,
-- "role": admin|engineer|viewer|guest}. TEXT (not JSONB) for the same reason as
-- migration 0005: SQLite and Postgres then read it identically, and the value is
-- validated at write time by the settings API before it is stored.
--
-- Neither column is secret: group names and role names are configuration, and
-- they are projected to admins by GET /teams/{id}/sso/config.

ALTER TABLE team_sso_configs
    ADD COLUMN groups_claim VARCHAR(120) NOT NULL DEFAULT '';
ALTER TABLE team_sso_configs
    ADD COLUMN group_roles TEXT NOT NULL DEFAULT '[]';
