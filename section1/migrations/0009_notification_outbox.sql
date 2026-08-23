-- Wave 9 node D — notification outbox (durable email delivery).
-- Rows are enqueued by event subscribers (run_finished / review_failed /
-- pr_opened) and drained by the notifier worker with backoff. Dev mode (no
-- HARNESS_SMTP_HOST) marks rows sent after logging so CI stays offline-green.
-- team_id is deliberately NULLable: bus events carry projectId only, and a
-- project→team lookup must never block or fail an enqueue.
CREATE TABLE notification_outbox (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) REFERENCES teams (id) ON DELETE SET NULL,
    to_email VARCHAR(255) NOT NULL,
    template VARCHAR(40) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(10) NOT NULL CHECK (status IN ('queued', 'sent', 'failed')),
    attempts INTEGER DEFAULT 0 NOT NULL,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    sent_at TIMESTAMPTZ
);
CREATE INDEX idx_notification_outbox_status ON notification_outbox (status);
