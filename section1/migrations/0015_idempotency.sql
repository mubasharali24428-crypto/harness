-- M12-W2.1 lane F1 (H-020): DB-backed Idempotency-Key replay cache.
-- One row per (route template | principal digest | key) fingerprint. The
-- envelope_middleware reserves the row (status_code = 0 sentinel) before the
-- handler runs and stores status+body after success, so concurrent replays
-- collapse onto a single execution. TTL/cleanup sweep is Wave 3's job;
-- expires_at + its index exist so that sweep is an index-only DELETE.
-- Purely additive: no FKs, no backfills, nothing existing touches this table.
CREATE TABLE idempotency_keys (
    fingerprint TEXT PRIMARY KEY,
    status_code INTEGER NOT NULL DEFAULT 0,
    response_body JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '24 hours'
);
CREATE INDEX idx_idempotency_keys_expires_at ON idempotency_keys (expires_at);
