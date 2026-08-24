-- M15-G6b (raw-02 L4): per-team reviewer-heuristic thresholds.
-- Generic (team_id, key) -> value store so team-scoped settings land without
-- a new migration each time; mirrors 0016_user_settings.sql at team scope.
-- Known key: reviewer_thresholds — JSON object of reviewer pass/fail knobs,
-- written ONLY through the admin-gated PUT /teams/{id}/settings/... handler
-- and read leniently by Section 8's reviewer (absent/malformed row means the
-- documented defaults, never an error). Purely additive: FKs to teams/users
-- with explicit ON DELETE per the W5-C2 cascade law.
CREATE TABLE team_settings (
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    key VARCHAR(80) NOT NULL,
    value TEXT,
    updated_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    PRIMARY KEY (team_id, key)
);
