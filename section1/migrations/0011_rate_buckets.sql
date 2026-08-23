-- Mission 10 node B5: fixed-window rate-limit buckets guarding the auth edge
-- (/api/v1/auth/login + /api/v1/auth/otp/request). One row per bucket_key
-- ("<client-ip>|<route-template>"); harness_identity.ratelimit.py upserts the
-- counter in a single atomic INSERT .. ON CONFLICT DO UPDATE .. RETURNING
-- statement, resetting the count whenever the stored window no longer matches
-- the current fixed window. Purely additive: no FKs, no backfills.
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER DEFAULT 0 NOT NULL
);
CREATE INDEX idx_rate_buckets_window_start ON rate_buckets (window_start);
