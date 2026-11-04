-- 0068: bring-your-own runners (W13). A runner is a customer-controlled host that
-- executes approved work outside the harness deployment. The harness keeps plan
-- approval, policy, metering and audit; the customer keeps the machine.
--
-- Semantics:
-- - A runner belongs to one team. The bearer token is shown once at registration and
--   only its sha256 is stored; revocation is a row update, not a token edit.
-- - The attestation key is HMAC-SHA256(token, ATTESTATION_KEY_LABEL), stored as a
--   one-way derivative. It verifies that the holder of the token signed the claimed
--   confinement facts; it does NOT prove host integrity. That trust boundary is
--   documented in docs/m2-w13-plan.md.
-- - Nonces are single-use per runner inside the accepted clock skew window. A
--   replayed request is refused before any job state is read or written.
-- - runner_jobs deliberately has NO FK to run_claims or agent_runs: the durable
--   queue creates claims lazily (0006), so a job can be offered before the run's
--   first persisted event. project_id and runner_id carry the ownership FKs.
-- - There is no column for a command, tool, path or permission: a runner brings a
--   host, never capability. Job assignment is an admin act on an already-approved run.
-- Portable SQL, no dollar quotes, no transaction control, no semicolons in comments.
-- Rollback: DROP TABLE runner_jobs, runner_nonces, team_runners (product data only,
-- audit_log untouched).

CREATE TABLE IF NOT EXISTS team_runners (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    name VARCHAR(40) NOT NULL,
    description VARCHAR(500) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    token_sha256 VARCHAR(64) NOT NULL,
    token_hint VARCHAR(8) NOT NULL,
    attestation_key VARCHAR(64) NOT NULL,
    confinement VARCHAR(16) NOT NULL DEFAULT 'none',
    attestation_json TEXT,
    attestation_sha256 VARCHAR(64),
    attestation_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    created_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    revoked_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT team_runners_status_chk CHECK (status IN ('active', 'revoked')),
    CONSTRAINT team_runners_conf_chk CHECK (confinement IN ('docker', 'process', 'none')),
    CONSTRAINT team_runners_name_chk CHECK (CHAR_LENGTH(name) BETWEEN 2 AND 40),
    CONSTRAINT team_runners_desc_chk CHECK (CHAR_LENGTH(description) BETWEEN 1 AND 500),
    CONSTRAINT team_runners_att_chk CHECK (
        attestation_json IS NULL OR CHAR_LENGTH(attestation_json) <= 8192
    ),
    UNIQUE (team_id, name),
    UNIQUE (token_sha256)
);

CREATE INDEX IF NOT EXISTS team_runners_team ON team_runners (team_id, status);

CREATE TABLE IF NOT EXISTS runner_nonces (
    runner_id VARCHAR(36) NOT NULL REFERENCES team_runners (id) ON DELETE CASCADE,
    nonce VARCHAR(64) NOT NULL,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (runner_id, nonce)
);

CREATE INDEX IF NOT EXISTS runner_nonces_seen ON runner_nonces (seen_at);

CREATE TABLE IF NOT EXISTS runner_jobs (
    run_id VARCHAR(36) PRIMARY KEY,
    runner_id VARCHAR(36) NOT NULL REFERENCES team_runners (id) ON DELETE CASCADE,
    project_id VARCHAR(36) NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    state VARCHAR(16) NOT NULL DEFAULT 'offered',
    lease_expires_at TIMESTAMPTZ,
    result_digest VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT runner_jobs_state_chk CHECK (
        state IN ('offered', 'leased', 'running', 'completed', 'failed', 'released')
    )
);

CREATE INDEX IF NOT EXISTS runner_jobs_runner ON runner_jobs (runner_id, state);
