-- Mission 22 lane M3 — Stripe webhook idempotency ledger.
-- The provider event id IS the primary key: a duplicate delivery trips the
-- PK conflict inside the SAME transaction as the tier sync, so replays are
-- exactly-once. team_id is nullable (events we cannot bind to a team are
-- still recorded for ops) and CASCADEs with its team (D1 deletion story).
-- Additive table only — no frozen table is altered.
CREATE TABLE stripe_webhook_events (
	id VARCHAR(255) NOT NULL,
	type VARCHAR(100) NOT NULL,
	team_id VARCHAR(36) NULL,
	customer_id VARCHAR(255) NULL,
	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(team_id) REFERENCES teams (id) ON DELETE CASCADE
);

CREATE INDEX ix_stripe_webhook_events_team_id ON stripe_webhook_events (team_id);
