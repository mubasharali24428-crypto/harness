-- Wave 9 node C: org invite tokens (email-based team membership invites).
-- Raw tokens are shown ONCE at mint time and never stored; only their SHA-256
-- hex digest lives here (same scheme as auth_sessions/login_otps).
-- Redemption is atomic: UPDATE ... WHERE token_hash = :h
--   AND redeemed_at IS NULL AND expires_at > :now RETURNING ...
-- role CHECK mirrors harness_contracts.roles.Role (admin/engineer/viewer/guest).

CREATE TABLE team_invites (
	id VARCHAR(36) NOT NULL,
	team_id VARCHAR(36) NOT NULL,
	email VARCHAR(255) NOT NULL,
	role VARCHAR(20) NOT NULL,
	token_hash VARCHAR(64) NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	redeemed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (role IN ('admin', 'engineer', 'viewer', 'guest')),
	FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE UNIQUE INDEX uq_team_invites_token_hash ON team_invites (token_hash);
CREATE INDEX ix_team_invites_team_id ON team_invites (team_id);
