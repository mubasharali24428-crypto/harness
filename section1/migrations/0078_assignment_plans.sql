-- Phase 7 (C-01..C-04) admin-reviewed assignment planning. Rows are IMMUTABLE versions:
-- every proposal and every admin review (approve | reject | edit) inserts version N+1, so
-- version 1 stays readable after version 2 exists. UNIQUE (project_id, version) is the
-- compare-and-swap: two concurrent reviews of the same version -> one wins, the other
-- gets 409 STALE_VERSION and must re-review the latest. Nothing here dispatches work
-- (D-05: AI proposes, admin approves). See harness_orchestrator/assignment_plan.py.
CREATE TABLE IF NOT EXISTS assignment_plan_versions (
    id             VARCHAR(36) PRIMARY KEY,
    team_id        VARCHAR(36) NOT NULL,
    project_id     VARCHAR(36) NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version        INTEGER NOT NULL CHECK (version >= 1),
    parent_version INTEGER,
    state          VARCHAR(16) NOT NULL CHECK (state IN ('proposed', 'approved', 'rejected')),
    brief          TEXT NOT NULL,
    assignments    TEXT NOT NULL,
    created_by     VARCHAR(64) NOT NULL,
    note           VARCHAR(500),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, version)
);
CREATE INDEX IF NOT EXISTS assignment_plan_versions_team ON assignment_plan_versions (team_id);
