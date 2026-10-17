-- A1-S5 — trial-to-paid lifecycle and workspace closure.
-- team_trials: at most one trial per team EVER (team_id PK). While active the plan is
-- stored in billing_plans (one storage, one truth, every tier consumer honors it);
-- expiry reverts it to 'free' unless a provider payment converted it first.
-- teams.closed_at: self-serve workspace close. Closed teams cannot authenticate; rows and
-- audit history are kept (audit_log -> team_members is RESTRICT, so hard delete is an
-- operator task, not an API).
CREATE TABLE team_trials (
	team_id VARCHAR(36) PRIMARY KEY REFERENCES teams (id) ON DELETE CASCADE,
	plan VARCHAR(30) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE NOT NULL,
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
	converted_at TIMESTAMP WITH TIME ZONE NULL,
	expired_at TIMESTAMP WITH TIME ZONE NULL
);
ALTER TABLE teams ADD COLUMN closed_at TIMESTAMP WITH TIME ZONE NULL;
