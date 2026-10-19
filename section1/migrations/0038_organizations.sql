-- 0038: organizations and identity (master prompt part 37). Lane O, Session 3.
-- Additive only. org_members is the source of truth for org roles. Users may belong to
-- many orgs. Guests carry expires_at and are expired lazily on every read.
-- teams.org_id is a nullable link: a team belongs to at most one org.
-- org_session_assurance records how a session authenticated (SSO or MFA) so require_sso
-- and require_mfa can be enforced. The SSO/MFA completion path must call
-- mark_session_assurance (see orgs.py) for the flags to be satisfiable.
-- Rollback: DROP the four org_* tables and organizations, then ALTER TABLE teams DROP COLUMN org_id.
-- Runner-safe: no dollar quoting, no transaction control, no now() defaults (code supplies timestamps).

CREATE TABLE organizations (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    slug VARCHAR(40) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
    require_sso BOOLEAN NOT NULL DEFAULT FALSE,
    require_mfa BOOLEAN NOT NULL DEFAULT FALSE,
    allowed_domains TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE org_members (
    org_id VARCHAR(36) NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL CHECK (role IN ('owner', 'admin', 'billing_admin', 'member', 'guest')),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    removed_at TIMESTAMPTZ,
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX idx_org_members_user ON org_members (user_id);

CREATE TABLE org_invites (
    id VARCHAR(36) PRIMARY KEY,
    org_id VARCHAR(36) NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    email VARCHAR(320) NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('admin', 'billing_admin', 'member', 'guest')),
    guest_days INTEGER,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    redeemed_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_org_invites_org ON org_invites (org_id);

CREATE TABLE org_service_identities (
    id VARCHAR(36) PRIMARY KEY,
    org_id VARCHAR(36) NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    scopes TEXT NOT NULL DEFAULT '[]',
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);
CREATE INDEX idx_org_service_identities_org ON org_service_identities (org_id);

CREATE TABLE org_session_assurance (
    token_hash VARCHAR(64) PRIMARY KEY,
    sso BOOLEAN NOT NULL DEFAULT FALSE,
    mfa BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE teams ADD COLUMN org_id VARCHAR(36) REFERENCES organizations (id);
CREATE INDEX idx_teams_org ON teams (org_id);
