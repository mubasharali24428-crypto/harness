-- migration 0002: production-grade indexes for hot replay/audit paths (H5)

CREATE INDEX IF NOT EXISTS idx_agent_events_project_seq ON agent_events (project_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log (ts);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_team_user ON auth_sessions (team_id, user_id);
