-- 0057: per-project opt-in for GitHub writes (branches, commits, draft PRs).
-- Reads and the repo connection stay available. Writes are refused until a team
-- admin turns them on for the project, so existing projects gain no write access.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS github_writes_enabled BOOLEAN NOT NULL DEFAULT FALSE;
