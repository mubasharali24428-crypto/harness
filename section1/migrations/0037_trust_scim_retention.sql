-- A1-S4 — supply-chain trust, SCIM provisioning tokens, per-team retention policy.
--
-- package_trust_issuers: team-scoped Ed25519 public keys allowed to sign agent/plugin
-- packages. Status revoked is terminal (rows are never deleted: history stays auditable).
-- package_revocations: (name, version) and/or content sha256 denied even with a valid signature.
-- scim_tokens: bearer credentials for the IdP's SCIM client; only a SHA-256 hash is stored.
-- retention_policies: per-team (project_id NULL) or per-project retention for a resource;
-- legal_hold blocks deletion and shortening until released.
CREATE TABLE package_trust_issuers (
	id VARCHAR(36) PRIMARY KEY,
	team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
	key_id VARCHAR(120) NOT NULL,
	issuer VARCHAR(200) NOT NULL,
	public_key VARCHAR(64) NOT NULL,
	status VARCHAR(8) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
	created_by VARCHAR(36) NULL REFERENCES users (id) ON DELETE SET NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE NULL,
	UNIQUE (team_id, key_id)
);

CREATE TABLE package_revocations (
	id VARCHAR(36) PRIMARY KEY,
	team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
	name VARCHAR(200) NULL,
	version VARCHAR(100) NULL,
	sha256 VARCHAR(64) NULL,
	reason VARCHAR(500) NOT NULL DEFAULT '',
	created_by VARCHAR(36) NULL REFERENCES users (id) ON DELETE SET NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CHECK (sha256 IS NOT NULL OR (name IS NOT NULL AND version IS NOT NULL))
);
CREATE INDEX ix_package_revocations_team ON package_revocations (team_id);

CREATE TABLE scim_tokens (
	id VARCHAR(36) PRIMARY KEY,
	team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
	token_hash VARCHAR(64) NOT NULL UNIQUE,
	label VARCHAR(120) NOT NULL DEFAULT '',
	created_by VARCHAR(36) NULL REFERENCES users (id) ON DELETE SET NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE NULL
);
CREATE INDEX ix_scim_tokens_team ON scim_tokens (team_id);

CREATE TABLE retention_policies (
	id VARCHAR(36) PRIMARY KEY,
	team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
	project_id VARCHAR(36) NULL REFERENCES projects (id) ON DELETE CASCADE,
	resource VARCHAR(40) NOT NULL CHECK (resource IN ('agent_events')),
	retain_days INTEGER NOT NULL CHECK (retain_days BETWEEN 7 AND 3650),
	legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
	hold_reason VARCHAR(500) NOT NULL DEFAULT '',
	updated_by VARCHAR(36) NULL REFERENCES users (id) ON DELETE SET NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
);
CREATE UNIQUE INDEX uq_retention_project ON retention_policies (team_id, project_id, resource)
	WHERE project_id IS NOT NULL;
CREATE UNIQUE INDEX uq_retention_team ON retention_policies (team_id, resource)
	WHERE project_id IS NULL;

-- SCIM deprovisioning keeps the membership row (audit_log.actor_member_id is ON DELETE
-- RESTRICT, so members with history cannot be deleted) and deactivates it instead.
-- Reactivation restores the prior role; get_principal rejects deactivated members.
ALTER TABLE team_members ADD COLUMN deactivated_at TIMESTAMP WITH TIME ZONE NULL;
