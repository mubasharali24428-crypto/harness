-- Mission 12 Wave 2.2 lane G10 (Squad BETA): user-authored project rules
-- (the harness's .cursorrules equivalent — part3 gap #9, roadmap item X2,
-- first slice).
--
-- One rules TEXT per project, stored per-project (not per-team) so the
-- precedence chain "team > repo > run" (X2) can layer later slices on top of
-- this repo-level row without renumbering. The orchestrator reads this text
-- at plan-proposal time and injects it into the planner prompt as an
-- ADVISORY "PROJECT RULES (user-authored)" block; hard constraints stay with
-- Section 5 (sandbox allow-list + forbidden_paths), which this content can
-- never bypass.
--
-- Semantics:
-- - project_id is UNIQUE: one live rules document per project. PUT upserts,
--   DELETE removes the row entirely ("no rules configured").
-- - rules_text is capped at 65536 characters (64KB) by the API layer;
--   the CHECK is defense in depth so no other writer can exceed it either.
-- - updated_by records who last saved (nullable for seeded/legacy rows).
-- - Purely additive: no frozen column or table is altered.

CREATE TABLE project_rules (
	id VARCHAR(36) NOT NULL,
	project_id VARCHAR(36) NOT NULL,
	rules_text TEXT NOT NULL,
	updated_by VARCHAR(36),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (CHAR_LENGTH(rules_text) <= 65536),
	UNIQUE (project_id),
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
	FOREIGN KEY(updated_by) REFERENCES users (id)
);

CREATE INDEX ix_project_rules_project_id ON project_rules (project_id);
