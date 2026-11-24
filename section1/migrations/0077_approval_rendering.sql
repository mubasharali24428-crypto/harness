-- X-07 (10X Wave B) approval rendering by reader: per-member rendering preference.
-- The member self-selects their own rendering; an admin may set it for a member
-- (X-D1). The preference is a PRESENTATION choice only: it never changes what the
-- policy engine discloses, computes as risk, or gates — see
-- harness_identity/approval_rendering.py (GET/PUT /api/v1/me/rendering and the
-- admin door /api/v1/teams/{team_id}/members/{user_id}/rendering) and the
-- section7 approval card that reads it.
-- Values: technical | plain | decision (default technical = the historical rendering).
ALTER TABLE team_members ADD COLUMN approval_rendering VARCHAR(16) NOT NULL DEFAULT 'technical';
ALTER TABLE team_members ADD CONSTRAINT approval_rendering_check
    CHECK (approval_rendering IN ('technical', 'plain', 'decision'));
