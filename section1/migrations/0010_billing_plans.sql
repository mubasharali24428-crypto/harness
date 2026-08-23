-- Mission 10 node B1 — per-team billing plans behind the HARNESS_BILLING flag.
-- One row per team (team_id UNIQUE): upserted by PUT /api/v1/billing/plan and
-- read by GET /api/v1/billing/state. plan CHECK mirrors contract M10-C3 tiers;
-- monthly_budget_usd seeds the quota meter locally until a Stripe adapter
-- (HARNESS_BILLING=stripe + key present) supplies live numbers. Additive table
-- only — no frozen table is altered.
CREATE TABLE billing_plans (
	id VARCHAR(36) NOT NULL,
	team_id VARCHAR(36) NOT NULL,
	plan VARCHAR(30) NOT NULL,
	monthly_budget_usd NUMERIC(12,2) DEFAULT 0 NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (plan IN ('free', 'pro', 'team')),
	FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE UNIQUE INDEX uq_billing_plans_team_id ON billing_plans (team_id);
