-- 0066: team skills (W12). A skill is a reviewed Markdown instruction bundle: text that
-- advisors prompt assembly, never capability. The W6 prototype proved the rules on the
-- filesystem (manifest allow-list, digest pin, team scope, draft/reviewed/disabled); this
-- table is the same rules in Section 1 storage, served by skills_api and read by the
-- orchestrator's skills_loader at plan-proposal time.
--
-- Semantics:
-- - One row per (team_id, name); versions are strings, re-created rows start as draft.
-- - The digest (pinned_sha256) covers the canonical content: {name, version, description,
--   instructions, source, author, teamId}. Section 1 computes it on create/review; the
--   orchestrator recomputes it before injecting and silently skips a mismatch. The pin
--   never moves on its own — review() rewrites it, editing content does not.
-- - status: draft (never injected), reviewed (injectable when enabled), disabled
--   (reviewed but switched off). expires_at is optional; an expired skill is not injected
--   and can be deleted.
-- - There is no column for a command, script, hook, env, tool or permission. Unknown
--   manifest keys are refused by the API, so a skill cannot smuggle capability in.
-- Portable SQL, no dollar quotes, no transaction control, no semicolons in comments.
-- Rollback: DROP TABLE team_skills (product data only, audit_log untouched).

CREATE TABLE IF NOT EXISTS team_skills (
    id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    name VARCHAR(32) NOT NULL,
    version VARCHAR(16) NOT NULL,
    description VARCHAR(500) NOT NULL,
    instructions TEXT NOT NULL,
    provenance_source VARCHAR(200) NOT NULL,
    provenance_author VARCHAR(200) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    pinned_sha256 VARCHAR(64),
    expires_at TIMESTAMPTZ,
    created_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT team_skills_status_chk CHECK (status IN ('draft', 'reviewed', 'disabled')),
    CONSTRAINT team_skills_version_chk CHECK (version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    CONSTRAINT team_skills_name_chk CHECK (CHAR_LENGTH(name) BETWEEN 2 AND 32),
    CONSTRAINT team_skills_desc_chk CHECK (CHAR_LENGTH(description) BETWEEN 1 AND 500),
    CONSTRAINT team_skills_instr_chk CHECK (CHAR_LENGTH(instructions) <= 16384),
    UNIQUE (team_id, name)
);

CREATE INDEX IF NOT EXISTS team_skills_team ON team_skills (team_id, status, enabled);
