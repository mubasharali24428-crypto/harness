-- 0060 (Part 49.003, session 12): a team admin chooses each webhook's payload format. 'harness' is the
-- signed id-only JSON envelope; 'slack' is a one-line chat message built from the same ids.
ALTER TABLE integration_webhooks
    ADD COLUMN IF NOT EXISTS payload_format VARCHAR(12) NOT NULL DEFAULT 'harness';
DO $$ BEGIN
    ALTER TABLE integration_webhooks
        ADD CONSTRAINT ck_integration_webhooks_payload_format CHECK (payload_format IN ('harness', 'slack'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
