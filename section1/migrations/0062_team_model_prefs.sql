-- 0062: per-team model preferences (Settings -> Models).
--
-- The model router already had in-memory per-role overrides; they died on
-- restart. This table makes the team's primary/reviewer model and reasoning
-- effort durable. Effort is stored for every deployment; it is only sent to a
-- provider that accepts a `reasoning` object (currently OpenRouter-style
-- endpoints), see harness_router.prefs.reasoning_wire_supported.

CREATE TABLE IF NOT EXISTS team_model_prefs (
    team_id VARCHAR(36) NOT NULL,
    primary_model VARCHAR(120) NOT NULL,
    reviewer_model VARCHAR(120) NOT NULL,
    reasoning_effort VARCHAR(10) NOT NULL DEFAULT 'off',
    updated_by VARCHAR(36),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (team_id),
    CONSTRAINT fk_team_model_prefs_team_id FOREIGN KEY(team_id)
        REFERENCES teams (id) ON DELETE CASCADE
);
