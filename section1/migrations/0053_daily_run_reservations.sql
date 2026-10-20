-- Session 7 continuation: atomically count a run against the DB-clock daily tier cap.
CREATE TABLE team_daily_run_reservations (
    team_id VARCHAR(36) NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    run_id VARCHAR(36) NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE CASCADE,
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, run_id)
);
CREATE INDEX idx_team_daily_run_reservations_team_day
    ON team_daily_run_reservations (team_id, reserved_at);

-- Preserve the existing count behavior for runs created today before rollout.
INSERT INTO team_daily_run_reservations (team_id, run_id, reserved_at)
SELECT p.team_id, r.id, r.created_at
FROM agent_runs r
JOIN projects p ON p.id = r.project_id
WHERE r.created_at >= date_trunc('day', now())
ON CONFLICT (run_id) DO NOTHING;
