-- A1-S2 — personal BYO-key profiles, consent-based sharing, per-share bounded spend.
--
-- api_keys.owner_user_id: NULL = team key (existing behavior, unchanged);
-- non-NULL = personal key owned by that user. Personal keys are excluded from
-- the team key list/revoke surface and are usable by others ONLY through an
-- ACTIVE key_shares row the owner offered and the recipient accepted.
--
-- key_shares: consent state machine pending -> active | declined | revoked.
-- committed_usd = reserved/unsettled holds + settled actuals (the payer's
-- exposure); spend_cap_usd NULL = owner set no cap (explicit, shown to both).
--
-- key_share_spend: per-reservation ledger; UNIQUE(share_id, idempotency_key)
-- makes retries exactly-once. status 'unsettled' = provider usage unknown;
-- the reservation keeps holding its amount (never fabricated as zero).
ALTER TABLE api_keys ADD COLUMN owner_user_id VARCHAR(36) NULL REFERENCES users (id) ON DELETE CASCADE;
CREATE INDEX ix_api_keys_owner_user_id ON api_keys (owner_user_id);

CREATE TABLE key_shares (
	id VARCHAR(36) PRIMARY KEY,
	api_key_id VARCHAR(36) NOT NULL REFERENCES api_keys (id) ON DELETE CASCADE,
	owner_user_id VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
	recipient_user_id VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
	team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
	status VARCHAR(12) NOT NULL DEFAULT 'pending'
		CHECK (status IN ('pending', 'active', 'declined', 'revoked')),
	spend_cap_usd NUMERIC(12, 6) NULL CHECK (spend_cap_usd IS NULL OR spend_cap_usd >= 0),
	committed_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
	expires_at TIMESTAMP WITH TIME ZONE NULL,
	consented_at TIMESTAMP WITH TIME ZONE NULL,
	revoked_at TIMESTAMP WITH TIME ZONE NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
);
CREATE INDEX ix_key_shares_recipient ON key_shares (recipient_user_id);
CREATE INDEX ix_key_shares_key ON key_shares (api_key_id);
CREATE UNIQUE INDEX uq_key_shares_live ON key_shares (api_key_id, recipient_user_id)
	WHERE status IN ('pending', 'active');

CREATE TABLE key_share_spend (
	id VARCHAR(36) PRIMARY KEY,
	share_id VARCHAR(36) NOT NULL REFERENCES key_shares (id) ON DELETE CASCADE,
	idempotency_key VARCHAR(128) NOT NULL,
	reserved_usd NUMERIC(12, 6) NOT NULL CHECK (reserved_usd >= 0),
	actual_usd NUMERIC(12, 6) NULL,
	status VARCHAR(12) NOT NULL DEFAULT 'reserved'
		CHECK (status IN ('reserved', 'settled', 'unsettled', 'released')),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	settled_at TIMESTAMP WITH TIME ZONE NULL,
	UNIQUE (share_id, idempotency_key)
);
