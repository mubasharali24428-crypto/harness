# Week 10 — P-19 compliance evidence pack (I-03): the one-click audit artifact

Opened 2026-11-21 (executed ahead of the calendar week; the plan's W10 window is
Jan 13–19, 2027). Base: branch `w28-s1-delegate`. Engineering only, offline,
zero spend — no partner data, no network, no provider calls.

Plan row: P-19 (W10, B+G): "I-03 compliance evidence export: audit pack from
the event log (SOC2/ISO-style AI change-management artifact). Acceptance:
Sample pack reviewed by partner; retention/permissions checks pass."

## 1. Scope and states

| Work | Role | State | Evidence |
|---|---|---|---|
| Pack builder `section1/src/harness_identity/audit_pack.py` | B | **Implemented — 5/5 tests pass** | manifest (ranges, counts, explicit truncation, retention statements), chain verdict, audit trail, collab events; §3 |
| Route `GET /api/v1/audit/pack` | B | **Implemented — permissions checks pass** | admin-only + step-up("export"), same door as `GET /audit/export`; classified `"export"` in `contracts/policy_enforce.py` (the route-classification gate `test_every_identity_route_is_classified` caught the missing line — fixed); pack generation itself audited |
| No-drift guarantee (pack rows == export rows) | B | **Pass** | both streams read through `audit_rows.read_audit_page` (the ONE team-scope reader shared with the SIEM pusher); prefix test in `section1/tests/test_audit_pack.py` |
| Honest chain verdict | B | **Pass** | on a non-PostgreSQL lane the pack records `performed: false` + reason — never `ok` from a no-op (same law as `POST /audit/verify`) |
| Spec + SDK + dist regen | B | **Done** | `identity.openapi.json` now 148 paths; clients/python + clients/ts src + dist rebuilt; `spec_drift.py` green ("specs and UI calls match the router"); `make check-ts-dist` green (26 tracked files) |
| Sample pack committed | B | **Done** | `docs/briefs/evidence/90day/p19-sample-pack.json` (scratch team, synthetic rows only) |
| Sample pack reviewed by a partner | R / owner | **Not met — owner-gated** | needs a consenting partner; the artifact is ready to send |
| P-18 (case study, publish benchmark) | R / owner | **Not started — owner-gated** | depends on the P-13 live benchmark run (spend caps preregistered) |

## 2. Laws the pack does not break

- **Truncation is said out loud.** `manifest.truncated` + delivered/available
  counts per stream; an auditor can detect a cap without trusting us.
- **Fail closed on absent streams.** A deployment without `run_collab_events`
  gets the same 503 the `stream=collab` export gives — never a fabricated
  `null` stream.
- **No new team-scope SQL.** Audit rows go through `audit_rows`; collab rows are
  a direct `team_id` column filter (the table carries the team id as a column).

## 3. Test results (run 2026-11-21, section1 venv)

- `tests/test_audit_pack.py` — 5/5 (pack assembly + no-drift + cross-team
  unreachability + self-audit + truncation honesty + bound).
- `tests/test_audit_export_ndjson.py`, `tests/test_audit_export_and_collab_push.py`
  — still green (no drift introduced).
- Full section1 suite: **1269 passed, 63 skipped** (was 1268+1 fail before the
  classification fix).
- ruff clean on all touched files (I001-only fixes applied).
