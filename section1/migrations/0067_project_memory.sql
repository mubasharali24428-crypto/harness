-- 0067: project memory (W12). User-authored, project-scoped advisory notes that the
-- orchestrator injects at plan-proposal time next to project rules — nothing more. They
-- are explicitly NOT instructions: the rendered block states they cannot override rules,
-- the approved scope, or any policy. Section 5 remains the only enforcement layer.
--
-- Semantics:
-- - One row per note; a note belongs to exactly one project (team scoping follows the
--   project's team; a foreign project answers 404 like project_rules does).
-- - status: draft (visible to the team in the API, never injected) -> reviewed (injected).
--   review is an explicit admin act recorded in reviewed_by/reviewed_at.
-- - source: 'user' for the minimal slice (API-authored); 'run' is reserved for a future
--   lane that would let a completed run propose memory, which must pass its own review.
-- - expires_at is optional; expired notes are not injected and can be deleted.
-- - memory_text is capped at 16 KiB by the API; the CHECK is defense in depth.
-- Portable SQL, no dollar quotes, no transaction control, no semicolons in comments.
-- Rollback: DROP TABLE project_memory (product data only, audit_log untouched).

CREATE TABLE IF NOT EXISTS project_memory (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    memory_text TEXT NOT NULL,
    source VARCHAR(16) NOT NULL DEFAULT 'user',
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    reviewed_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_by VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT project_memory_source_chk CHECK (source IN ('user', 'run')),
    CONSTRAINT project_memory_status_chk CHECK (status IN ('draft', 'reviewed')),
    CONSTRAINT project_memory_text_chk CHECK (CHAR_LENGTH(memory_text) <= 16384)
);

CREATE INDEX IF NOT EXISTS project_memory_project ON project_memory (project_id, status);
