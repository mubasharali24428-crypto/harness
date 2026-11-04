-- 0064: deployment requests and the approval bound to them (W9).
-- A deployment row is a RECORD, not an action: this repo deploys nothing. What it
-- guarantees is that an approval names the exact reviewed revision. The binding is
-- the same triple merge approval uses - changes_digest (path + diff_sha pairs),
-- the run's head sha, and the plan version - plus the base it was built on.
-- approve/revoke are checked against the CURRENT revision at decision time, so a
-- head that moved after the request makes the request stale (409) instead of
-- approving something nobody reviewed. state is requested, approved or revoked -
-- exactly what the API can write; revocation is final and idempotent. History
-- lives in audit_log.
-- JSON text columns, no dollar quotes, no now() default (the 0049 convention).
-- Rollback: DROP TABLE deployments (product data only, audit_log untouched).

CREATE TABLE IF NOT EXISTS deployments (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    environment VARCHAR(60) NOT NULL,
    changes_digest VARCHAR(64) NOT NULL,
    run_head_sha VARCHAR(64),
    base_sha VARCHAR(64),
    plan_version INTEGER,
    state VARCHAR(12) NOT NULL DEFAULT 'requested',
    requested_by VARCHAR(64) NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    decided_by VARCHAR(64),
    decided_at TIMESTAMPTZ,
    revoked_by VARCHAR(64),
    revoked_at TIMESTAMPTZ,
    note VARCHAR(500),
    request_id VARCHAR(64),
    CONSTRAINT deployments_state_chk CHECK (
        state IN ('requested', 'approved', 'revoked')
    )
);

CREATE INDEX IF NOT EXISTS deployments_run ON deployments (team_id, run_id, requested_at);

-- A retried request with the same request id is one deployment.
CREATE UNIQUE INDEX IF NOT EXISTS deployments_request
    ON deployments (run_id, request_id) WHERE request_id IS NOT NULL;
