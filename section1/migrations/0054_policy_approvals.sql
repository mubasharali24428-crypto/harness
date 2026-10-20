-- Session 7 continuation: optional second-person approval for high-risk policies.
ALTER TABLE organizations
    ADD COLUMN require_dual_approval BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE policy_change_requests (
    id VARCHAR(36) PRIMARY KEY,
    org_id VARCHAR(36) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    level VARCHAR(16) NOT NULL,
    scope_id VARCHAR(64) NOT NULL,
    proposed_doc JSONB NOT NULL,
    expected_version INTEGER NOT NULL,
    requested_by VARCHAR(36) NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ
);
CREATE INDEX idx_policy_change_requests_pending
    ON policy_change_requests (org_id, requested_at) WHERE status = 'pending';
