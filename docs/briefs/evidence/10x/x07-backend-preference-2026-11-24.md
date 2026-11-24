# X-07 backend slice — per-member approval rendering preference (2026-11-24)

Revision: working tree on `w28-s1-delegate` at HEAD `c360d14d` plus this change.
States follow `FUTURE PLAN 2.0.md` §0 — this is an **incremental slice**, not X-07 done.

## What landed

| Piece | State | Evidence |
|---|---|---|
| Migration `0077_approval_rendering.sql` — `team_members.approval_rendering` (`technical\|plain\|decision`, default `technical`) + CHECK | **Verified** | `migrate_selftest PASS: 75 migrations (0001→0077) applied clean on real PG; re-run idempotent` (PostgreSQL 18.4, 2026-11-24) |
| `GET/PUT /api/v1/me/rendering` — self-service for the caller's own row; any role | **Verified** | `section1/tests/test_approval_rendering.py` 5/5; policy-gate classification test |
| `GET/PUT /api/v1/teams/{team_id}/members/{user_id}/rendering` — admin sets it for a member (X-D1) | **Verified** | same 5/5; engineer/viewer 403, foreign team 404 without oracle |
| `member.rendering_changed` audit row with before/after/setBy; no-op PUT silent | **Verified** | same 5/5 (exactly 2 rows for 3 PUTs, one a no-op) |
| Policy classification: self door `read` (same self-service door as `DELETE /orgs/me/identity`), admin door `administer` | **Verified** | `section1/tests/test_policy_gate.py` 7/7 incl. the new classification test |
| Identity spec + Python/TS SDKs + TS dist | **Verified** | `make check-ts-dist` — 26 tracked files match a fresh build; `spec_drift.py` clean; `static-gates` clean |
| Ruff / whitespace | **Verified** | `ruff check` clean on every touched Python file; `git diff --check` clean |
| Full section1 suite (SQLite default) | **Verified** | `1299 passed, 62 skipped` in 126s, exit 0 (2026-11-24) |
| Authz matrix (the new route is in its own-data set) | **Verified** | `3/3` on live PostgreSQL 18 and `3/3` on sqlite (2026-11-24) |

## Honest limits

- **X-07 is not complete.** `section7` has no approval card yet: the three renderings
  (Technical / Plain / Decision card), deterministic template snapshot tests, the
  keyboard-only + axe-clean journey, and the blind-approval KPI are all still open.
  The backend preference is the seam those surfaces will read.
- The preference is presentation-only by construction: the route touches one
  `team_members` row, and the policy gate still decides disclosure. A rendering
  value cannot widen access or change the computed risk tier.
- The org-policy gate path is covered by classification tests; the full org-linked
  end-to-end viewer/guest walk is not in this slice (the route tests run without an org).
- The live-service spec fetch had dropped `/health?deep=0` (the runner's duplicate
  `/health` shadowed the app route). That hunk was reverted; the spec/SDK keep the
  app's `deep` parameter. The duplicate-operation-ID warning in `deploy/runners/services.py`
  is a pre-existing spec-generation defect, not part of X-07.

## Remaining for X-07

1. `section7` approval card with the three renderings and a profile-based default.
2. Template snapshot tests; keyboard-only and axe-clean journeys.
3. Blind-approval-rate instrumentation (the standing KPI) — only meaningful once the
   risk-tiered envelopes (X-08, P-D6/D-U4) land.

## Update 2026-11-24 (later) — Tasks 4 + 5: Plain sentence, Decision-card grid (X-21), KPI split

| Claim | Status | Evidence |
|---|---|---|
| Deterministic Plain sentence + X-21 grid computed from action + paths only (`section4/src/harness_orchestrator/approval_copy.py`); unknown action/paths say "unknown" | **Verified** | `section4/tests/unit/test_approval_copy.py` 7/7 — golden read / write / rm / network / `.env` |
| Every collab approval carries `plain` + `decisionGrid` beside the unchanged technical fields | **Verified** | `test_collab_backend.py::test_approvals_carry_plain_sentence_and_grid_beside_technical` |
| section7 card picks the view from `GET /api/v1/me/rendering` (failure -> technical); grid's dominant band precedes Approve in DOM order; every cell and column is text-labelled | **Verified (static render)** | `section7/.../approvalRendering.test.tsx` 3/3; full vitest 1208/1208; `tsc --noEmit` clean |
| Reviewer's rendering recorded on `review.approved` / `review.changes_requested` audit rows (blind-approval denominator, fail-open `unknown`) | **Verified** | `section8/tests/unit/test_decision_context.py` 2/2 + conformance assertion; `make test-section8` 106 passed |
| Keyboard-only + axe journey; no-scroll at 1280 / 375 px | **Verified (browser, mocked API)** — Sprint 2 Task 11 commit | `section7/e2e/approval-rendering.e2e.ts` 6/6 in Chromium; `docs/briefs/evidence/sprint2/task-11-browser.md`. Live-stack round-trip still not run (Docker down) |
| Grid approves anything | **No** — P-D6 envelopes not enabled; display only |
