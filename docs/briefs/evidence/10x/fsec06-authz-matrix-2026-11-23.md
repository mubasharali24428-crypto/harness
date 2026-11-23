# F-SEC-06 closed — PG-gated authz matrix is green unskipped (2026-11-23)

Revision: `w28-s1-delegate` working tree on HEAD `ab29e8c9` + the Wave A edits.
Finding: `docs/briefs/evidence/security/findings-ledger.md` F-SEC-06 (opened 2026-11-22
by the first routine `make test-all`; 1 failed / 1354 passed).

## What was actually wrong

Two independent classes, neither a cross-tenant leak.

**Class 1 — probe-token poisoning (the org-route 401s).** The walk probed
`DELETE /api/v1/orgs/me/identity` with each actor's *real* probe token. That route is
self-service account deletion: it deactivates the caller (`users.is_active=false`),
deletes every session and every team seat they hold (`orgs.py:466-500`,
`_revoke_access(..., everywhere=True)`). After the admin/engineer/viewer passes, every
later `/api/v1/orgs/{org_id}/…` request from that actor correctly answered
`401 invalid or expired session` — the walk had deleted its own principals. Reproduced
by running the same probe order with the route skipped: all org routes answer 404/403
by design instead of 401. This is fixture-vs-route drift, not an authz gap; the 401s
were the session layer working as specified.

**Class 2 — stale pins on scoped-query reads.** Five kinds of GET answer `200` with the
foreign admin's *own* (empty) rows because the SQL is scoped to the caller, not to the
path: `/agents` (`agents.py:287 WHERE team_id=:t`), `/notifications`,
`/notifications/unread-count`, `/notifications/preferences` (`team_id+user_id`),
`/sessions`, `/sessions/templates`, `/workspaces`, `/workspaces/allowlist`,
`/workspaces/templates` (`WHERE team_id=:t`), and `/orgs` (`WHERE m.user_id=:u`).
`POST /notifications/read-all` is the same shape for writes
(`WHERE team_id=:t AND user_id=:u`). The old "no 2xx for foreign" pin described a
different design; the design in code is own-data-scoped, and 200+empty is the honest
answer. No row of another tenant was returned in any of these responses.

Also re-pinned: `/api/v1/orgs/service/whoami` is a service-identity door
(`Bearer hsi_…`); a user bearer gets `401 invalid service identity token` by design,
the same law as the runner and SCIM doors.

## The fix (`section1/tests/test_authz_matrix.py`)

- `SELF_DELETING` class for `/orgs/me/identity`, probed with **throwaway twins** (one
  independent user+seat+session per actor) so the walk can exercise the real route
  without consuming its own principals.
- `SERVICE_DOORS` class for the service-identity door (no 2xx for a user bearer).
- `own_data` extended to the team/user-scoped reads above; new `own_data_write` class
  for `read-all` (any role may mark its own inbox read).
- `self_delete` pins: anonymous 401, guest deny-by-default, any member 204-or-honest-4xx
  and never 401/5xx.

## Evidence

| Run | Command | Result |
|---|---|---|
| Matrix, real PG (the failing test) | `HARNESS_TEST_PG=1 … pytest tests/test_authz_matrix.py -q` | **3 passed** in 6.41s |
| Matrix, default sqlite lane | `pytest tests/test_authz_matrix.py -q` | **3 passed** in 5.94s |
| Section 1 full suite, real PG | `HARNESS_TEST_PG=1 … pytest tests/ -q` | **1351 passed, 1 failed, 3 errors** in 165.71s |

The four non-passing rows are all one environmental class: they `docker exec` into
`harness-db-1`, and this session has no Docker daemon (`test_fk_deletion_story.py:629`
"docker container harness-db-1 not reachable"; `test_runs_index_tuning.py:551`
`docker exec` refused). Test-count reconciliation is exact: 1355 tests total in both
runs; the recorded X-02 run had Docker up (so those four passed) and the matrix red
(1354 passed / 1 failed). This run has Docker down (four docker rows red) and the
matrix green — **zero authz-matrix failures**, and no change to any other section-1
test.

Lint: `ruff check tests/test_authz_matrix.py` clean.

## What this does not prove

- The matrix runs against a throwaway migrated schema at HEAD, not against the deployed
  `vv90r6` service; the rubric's "deployed surface" condition for G-C stays open
  (and F-SEC-05's redeploy is still the owner's action).
- Own-data 200s are proven by code scope and by the fixtures' empty foreign result;
  this suite does not enumerate every row of a populated foreign team. The existing
  R-01 cross-tenant suites remain the row-level proof.
- The org-route 401s are explained and eliminated as a probe artifact; no product
  change was made, because the product behaviour was correct.
