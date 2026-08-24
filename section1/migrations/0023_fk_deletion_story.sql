-- Mission 15 Wave 5 lane D1 — DB#6: FK deletion story made explicit.
--
-- Every foreign key in the schema now carries an explicit ON DELETE,
-- matching models.py (pair contract W5-C2). The deletion law:
--   CASCADE  - owned children die with their owner
--              (team -> projects/keys/configs, project -> runs/events/...).
--   SET NULL - optional back-references survive the referenced row
--              (run.brief_id / run.pr_id / event.run_id, approved_by, ...).
--   RESTRICT - append-only integrity: audit_log and cost_ledger rows are
--              never deleted or detached as a side effect of a parent delete.
--
-- Mechanics, per repo conventions:
--   * Append-only chain law: 0001 is never edited. Unnamed 0001 FKs carry
--     deployment-dependent constraint names, so PHASE A discovers and drops
--     every FK via pg_constraint instead of guessing names.
--   * Whole-schema restripe: old child tables must release their old-parent
--     references BEFORE any old parent can be dropped, so every FK is
--     stripped up front, then rebuilt against the final shapes.
--   * Recreate-table pattern for the 0001 tables (SQLite compat: SQLite has
--     no DROP CONSTRAINT; deploy replays migrations statement-by-statement,
--     so each statement here stands alone. The $$ DO bodies are
--     Postgres-only and inert where they do not apply).
--   * Runner-compat: dollar quotes are plain $$ (deploy/runners/migrate.py
--     splits on literal $$), no BEGIN/COMMIT (the runner already wraps the
--     whole file in one transaction, like every migration before this one).
--   * Named fk_* constraints from here on (0016/0019 convention). Indexes
--     die with their dropped tables and are recreated under their names.
--
-- Behavioral pins live in tests/test_fk_deletion_story.py.

-- ===========================================================================
-- PHASE A — strip every existing FK constraint (data untouched).
-- Generic per-table sweep: zero rows => no-op, so this phase is idempotent.
-- ===========================================================================

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'team_members'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE team_members DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'projects'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE projects DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'meetings'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE meetings DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'engineering_briefs'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE engineering_briefs DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'agent_runs'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE agent_runs DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'pull_requests'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE pull_requests DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'review_comments'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE review_comments DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'agent_events'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE agent_events DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'agent_plans'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE agent_plans DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'api_keys'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE api_keys DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'model_configs'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE model_configs DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'auth_sessions'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE auth_sessions DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'github_connections'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE github_connections DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'meeting_messages'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE meeting_messages DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'project_rules'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE project_rules DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'user_settings'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE user_settings DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'billing_plans'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE billing_plans DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'team_invites'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE team_invites DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'cost_ledger'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE cost_ledger DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'audit_log'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE audit_log DROP CONSTRAINT %I', r.conname);
    END LOOP;
END
$$;

-- ===========================================================================
-- PHASE B — recreate the 0001 tables with explicit ON DELETE, parent-first.
-- ===========================================================================

CREATE TABLE team_members_d1_new (
    id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36),
    display_name VARCHAR(200) NOT NULL,
    member_type VARCHAR(16) NOT NULL,
    role VARCHAR(16) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_team_members_team_id FOREIGN KEY(team_id)
        REFERENCES teams (id) ON DELETE CASCADE,
    CONSTRAINT fk_team_members_user_id FOREIGN KEY(user_id)
        REFERENCES users (id) ON DELETE SET NULL
);
INSERT INTO team_members_d1_new
    (id, team_id, user_id, display_name, member_type, role, created_at)
SELECT id, team_id, user_id, display_name, member_type, role, created_at
FROM team_members;
DROP TABLE team_members;
ALTER TABLE team_members_d1_new RENAME TO team_members;
CREATE INDEX ix_team_members_team_id ON team_members (team_id);
CREATE INDEX ix_team_members_user_id ON team_members (user_id);

CREATE TABLE projects_d1_new (
    id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    name VARCHAR(200) NOT NULL,
    repo_full_name VARCHAR(400),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_projects_team_id FOREIGN KEY(team_id)
        REFERENCES teams (id) ON DELETE CASCADE
);
INSERT INTO projects_d1_new (id, team_id, name, repo_full_name, created_at)
SELECT id, team_id, name, repo_full_name, created_at FROM projects;
DROP TABLE projects;
ALTER TABLE projects_d1_new RENAME TO projects;
CREATE INDEX ix_projects_team_id ON projects (team_id);

CREATE TABLE meetings_d1_new (
    id VARCHAR(36) NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    title VARCHAR(300) NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_meetings_project_id FOREIGN KEY(project_id)
        REFERENCES projects (id) ON DELETE CASCADE
);
INSERT INTO meetings_d1_new (id, project_id, title, status, created_at)
SELECT id, project_id, title, status, created_at FROM meetings;
DROP TABLE meetings;
ALTER TABLE meetings_d1_new RENAME TO meetings;
CREATE INDEX ix_meetings_project_id ON meetings (project_id);

CREATE TABLE engineering_briefs_d1_new (
    id VARCHAR(36) NOT NULL,
    meeting_id VARCHAR(36) NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    requirements JSONB NOT NULL,
    constraints JSONB NOT NULL,
    acceptance_criteria JSONB NOT NULL,
    risks JSONB NOT NULL,
    open_questions JSONB NOT NULL,
    status VARCHAR(20) NOT NULL,
    approved_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_engineering_briefs_meeting_id FOREIGN KEY(meeting_id)
        REFERENCES meetings (id) ON DELETE CASCADE,
    CONSTRAINT fk_engineering_briefs_project_id FOREIGN KEY(project_id)
        REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT fk_engineering_briefs_approved_by FOREIGN KEY(approved_by)
        REFERENCES users (id) ON DELETE SET NULL
);
INSERT INTO engineering_briefs_d1_new
    (id, meeting_id, project_id, requirements, constraints, acceptance_criteria,
     risks, open_questions, status, approved_by, created_at)
SELECT id, meeting_id, project_id, requirements, constraints, acceptance_criteria,
       risks, open_questions, status, approved_by, created_at
FROM engineering_briefs;
DROP TABLE engineering_briefs;
ALTER TABLE engineering_briefs_d1_new RENAME TO engineering_briefs;
CREATE INDEX ix_engineering_briefs_meeting_id ON engineering_briefs (meeting_id);
CREATE INDEX ix_engineering_briefs_project_id ON engineering_briefs (project_id);

-- 0017 follow-up: carry any live pr_id values across the restripe. The FK
-- itself is attached AFTER pull_requests is rebuilt below (runs <-> PRs form
-- a reference cycle; attaching early would pin the old table).
CREATE TABLE agent_runs_pr_id_backfill AS SELECT id, pr_id FROM agent_runs;

CREATE TABLE agent_runs_d1_new (
    id VARCHAR(36) NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    brief_id VARCHAR(36),
    branch VARCHAR(300),
    state VARCHAR(40) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    pr_id VARCHAR(36),
    PRIMARY KEY (id),
    CONSTRAINT fk_agent_runs_project_id FOREIGN KEY(project_id)
        REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_runs_brief_id FOREIGN KEY(brief_id)
        REFERENCES engineering_briefs (id) ON DELETE SET NULL
);
INSERT INTO agent_runs_d1_new
    (id, project_id, brief_id, branch, state, updated_at, created_at, pr_id)
SELECT r.id, r.project_id, r.brief_id, r.branch, r.state, r.updated_at,
       r.created_at, b.pr_id
FROM agent_runs r
LEFT JOIN agent_runs_pr_id_backfill b ON b.id = r.id;
DROP TABLE agent_runs;
ALTER TABLE agent_runs_d1_new RENAME TO agent_runs;
CREATE INDEX ix_agent_runs_project_id ON agent_runs (project_id);

CREATE TABLE pull_requests_d1_new (
    id VARCHAR(36) NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36),
    github_pr_number INTEGER NOT NULL,
    pr_url VARCHAR(500) NOT NULL,
    summary_md TEXT NOT NULL,
    status VARCHAR(30) NOT NULL,
    approved_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_pull_requests_project_id FOREIGN KEY(project_id)
        REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT fk_pull_requests_run_id FOREIGN KEY(run_id)
        REFERENCES agent_runs (id) ON DELETE SET NULL,
    CONSTRAINT fk_pull_requests_approved_by FOREIGN KEY(approved_by)
        REFERENCES users (id) ON DELETE SET NULL
);
INSERT INTO pull_requests_d1_new
    (id, project_id, run_id, github_pr_number, pr_url, summary_md, status,
     approved_by, created_at)
SELECT id, project_id, run_id, github_pr_number, pr_url, summary_md, status,
       approved_by, created_at
FROM pull_requests;
DROP TABLE pull_requests;
ALTER TABLE pull_requests_d1_new RENAME TO pull_requests;
CREATE INDEX ix_pull_requests_project_id ON pull_requests (project_id);

-- Now that the new pull_requests owns the name, the cycle leg gets its
-- explicit story: deleting a PR nulls the run's back-reference.
ALTER TABLE agent_runs
    ADD CONSTRAINT fk_agent_runs_pr_id
    FOREIGN KEY (pr_id) REFERENCES pull_requests (id) ON DELETE SET NULL;
DROP TABLE agent_runs_pr_id_backfill;

CREATE TABLE review_comments_d1_new (
    id VARCHAR(36) NOT NULL,
    pr_id VARCHAR(36) NOT NULL,
    author_user_id VARCHAR(36),
    file_path VARCHAR(500),
    line INTEGER,
    body TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_review_comments_pr_id FOREIGN KEY(pr_id)
        REFERENCES pull_requests (id) ON DELETE CASCADE,
    CONSTRAINT fk_review_comments_author_user_id FOREIGN KEY(author_user_id)
        REFERENCES users (id) ON DELETE SET NULL
);
INSERT INTO review_comments_d1_new
    (id, pr_id, author_user_id, file_path, line, body, created_at)
SELECT id, pr_id, author_user_id, file_path, line, body, created_at
FROM review_comments;
DROP TABLE review_comments;
ALTER TABLE review_comments_d1_new RENAME TO review_comments;
CREATE INDEX ix_review_comments_pr_id ON review_comments (pr_id);

CREATE TABLE agent_events_d1_new (
    seq BIGSERIAL NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36),
    type VARCHAR(60) NOT NULL,
    payload JSONB NOT NULL,
    ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (seq),
    CONSTRAINT fk_agent_events_project_id FOREIGN KEY(project_id)
        REFERENCES projects (id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_events_run_id FOREIGN KEY(run_id)
        REFERENCES agent_runs (id) ON DELETE SET NULL
);
INSERT INTO agent_events_d1_new (seq, project_id, run_id, type, payload, ts)
SELECT seq, project_id, run_id, type, payload, ts FROM agent_events;
DROP TABLE agent_events;
ALTER TABLE agent_events_d1_new RENAME TO agent_events;
CREATE INDEX ix_agent_events_project_id ON agent_events (project_id);
CREATE INDEX ix_agent_events_run_id ON agent_events (run_id);
-- Secondary indexes from 0002/0012/0018 died with the old table.
CREATE INDEX IF NOT EXISTS idx_agent_events_project_seq ON agent_events (project_id, seq);
CREATE INDEX IF NOT EXISTS idx_agent_events_run_id_type ON agent_events (run_id, type);
CREATE INDEX IF NOT EXISTS idx_agent_events_ts ON agent_events (ts);
-- 0003's LISTEN/NOTIFY trigger died with the old table too; the event bus
-- (Section 1 owns delivery) needs it re-attached to the replacement table.
DROP TRIGGER IF EXISTS trg_notify_agent_events ON agent_events;
CREATE TRIGGER trg_notify_agent_events
AFTER INSERT ON agent_events
FOR EACH ROW EXECUTE FUNCTION notify_agent_event();
SELECT setval(pg_get_serial_sequence('agent_events', 'seq'),
              COALESCE((SELECT MAX(seq) FROM agent_events), 0) + 1, false);

CREATE TABLE agent_plans_d1_new (
    id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change JSONB NOT NULL,
    steps JSONB NOT NULL,
    tests JSONB NOT NULL,
    risks JSONB NOT NULL,
    estimated_cost_usd NUMERIC(12, 6) NOT NULL,
    forbidden_paths JSONB NOT NULL,
    expected_result TEXT NOT NULL,
    status VARCHAR(20) NOT NULL,
    -- 0013 follow-up: plan versioning survives the restripe.
    version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_agent_plans_run_id FOREIGN KEY(run_id)
        REFERENCES agent_runs (id) ON DELETE CASCADE
);
INSERT INTO agent_plans_d1_new
    (id, run_id, goal, files_likely_to_change, steps, tests, risks,
     estimated_cost_usd, forbidden_paths, expected_result, status, version,
     created_at)
SELECT id, run_id, goal, files_likely_to_change, steps, tests, risks,
       estimated_cost_usd, forbidden_paths, expected_result, status, version,
       created_at
FROM agent_plans;
DROP TABLE agent_plans;
ALTER TABLE agent_plans_d1_new RENAME TO agent_plans;

CREATE TABLE api_keys_d1_new (
    id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    provider VARCHAR(40) NOT NULL,
    label VARCHAR(120) NOT NULL,
    key_encrypted TEXT NOT NULL,
    key_hint VARCHAR(8) NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_api_keys_team_id FOREIGN KEY(team_id)
        REFERENCES teams (id) ON DELETE CASCADE
);
INSERT INTO api_keys_d1_new
    (id, team_id, provider, label, key_encrypted, key_hint, revoked_at, created_at)
SELECT id, team_id, provider, label, key_encrypted, key_hint, revoked_at, created_at
FROM api_keys;
DROP TABLE api_keys;
ALTER TABLE api_keys_d1_new RENAME TO api_keys;
CREATE INDEX ix_api_keys_team_id ON api_keys (team_id);

CREATE TABLE model_configs_d1_new (
    id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    agent_role VARCHAR(20) NOT NULL,
    provider VARCHAR(40) NOT NULL,
    model VARCHAR(120) NOT NULL,
    params JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_model_configs_team_id FOREIGN KEY(team_id)
        REFERENCES teams (id) ON DELETE CASCADE
);
INSERT INTO model_configs_d1_new
    (id, team_id, agent_role, provider, model, params, created_at)
SELECT id, team_id, agent_role, provider, model, params, created_at
FROM model_configs;
DROP TABLE model_configs;
ALTER TABLE model_configs_d1_new RENAME TO model_configs;
CREATE INDEX ix_model_configs_team_id ON model_configs (team_id);

CREATE TABLE auth_sessions_d1_new (
    token_hash VARCHAR(64) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (token_hash),
    CONSTRAINT fk_auth_sessions_user_id FOREIGN KEY(user_id)
        REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_auth_sessions_team_id FOREIGN KEY(team_id)
        REFERENCES teams (id) ON DELETE CASCADE
);
INSERT INTO auth_sessions_d1_new (token_hash, user_id, team_id, expires_at)
SELECT token_hash, user_id, team_id, expires_at FROM auth_sessions;
DROP TABLE auth_sessions;
ALTER TABLE auth_sessions_d1_new RENAME TO auth_sessions;
CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);
-- 0002 composite index died with the old table.
CREATE INDEX IF NOT EXISTS idx_auth_sessions_team_user ON auth_sessions (team_id, user_id);

CREATE TABLE github_connections_d1_new (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    github_login VARCHAR(100) NOT NULL,
    access_token_encrypted TEXT NOT NULL,
    scopes JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_github_connections_user_id FOREIGN KEY(user_id)
        REFERENCES users (id) ON DELETE CASCADE
);
INSERT INTO github_connections_d1_new
    (id, user_id, github_login, access_token_encrypted, scopes, created_at)
SELECT id, user_id, github_login, access_token_encrypted, scopes, created_at
FROM github_connections;
DROP TABLE github_connections;
ALTER TABLE github_connections_d1_new RENAME TO github_connections;
CREATE INDEX ix_github_connections_user_id ON github_connections (user_id);

-- ===========================================================================
-- PHASE C — re-arm the tables that kept their rows in place (FKs were
-- stripped in PHASE A; shapes unchanged, constraints rebuilt explicit).
-- ===========================================================================

ALTER TABLE meeting_messages
    ADD CONSTRAINT fk_meeting_messages_meeting_id
    FOREIGN KEY (meeting_id) REFERENCES meetings (id) ON DELETE CASCADE;
ALTER TABLE meeting_messages
    ADD CONSTRAINT fk_meeting_messages_sender_member_id
    FOREIGN KEY (sender_member_id) REFERENCES team_members (id) ON DELETE SET NULL;

ALTER TABLE project_rules
    ADD CONSTRAINT fk_project_rules_project_id
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE;
ALTER TABLE project_rules
    ADD CONSTRAINT fk_project_rules_updated_by
    FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE user_settings
    ADD CONSTRAINT fk_user_settings_user_id
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE billing_plans
    ADD CONSTRAINT fk_billing_plans_team_id
    FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE;

ALTER TABLE team_invites
    ADD CONSTRAINT fk_team_invites_team_id
    FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE;

ALTER TABLE cost_ledger
    ADD CONSTRAINT fk_cost_ledger_run_id
    FOREIGN KEY (run_id) REFERENCES agent_runs (id) ON DELETE RESTRICT;

ALTER TABLE audit_log
    ADD CONSTRAINT fk_audit_log_actor_member_id
    FOREIGN KEY (actor_member_id) REFERENCES team_members (id) ON DELETE RESTRICT;
