-- P-17 client trust portal: which projects a team guest may read.
-- A guest with no row here can read nothing (see harness_contracts.fastapi_auth.guest_gate).
CREATE TABLE project_guest_grants (
    project_id VARCHAR(36) NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    granted_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX ix_project_guest_grants_user ON project_guest_grants (user_id);
