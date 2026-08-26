-- Mission 22 M4 — OIDC SSO hooks (Team tier).
--
-- Two additive tables:
--
-- team_sso_configs — per-team IdP configuration. One row per team
-- (team_id PK): the OIDC issuer URL (the discovery root, RFC 8414 style:
-- <issuer> + /.well-known/openid-configuration), client id, and the client
-- secret ENCRYPTED with the same KeyVault Fernet used for api_keys /
-- github tokens (never stored or logged in plaintext). `enabled` lets an
-- admin suspend SSO without losing the config; `idp_display_name` is a
-- cosmetic label for the login page. FK to teams CASCADEs per the W5-C2
-- deletion law: SSO config dies with its team.
--
-- sso_login_states — cross-process ephemeral auth state for the
-- authorization-code + PKCE round trip, mirroring migration 0005's
-- oauth_states discipline: state and PKCE verifier hashes only (SHA-256 hex),
-- timestamps as UTC ISO-8601 TEXT so SQLite/Postgres compare identically.
-- Redemption is atomic single-statement DELETE ... RETURNING (cross-process
-- single-use); expired rows are swept opportunistically at mint time.

CREATE TABLE team_sso_configs (
    team_id VARCHAR(36) PRIMARY KEY REFERENCES teams (id) ON DELETE CASCADE,
    issuer_url TEXT NOT NULL,
    client_id VARCHAR(256) NOT NULL,
    client_secret_encrypted TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    idp_display_name VARCHAR(200) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

CREATE TABLE sso_login_states (
    state_hash VARCHAR(64) PRIMARY KEY,
    nonce_hash VARCHAR(64) NOT NULL,
    pkce_verifier_hash VARCHAR(64) NOT NULL,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    redirect_uri TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_sso_login_states_expires ON sso_login_states (expires_at);
