-- Session 7, part 49: team-owned, versioned outbound webhook integrations.
-- Delivery rows contain only allow-listed audit identity fields; never detail text.
-- Rollback: DROP TABLE integration_webhook_deliveries, integration_webhooks.

CREATE TABLE integration_webhooks (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    endpoint_url TEXT NOT NULL,
    event_types JSONB NOT NULL,
    secret_encrypted TEXT NOT NULL,
    secret_hint VARCHAR(8) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_by VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_integration_webhooks_team_active ON integration_webhooks(team_id, active);

CREATE TABLE integration_webhook_deliveries (
    id VARCHAR(36) PRIMARY KEY,
    webhook_id VARCHAR(36) NOT NULL REFERENCES integration_webhooks(id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    audit_seq BIGINT NOT NULL REFERENCES audit_log(seq) ON DELETE RESTRICT,
    event_type VARCHAR(80) NOT NULL,
    payload JSONB NOT NULL,
    state VARCHAR(12) NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'sending', 'delivered', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    last_status_code INTEGER,
    last_error VARCHAR(40),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    CONSTRAINT uq_webhook_audit_event UNIQUE(webhook_id, audit_seq)
);
CREATE INDEX ix_webhook_delivery_due
    ON integration_webhook_deliveries(state, next_attempt_at, claimed_at);
