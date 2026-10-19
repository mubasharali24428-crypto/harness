-- 0039: permissions and policy model (master prompt part 38, Session 3 Lane P).
-- Policy documents per (org, level, scope), org-scoped custom roles, role assignments
-- below the org level, time-boxed emergency grants, the permission decision log, and
-- the per-session step-up timestamp. organizations/org_members belong to migration 0038.
-- Decision log retention: rows are an operational/security log, not evidence of record
-- (the audit chain is). Recommended: keep 90 days, purge older rows with
--   DELETE FROM policy_decision_log WHERE ts < now() - interval '90 days';
-- run from a scheduled job; ix_policy_decision_log_org_ts keeps the purge and the
-- admin query (bounded to 31 days / 200 rows) index-backed.
-- Rollback: DROP TABLE policy_stepup, policy_decision_log, policy_emergency_grants,
-- policy_assignments, policy_custom_roles, policy_documents (no other table references them).
-- Runner-safe: no transaction control, no dollar quoting.

CREATE TABLE IF NOT EXISTS policy_documents (
    id         VARCHAR(36) PRIMARY KEY,
    org_id     VARCHAR(36) NOT NULL,
    level      VARCHAR(16) NOT NULL,
    scope_id   VARCHAR(64) NOT NULL,
    version    INTEGER NOT NULL,
    doc        JSONB NOT NULL,
    updated_by VARCHAR(36) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_policy_documents_scope UNIQUE (org_id, level, scope_id)
);

CREATE TABLE IF NOT EXISTS policy_custom_roles (
    org_id      VARCHAR(36) NOT NULL,
    name        VARCHAR(32) NOT NULL,
    permissions JSONB NOT NULL,
    created_by  VARCHAR(36) NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (org_id, name)
);

CREATE TABLE IF NOT EXISTS policy_assignments (
    id         VARCHAR(36) PRIMARY KEY,
    org_id     VARCHAR(36) NOT NULL,
    user_id    VARCHAR(36) NOT NULL,
    level      VARCHAR(16) NOT NULL,
    scope_id   VARCHAR(64) NOT NULL,
    role       VARCHAR(32) NOT NULL,
    granted_by VARCHAR(36) NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_policy_assignment UNIQUE (org_id, user_id, level, scope_id, role)
);

CREATE TABLE IF NOT EXISTS policy_emergency_grants (
    id               VARCHAR(36) PRIMARY KEY,
    org_id           VARCHAR(36) NOT NULL,
    user_id          VARCHAR(36) NOT NULL,
    level            VARCHAR(16) NOT NULL,
    scope_id         VARCHAR(64) NOT NULL,
    permissions      JSONB NOT NULL,
    reason           TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    status           VARCHAR(12) NOT NULL,
    requested_by     VARCHAR(36) NOT NULL,
    approved_by      VARCHAR(36),
    starts_at        TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ,
    revoked_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_policy_emergency_org_user
    ON policy_emergency_grants (org_id, user_id, status);

CREATE TABLE IF NOT EXISTS policy_decision_log (
    id         VARCHAR(36) PRIMARY KEY,
    org_id     VARCHAR(36) NOT NULL,
    ts         TIMESTAMPTZ NOT NULL,
    subject    VARCHAR(64) NOT NULL,
    permission VARCHAR(16) NOT NULL,
    resource   VARCHAR(512) NOT NULL,
    decision   VARCHAR(5) NOT NULL,
    reason     VARCHAR(500) NOT NULL,
    rule_ids   JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_policy_decision_log_org_ts ON policy_decision_log (org_id, ts);

CREATE TABLE IF NOT EXISTS policy_stepup (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id    VARCHAR(36) NOT NULL,
    stepup_at  TIMESTAMPTZ NOT NULL
);
