-- 0048: execution tracking (part 44.004-010).
-- run_execution_limits: one optional bound per run for time, ledger-recorded model calls, tool calls
-- and cost (NULL means unbounded). run_subtasks with run_subtask_deps (dependencies), run_subtask_blockers
-- and run_subtask_evidence (completion evidence references, opaque text, not verified to exist).
-- run_completion: what the agent reported versus what a human decided, one row per run, plus the
-- append-only run_completion_history. A CHECK makes "accepted by the reporter" impossible at the
-- database level. run_attempts: retry requests (one in-flight per run, one row per attempt number) and
-- the single abandon or fail record of a run, with a snapshot of the evidence that was preserved.
-- Nothing here deletes anything: abandoning a run only adds a row.
-- Portable SQL (PostgreSQL + SQLite for tests), JSON-free (TEXT columns hold JSON text).
-- Rollback: DROP TABLE run_attempts, run_completion_history, run_completion, run_subtask_evidence,
-- run_subtask_blockers, run_subtask_deps, run_subtasks, run_execution_limits (product data only,
-- audit_log untouched).
-- Runner-safe: no dollar quotes, no transaction control, no semicolons in comments,
-- and no now() default so the file also loads on SQLite for tests.

CREATE TABLE IF NOT EXISTS run_execution_limits (
    run_id VARCHAR(36) PRIMARY KEY REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    max_seconds INTEGER,
    max_model_calls INTEGER,
    max_tool_calls INTEGER,
    max_cost_microusd BIGINT,
    version INTEGER NOT NULL DEFAULT 1,
    updated_by VARCHAR(36) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT run_limits_seconds_chk CHECK (max_seconds IS NULL OR max_seconds > 0),
    CONSTRAINT run_limits_model_chk CHECK (max_model_calls IS NULL OR max_model_calls > 0),
    CONSTRAINT run_limits_tool_chk CHECK (max_tool_calls IS NULL OR max_tool_calls > 0),
    CONSTRAINT run_limits_cost_chk CHECK (max_cost_microusd IS NULL OR max_cost_microusd > 0)
);

CREATE TABLE IF NOT EXISTS run_subtasks (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    state VARCHAR(12) NOT NULL DEFAULT 'todo',
    state_before_block VARCHAR(12),
    position INTEGER NOT NULL DEFAULT 0,
    request_id VARCHAR(64),
    version INTEGER NOT NULL DEFAULT 1,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_by VARCHAR(36) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_by VARCHAR(36),
    completed_at TIMESTAMPTZ,
    CONSTRAINT run_subtasks_state_chk CHECK (
        state IN ('todo', 'in_progress', 'blocked', 'done', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS run_subtasks_run ON run_subtasks (run_id, position, id);

-- A retried create with the same request id is one subtask.
CREATE UNIQUE INDEX IF NOT EXISTS run_subtasks_request
    ON run_subtasks (run_id, created_by, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS run_subtask_deps (
    subtask_id VARCHAR(36) NOT NULL REFERENCES run_subtasks (id) ON DELETE CASCADE,
    depends_on_id VARCHAR(36) NOT NULL REFERENCES run_subtasks (id) ON DELETE CASCADE,
    run_id VARCHAR(36) NOT NULL,
    PRIMARY KEY (subtask_id, depends_on_id),
    CONSTRAINT run_subtask_deps_self_chk CHECK (subtask_id <> depends_on_id)
);

CREATE INDEX IF NOT EXISTS run_subtask_deps_run ON run_subtask_deps (run_id);

CREATE TABLE IF NOT EXISTS run_subtask_blockers (
    id VARCHAR(36) PRIMARY KEY,
    subtask_id VARCHAR(36) NOT NULL REFERENCES run_subtasks (id) ON DELETE CASCADE,
    run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    state VARCHAR(8) NOT NULL DEFAULT 'open',
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    resolved_by VARCHAR(36),
    resolved_at TIMESTAMPTZ,
    resolution VARCHAR(500),
    CONSTRAINT run_subtask_blockers_state_chk CHECK (state IN ('open', 'resolved'))
);

CREATE INDEX IF NOT EXISTS run_subtask_blockers_subtask ON run_subtask_blockers (subtask_id, state);

-- The same open reason is recorded once per subtask (a retried POST is a no-op).
CREATE UNIQUE INDEX IF NOT EXISTS run_subtask_blockers_open_reason
    ON run_subtask_blockers (subtask_id, reason) WHERE state = 'open';

CREATE TABLE IF NOT EXISTS run_subtask_evidence (
    id VARCHAR(36) PRIMARY KEY,
    subtask_id VARCHAR(36) NOT NULL REFERENCES run_subtasks (id) ON DELETE CASCADE,
    run_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    kind VARCHAR(12) NOT NULL,
    ref VARCHAR(500) NOT NULL,
    note VARCHAR(500) NOT NULL DEFAULT '',
    added_by VARCHAR(36) NOT NULL,
    added_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT run_subtask_evidence_kind_chk CHECK (
        kind IN ('file', 'diff', 'test', 'output', 'link', 'note', 'artifact'))
);

-- The same reference is recorded once per subtask (a retried POST is a no-op).
CREATE UNIQUE INDEX IF NOT EXISTS run_subtask_evidence_ref
    ON run_subtask_evidence (subtask_id, kind, ref);

CREATE TABLE IF NOT EXISTS run_completion (
    run_id VARCHAR(36) PRIMARY KEY REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    agent_state VARCHAR(8) NOT NULL DEFAULT 'none',
    report_no INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    outputs TEXT NOT NULL DEFAULT '[]',
    report_digest VARCHAR(64),
    reported_by VARCHAR(36),
    reported_at TIMESTAMPTZ,
    human_state VARCHAR(8) NOT NULL DEFAULT 'none',
    decided_by VARCHAR(36),
    decided_at TIMESTAMPTZ,
    decision_reason VARCHAR(500),
    decided_digest VARCHAR(64),
    version INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT run_completion_agent_chk CHECK (agent_state IN ('none', 'reported')),
    CONSTRAINT run_completion_human_chk CHECK (human_state IN ('none', 'accepted', 'rejected')),
    -- agent-reported completion is never accepted completion: a human who is not the reporter decides
    CONSTRAINT run_completion_accept_chk CHECK (
        human_state = 'none'
        OR (agent_state = 'reported' AND decided_by IS NOT NULL AND decided_at IS NOT NULL)),
    CONSTRAINT run_completion_not_self_chk CHECK (
        human_state <> 'accepted' OR reported_by IS NULL OR decided_by <> reported_by)
);

CREATE TABLE IF NOT EXISTS run_completion_history (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL,
    report_no INTEGER NOT NULL,
    event VARCHAR(8) NOT NULL,
    actor_user_id VARCHAR(36) NOT NULL,
    digest VARCHAR(64),
    reason VARCHAR(500),
    request_id VARCHAR(64),
    at TIMESTAMPTZ NOT NULL,
    CONSTRAINT run_completion_history_event_chk CHECK (event IN ('reported', 'accepted', 'rejected'))
);

CREATE INDEX IF NOT EXISTS run_completion_history_run ON run_completion_history (run_id, at, id);

-- A retried report with the same request id is one report.
CREATE UNIQUE INDEX IF NOT EXISTS run_completion_history_request
    ON run_completion_history (run_id, actor_user_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS run_attempts (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    kind VARCHAR(8) NOT NULL,
    state VARCHAR(12) NOT NULL,
    reason VARCHAR(500),
    request_id VARCHAR(64),
    requested_by VARCHAR(36) NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    from_state VARCHAR(40),
    from_event_seq BIGINT,
    from_event_type VARCHAR(60),
    error_code VARCHAR(40),
    preserved TEXT,
    resolved_at TIMESTAMPTZ,
    CONSTRAINT run_attempts_kind_chk CHECK (kind IN ('retry', 'abandon')),
    CONSTRAINT run_attempts_state_chk CHECK (
        state IN ('requested', 'resumed', 'start_failed', 'abandoned', 'failed')),
    CONSTRAINT run_attempts_reason_chk CHECK (kind <> 'abandon' OR reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS run_attempts_run ON run_attempts (run_id, attempt_no, requested_at);

-- One row per retry attempt number.
CREATE UNIQUE INDEX IF NOT EXISTS run_attempts_retry_no
    ON run_attempts (run_id, attempt_no) WHERE kind = 'retry';

-- At most one retry may be resuming a run at a time: the race backstop for "a previous attempt still holds the run".
CREATE UNIQUE INDEX IF NOT EXISTS run_attempts_one_in_flight
    ON run_attempts (run_id) WHERE kind = 'retry' AND state = 'requested';

-- A run is abandoned or failed at most once.
CREATE UNIQUE INDEX IF NOT EXISTS run_attempts_one_abandon
    ON run_attempts (run_id) WHERE kind = 'abandon';

-- A retried request with the same request id is one attempt.
CREATE UNIQUE INDEX IF NOT EXISTS run_attempts_request
    ON run_attempts (run_id, requested_by, request_id) WHERE request_id IS NOT NULL;
