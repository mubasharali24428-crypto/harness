-- W28 S1b: a delegated assignment points at the child run that executes it.
-- Nullable: plain spawned assignments (no run of their own) keep NULL.
ALTER TABLE agent_assignments ADD COLUMN child_run_id VARCHAR(36);
