-- migration 0014: agent_runs.pr_id (M12-W2 lane E4a, pair contract W2-C2)
--
-- The PR-open producer (Section 2 openDraftPR) persists the opened PR against
-- its run so the review flow can resolve run -> prId without replaying events.
-- Nullable until that run opens a PR; NULL means "no PR yet", never "unknown".
--
-- Numbering note: 0013_plan_version.sql belongs to lane F2 (optimistic
-- concurrency) per docs/briefs/mission12-wave2-graph.md; this file takes 0014.
ALTER TABLE agent_runs ADD COLUMN pr_id VARCHAR(36);

ALTER TABLE agent_runs
    ADD CONSTRAINT fk_agent_runs_pr_id
    FOREIGN KEY (pr_id) REFERENCES pull_requests (id);
