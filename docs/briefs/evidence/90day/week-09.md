# Week 09 — P-17 client trust portal (I-01): engineering delivered and independently verified

Opened 2026-11-21 (executed ahead of the calendar week; the plan's W9 window is
Jan 6–12, 2027). Base: branch `w28-s1-delegate` at `2ddf03b`. Engineering only,
offline, zero spend.

Plan row: P-17 (W9, S+B): "I-01 client-facing trust portal: read-only auditor
view for the partner's end-client (every change, approver, test evidence, cost).
Acceptance: Partner's end-client completes a review session; cross-member
negative tests pass."

## 1. Scope and states

| Work | Role | State | Evidence |
|---|---|---|---|
| P-17 portal build (guest role, `project_guest_grants` migration 0076, guest gate in both principal resolvers, portal routes, UI) | S+B | **Shipped — independently verified 8/8** | `docs/briefs/evidence/90day/p17-verification-result.md` (§6 attack list held; 8/8 checks table); landed with the Nov 13–16 batch, TS dist rebuilt in `c286d22` after check-ts-dist caught the stale dist |
| Cross-member negative tests | S+B | **Pass** | `section1/tests/test_portal_guest.py` (18 tests) incl. `test_gate_path_shape_is_exact` and repeated-query reading; contracts guest-gate tests |
| End-client completes a review session | R / owner | **Not met — owner-gated** | Acceptance is a live partner session; needs the vv90r5 redeploy (runbook ready) and a consenting partner |
| P-16 (convert pilot → paid, test transaction) | R / owner | **Not started — owner-gated** | Payment-rail decision P-D2 is the owner's; billing stays flag-off |

## 2. Notes

- The guest gate is default-deny 403 `GUEST_PORTAL_ONLY` outside the granted
  route list; regexes anchor with `\Z` (not `$` — re's `$` matches before a
  trailing newline); Starlette `QueryParams.get` returns the LAST repeated
  value, which the repeated-query test pins.
- The deployed RC still runs pre-P-17 code (schema 0075). The vv90r5 one-tag-set
  redeploy (owner runbook `a06-redeploy-runbook-vv90r5.md`) lands 0076 + the
  portal together; after it, flip the week-03 drift finding to resolved.
