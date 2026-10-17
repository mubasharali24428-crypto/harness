# A1-S2 — Personal keys, payer consent and bounded spend

Owner A1. Features F081 F082 F087 F088 F143 F144. Risks VR018 VR041 VR045 (+ VR012/VR017 cross-cutting).

## Built (section1, namespace `/api/v1/identity`, migration `0035_personal_keys_shares.sql`)

- F081 personal BYO-key profiles: `api_keys.owner_user_id` (NULL = team key, unchanged). Personal keys are
  filtered OUT of `/api/v1/keys` list/revoke, so teammates cannot see or revoke them; Fernet vault +
  KDF re-encryption cover them automatically (same table). `POST/GET/DELETE /personal-keys`.
- F082 consent-based sharing: `key_shares` pending -> active|declined|revoked. Owner offers to a
  same-team member (cap optional, expiry default 30d); ONLY the recipient accepts; owner OR recipient
  revokes at any time; revoking the key revokes its shares. Partial unique index forbids duplicate live shares.
- Payer attribution + bounded spend: owner is payer. `POST /shares/{id}/reservations` is a single conditional
  UPDATE (`committed_usd + amount <= cap`, active, unexpired, key unrevoked); idempotent per
  `(share, idempotencyKey)`. `settle` records actuals (unknown usage => `unsettled`, hold kept; late usage
  after release/revocation still charged, flagged `late`/`overCap`). `release` returns unused holds.
  `resolve_shared_key()` is the in-process resolver for S3/S4 (re-checks consent at call time).
- F087 token forecast (`spend.predict_tokens`, `GET /spend/forecast`): empirical p50/p90 interval and
  $/token, `calibrated=false` + widened interval under 8 samples, `stale` after 14d, `available=false` with none.
- F088 anomalies (`spend.detect_spend_anomaly`, `GET /spend/anomalies`): median/MAD z-score, needs >=7 prior days
  else `insufficient_history` (never a false "normal").
- F143 usage billing: `GET /usage-statement` month usage by model + share payer exposure, reconciled
  against provider-reported `providerUsd` (`matched|discrepancy|unsettled`).
- F144 `GET /spend/alerts`: ok/warning/ceiling/unlimited from the ONE cap truth (`resolve_cap`).

## Defects found while building
- `Team` has no ORM mirror of `budget_usd` (migration 0007): an ORM select would crash; raw SQL used (as billing does).
- Money output on SQLite vs PG differed in scale; statement values now quantized to the NUMERIC(12,6) grid.

## Verification (2026-10-17)
- `section1`: `./.venv/bin/python -m pytest tests -q` -> 660 passed, 10 skipped (baseline 639/10); `ruff check src tests scripts` clean.
  13 new tests in `tests/test_personal_keys_api.py`: key isolation from team surfaces, pending/declined/stranger denial,
  non-member/self share refusal, 10 concurrent $1 reservations vs $5 cap => exactly 5 win (file-backed SQLite),
  idempotent retry / key reuse 409, revoke while queued vs in-flight, unsettled + late usage + release, expiry and
  revoked-key resolver denial, audit has no secret, endpoint smoke, pure forecast/anomaly/alert/reconcile math.
  Schema-guard tests (`test_fk_deletion_story`, `test_api_keys_routes` shape) updated for the new FKs/column (38->44).
- Real Postgres: all 35 migrations applied cleanly to a throwaway local database (then dropped).
  NOT done: concurrency on real PG (SQLite serialized the writers; the guarantee relies on a single conditional UPDATE).

## Dispositions
| F | Status |
|---|---|
| F081 | VERIFIED IMPLEMENTED (API+vault+tests); UI = NEEDS INTEGRATION (A4 section7) |
| F082 | VERIFIED IMPLEMENTED (consent/revocation); S3/S4 must call `resolve_shared_key` at provider-call time -> NEEDS INTEGRATION |
| F087 | PARTIAL: empirical forecast with honest uncertainty; not validated against real model traffic |
| F088 | PARTIAL: statistical signal only; no notification/hold action on anomaly |
| F143 | PARTIAL: statement + reconciliation logic; no live Stripe usage-record drill (NEEDS EXTERNAL VALIDATION) |
| F144 | PARTIAL: ceiling is enforced by existing S3 atomic reservation; alert reporting added; email/UI delivery = NEEDS INTEGRATION |

Risks: VR018 PARTIAL CONTROL (consent+revocation+cap enforced; no legal/UX validation). VR045 PARTIAL CONTROL
(payer attribution per share; team ledger itself still team-level). VR041 PARTIAL CONTROL (forecast+anomaly+alerts;
unit economics unmeasured). Owner/next decision: product owner on default cap/expiry policy.

## Integration proposals (non-owned)
- Section3/4 (A2): when a run uses a shared key call `resolve_shared_key(share_id,user)` then reserve/settle per call.
- A4: UI for personal keys, consent inbox, share cap display. A5: alert delivery via notification outbox.
## Rollback
Drop routers (remove include in `app.py`), then `DROP TABLE key_share_spend, key_shares; ALTER TABLE api_keys DROP COLUMN owner_user_id;` (no data in team keys is touched).
