-- Organization data-retention windows, constrained by deployment defaults.
ALTER TABLE organizations
    ADD COLUMN event_retention_days INTEGER
        CHECK (event_retention_days IS NULL OR event_retention_days BETWEEN 1 AND 3650);
ALTER TABLE organizations
    ADD COLUMN audit_retention_days INTEGER
        CHECK (audit_retention_days IS NULL OR audit_retention_days BETWEEN 1 AND 3650);
