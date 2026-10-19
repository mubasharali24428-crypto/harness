-- 0040: workspaces (master prompt part 39, Session 3 lane W).
-- A workspace is a team-owned container for projects with its own membership,
-- versioned settings, visibility, data residency and lifecycle state.
-- projects.workspace_id is the project link (nullable: a project without a
-- workspace behaves exactly as before). Linking grants no workspace membership.
-- States: active | paused | archived | deleted (soft delete, see docs/workspaces.md).
-- Portable SQL (PostgreSQL + SQLite for tests): JSON is stored as TEXT.
-- Rollback: DROP the workspace_* tables, workspaces, and projects.workspace_id.
-- Runner-safe: no dollar quoting, no transaction control.

CREATE TABLE IF NOT EXISTS workspaces (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    org_id VARCHAR(36),
    project_id VARCHAR(36),
    name VARCHAR(80) NOT NULL,
    name_key VARCHAR(80) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'active',
    state_reason VARCHAR(500),
    visibility VARCHAR(8) NOT NULL DEFAULT 'team',
    data_residency VARCHAR(16),
    owner_user_id VARCHAR(36) NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    settings TEXT NOT NULL DEFAULT '{}',
    settings_version INTEGER NOT NULL DEFAULT 1,
    template_id VARCHAR(36),
    template_name VARCHAR(80),
    template_version INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ,
    purge_after TIMESTAMPTZ,
    CONSTRAINT ck_workspaces_state CHECK (state IN ('active','paused','archived','deleted')),
    CONSTRAINT ck_workspaces_visibility CHECK (visibility IN ('private','team','org'))
);

-- Name is unique per team among non-deleted workspaces (case-insensitive via name_key).
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspaces_team_name
    ON workspaces (team_id, name_key) WHERE state <> 'deleted';
CREATE INDEX IF NOT EXISTS idx_workspaces_team_state ON workspaces (team_id, state);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'member',
    added_by VARCHAR(36),
    created_at TIMESTAMPTZ NOT NULL,
    removed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, user_id),
    CONSTRAINT ck_workspace_members_role CHECK (role IN ('owner','member','viewer'))
);

CREATE TABLE IF NOT EXISTS workspace_settings_versions (
    id VARCHAR(36) PRIMARY KEY,
    workspace_id VARCHAR(36) NOT NULL,
    version INTEGER NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    changed_by VARCHAR(36) NOT NULL,
    reverted_from INTEGER,
    template_name VARCHAR(80),
    template_version INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_workspace_settings_version UNIQUE (workspace_id, version)
);

CREATE TABLE IF NOT EXISTS workspace_templates (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    name VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL,
    defaults TEXT NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_workspace_template_version UNIQUE (team_id, name, version)
);

-- Org/team-approved allowlist of repository and environment values.
CREATE TABLE IF NOT EXISTS workspace_binding_allowlist (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    org_id VARCHAR(36),
    kind VARCHAR(16) NOT NULL,
    value VARCHAR(400) NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT ck_binding_allowlist_kind CHECK (kind IN ('repository','environment'))
);
CREATE INDEX IF NOT EXISTS idx_binding_allowlist_team ON workspace_binding_allowlist (team_id, kind);

CREATE TABLE IF NOT EXISTS workspace_bindings (
    id VARCHAR(36) PRIMARY KEY,
    workspace_id VARCHAR(36) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    value VARCHAR(400) NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    removed_at TIMESTAMPTZ,
    CONSTRAINT ck_workspace_bindings_kind CHECK (kind IN ('repository','environment'))
);
CREATE INDEX IF NOT EXISTS idx_workspace_bindings_ws ON workspace_bindings (workspace_id);

ALTER TABLE projects ADD COLUMN IF NOT EXISTS workspace_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects (workspace_id);
