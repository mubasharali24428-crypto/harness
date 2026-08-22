-- migration 0001: initial frozen schema (17 tables)

CREATE TABLE teams (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

CREATE TABLE api_keys (
	id VARCHAR(36) NOT NULL, 
	team_id VARCHAR(36) NOT NULL, 
	provider VARCHAR(40) NOT NULL, 
	label VARCHAR(120) NOT NULL, 
	key_encrypted TEXT NOT NULL, 
	key_hint VARCHAR(8) NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE TABLE auth_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	team_id VARCHAR(36) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE TABLE github_connections (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	github_login VARCHAR(100) NOT NULL, 
	access_token_encrypted TEXT NOT NULL, 
	scopes JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE model_configs (
	id VARCHAR(36) NOT NULL, 
	team_id VARCHAR(36) NOT NULL, 
	agent_role VARCHAR(20) NOT NULL, 
	provider VARCHAR(40) NOT NULL, 
	model VARCHAR(120) NOT NULL, 
	params JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE TABLE projects (
	id VARCHAR(36) NOT NULL, 
	team_id VARCHAR(36) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	repo_full_name VARCHAR(400), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE TABLE team_members (
	id VARCHAR(36) NOT NULL, 
	team_id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36), 
	display_name VARCHAR(200) NOT NULL, 
	member_type VARCHAR(16) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE TABLE audit_log (
	seq BIGSERIAL NOT NULL, 
	actor_member_id VARCHAR(36), 
	actor_kind VARCHAR(16) NOT NULL, 
	action VARCHAR(80) NOT NULL, 
	target_type VARCHAR(60) NOT NULL, 
	target_id VARCHAR(80) NOT NULL, 
	detail JSONB NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (seq), 
	FOREIGN KEY(actor_member_id) REFERENCES team_members (id)
);

CREATE TABLE meetings (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	title VARCHAR(300) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE TABLE engineering_briefs (
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
	FOREIGN KEY(meeting_id) REFERENCES meetings (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(approved_by) REFERENCES users (id)
);

CREATE TABLE meeting_messages (
	id VARCHAR(36) NOT NULL, 
	meeting_id VARCHAR(36) NOT NULL, 
	sender_member_id VARCHAR(36), 
	kind VARCHAR(10) NOT NULL, 
	body TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(meeting_id) REFERENCES meetings (id), 
	FOREIGN KEY(sender_member_id) REFERENCES team_members (id)
);

CREATE TABLE agent_runs (
	id VARCHAR(36) NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	brief_id VARCHAR(36), 
	branch VARCHAR(300), 
	state VARCHAR(40) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(brief_id) REFERENCES engineering_briefs (id)
);

CREATE TABLE agent_events (
	seq BIGSERIAL NOT NULL, 
	project_id VARCHAR(36) NOT NULL, 
	run_id VARCHAR(36), 
	type VARCHAR(60) NOT NULL, 
	payload JSONB NOT NULL, 
	ts TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (seq), 
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (id)
);

CREATE TABLE agent_plans (
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
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (id)
);

CREATE TABLE cost_ledger (
	seq BIGSERIAL NOT NULL, 
	run_id VARCHAR(36), 
	agent_role VARCHAR(20) NOT NULL, 
	model VARCHAR(120) NOT NULL, 
	input_tokens INTEGER NOT NULL, 
	output_tokens INTEGER NOT NULL, 
	cost_usd NUMERIC(12, 6) NOT NULL, 
	latency_ms INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (seq), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (id)
);

CREATE TABLE pull_requests (
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
	FOREIGN KEY(project_id) REFERENCES projects (id), 
	FOREIGN KEY(run_id) REFERENCES agent_runs (id), 
	FOREIGN KEY(approved_by) REFERENCES users (id)
);

CREATE TABLE review_comments (
	id VARCHAR(36) NOT NULL, 
	pr_id VARCHAR(36) NOT NULL, 
	author_user_id VARCHAR(36), 
	file_path VARCHAR(500), 
	line INTEGER, 
	body TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(pr_id) REFERENCES pull_requests (id), 
	FOREIGN KEY(author_user_id) REFERENCES users (id)
);
