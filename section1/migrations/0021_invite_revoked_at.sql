-- M14-F2a: invite lifecycle status support.
--
-- Admin revoke previously DELETED the team_invites row, so the admin UI could
-- only ever see pending invites and the audit trail referenced a vanished
-- target. Revoke now stamps revoked_at instead of deleting, which lets the
-- list endpoint derive the full lifecycle status:
--   draft    -> sent_at IS NULL (not yet marked sent)
--   sent     -> unredeemed, unrevoked, unexpired, sent_at stamped
--   redeemed -> redeemed_at IS NOT NULL
--   expired  -> expires_at <= now() and never redeemed/revoked
--   revoked  -> revoked_at IS NOT NULL (covers resend-superseded tokens too)
--
-- Additive + nullable + no backfill needed: every pre-migration row is a
-- live-or-consumed invite whose new columns are legitimately NULL (mint
-- stamps sent_at going forward; legacy rows simply read as their old status).

ALTER TABLE team_invites ADD COLUMN revoked_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE team_invites ADD COLUMN sent_at TIMESTAMP WITH TIME ZONE;
