-- 0073: BYO runner job attempts (M2-W13 lost-lease re-dispatch).
--
-- Why: `runner_jobs` was keyed by `run_id` alone, so a run could be offered to a
-- runner exactly once. When a remote host died mid-command its lease expired and
-- the run was ORPHANED (correctly - the server must not re-execute work the host
-- may still be running), but there was no way to record a second offer: the new
-- row collided with the dead one. An orphaned BYO run could therefore never be
-- resumed by a human, only abandoned.
--
-- The key becomes `(run_id, attempt)`. Attempt 1 is the original offer; a human
-- re-dispatch writes attempt 2, 3, ... and each attempt keeps its own terminal
-- state, so the log of *who was offered what, and what became of it* survives.
--
-- Semantics that stay true:
-- - At most ONE attempt per run may be live (offered/leased/running) at a time:
--   the API only re-offers a run whose claim the previous attempt lost, i.e. the
--   claim state is `orphaned`.
-- - The lease itself is still the single `run_claims` row. A re-dispatch puts it
--   back to `queued` so the next attempt can claim it; nothing else about the
--   queue changes (`claim_job` still requires state='queued').
-- - The verdict is re-derived from the *current* attempt's evidence: the run's
--   max seq at re-offer time is stored in `evidence_from_seq`, so a partial
--   failure left by the dead host cannot poison the retry's settlement, and the
--   retry cannot claim credit for the dead host's passes either.
-- - SQLite has no in-place "drop the primary key", so the table is rebuilt and
--   copied rather than altered. The old index is dropped with its table, so the
--   name is free when it is recreated after the rename.
--
-- Rollback: rebuild the table without `attempt` after DELETEing every attempt > 1
-- (that loses re-dispatch history, which is the point of the column).

CREATE TABLE IF NOT EXISTS runner_jobs_v2 (
    run_id VARCHAR(36) NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    runner_id VARCHAR(36) NOT NULL REFERENCES team_runners (id) ON DELETE CASCADE,
    project_id VARCHAR(36) NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    state VARCHAR(16) NOT NULL DEFAULT 'offered',
    lease_expires_at TIMESTAMPTZ,
    result_digest VARCHAR(64),
    evidence_from_seq BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, attempt),
    CONSTRAINT runner_jobs_state_chk CHECK (
        state IN ('offered', 'leased', 'running', 'completed', 'failed', 'released')
    )
);

INSERT INTO runner_jobs_v2
    (run_id, attempt, runner_id, project_id, state, lease_expires_at, result_digest,
     evidence_from_seq, created_at, updated_at)
SELECT run_id, 1, runner_id, project_id, state, lease_expires_at, result_digest,
       0, created_at, updated_at
FROM runner_jobs;

DROP TABLE runner_jobs;

ALTER TABLE runner_jobs_v2 RENAME TO runner_jobs;

CREATE INDEX IF NOT EXISTS runner_jobs_runner ON runner_jobs (runner_id, state);
