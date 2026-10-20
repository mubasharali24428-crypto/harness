-- 0058: idempotency keys for GitHub writes that create external objects (issues).
-- A key is reserved before the call to GitHub and stores the response after it, so a
-- retried request returns the first result instead of creating a second issue.
CREATE TABLE IF NOT EXISTS github_write_keys (
    project_id VARCHAR(36) NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    idem_key VARCHAR(200) NOT NULL,
    operation VARCHAR(40) NOT NULL,
    result TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (project_id, idem_key)
);
