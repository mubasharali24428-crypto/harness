-- 0063: artifact share links (W9). A share is a CAPABILITY: the URL is the authority,
-- so the row stores only sha256(token) and the raw token is shown once, at mint time.
-- Revocation is a write to this row (revoked_at), which is the reason it is a registry
-- and not a signed string: a signature cannot be withdrawn before its expiry.
-- expires_at is required (no share exists without one), uses/last_used_at record what
-- the link did, and nothing here can widen access beyond ONE artifact's bytes.
-- Deleting the artifact or quarantining it stops the link without touching this row.
-- JSON text columns, no dollar quotes, no now() default (the 0049 convention).
-- Rollback: DROP TABLE artifact_shares (product data only, audit_log untouched).

CREATE TABLE IF NOT EXISTS artifact_shares (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    artifact_id VARCHAR(36) NOT NULL,
    token_sha VARCHAR(64) NOT NULL,
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by VARCHAR(64),
    last_used_at TIMESTAMPTZ,
    uses INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT artifact_shares_uses_chk CHECK (uses >= 0)
);

-- One row per token: a lookup by hash is a single index probe.
CREATE UNIQUE INDEX IF NOT EXISTS artifact_shares_token ON artifact_shares (token_sha);
CREATE INDEX IF NOT EXISTS artifact_shares_run ON artifact_shares (team_id, run_id, created_at);
CREATE INDEX IF NOT EXISTS artifact_shares_artifact ON artifact_shares (artifact_id);
