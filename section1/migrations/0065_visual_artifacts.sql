-- 0065: visual evidence (W9). A screenshot is an artifact PLUS the reviewed revision it
-- evidences and its place in a before/after pair. The artifacts table keeps the bytes, size,
-- retention and share rules; this table adds the visual semantics: role, pair, pixel
-- dimensions and the provenance snapshot (run head sha, base sha, approved plan version,
-- changes digest) taken when the image was attached. Rows are never updated: a new capture
-- is a new row, so evidence cannot be rewritten in place.
-- The pair is explicit (`after.pair_id -> before.artifact_id`), one after per before (unique
-- partial index), and both must share a provenance snapshot (enforced by the API).
-- Portable SQL, no dollar quotes, no transaction control, no semicolons in comments.
-- Rollback: DROP TABLE visual_artifacts (product data only, audit_log untouched).

CREATE TABLE IF NOT EXISTS visual_artifacts (
    artifact_id VARCHAR(36) PRIMARY KEY,
    team_id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    role VARCHAR(8) NOT NULL,
    pair_id VARCHAR(36),
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    provenance TEXT NOT NULL,
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT visual_artifacts_role_chk CHECK (role IN ('before', 'after')),
    CONSTRAINT visual_artifacts_pair_chk CHECK (
        (role = 'before' AND pair_id IS NULL) OR (role = 'after' AND pair_id IS NOT NULL)
    ),
    CONSTRAINT visual_artifacts_px_chk CHECK (width > 0 AND height > 0)
);

CREATE INDEX IF NOT EXISTS visual_artifacts_run
    ON visual_artifacts (team_id, run_id, created_at);

-- One "after" per "before": a paired comparison has exactly one result image.
CREATE UNIQUE INDEX IF NOT EXISTS visual_artifacts_after_once
    ON visual_artifacts (pair_id) WHERE role = 'after';
