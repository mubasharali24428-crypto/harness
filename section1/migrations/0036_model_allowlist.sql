-- A1-S3 — organization model allowlist with approval workflow.
-- One row per (team, model). status: pending (requested) -> approved | denied;
-- approved -> revoked; denied/revoked -> pending (re-request). The team's
-- routing/fallback/cache policy lives in team_settings key 'model_policy'
-- (migration 0024 KV), validated by the policy API before it is stored.
CREATE TABLE model_allowlist_entries (
	id VARCHAR(36) PRIMARY KEY,
	team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
	model VARCHAR(120) NOT NULL,
	status VARCHAR(10) NOT NULL DEFAULT 'pending'
		CHECK (status IN ('pending', 'approved', 'denied', 'revoked')),
	note VARCHAR(500) NOT NULL DEFAULT '',
	requested_by VARCHAR(36) NULL REFERENCES users (id) ON DELETE SET NULL,
	decided_by VARCHAR(36) NULL REFERENCES users (id) ON DELETE SET NULL,
	decided_at TIMESTAMP WITH TIME ZONE NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	UNIQUE (team_id, model)
);
