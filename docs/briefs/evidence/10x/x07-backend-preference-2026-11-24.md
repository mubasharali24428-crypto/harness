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

## Sprint 2 Task 18 — blind-approval KPI (2026-11-24)

**Definition.** *Blind approval* = a PR approval whose `viewedDetail` is `false`, i.e. the diff was
never on screen before Approve (the run-page review pane measures this with an
IntersectionObserver at 25% visibility and sends `viewed_detail`). Approvals without the
signal (the standalone `/review` page, API callers, rows before today) count as **not measured**,
never as blind. **Rate** = blind / measured approvals, `null` ("insufficient data") below 10.

| Claim | Status | Evidence |
|---|---|---|
| Approval audit rows carry `teamId`, `rendering`, `viewedDetail` | **Verified (unit)** | `section8/tests/unit/test_decision_context.py`; `make test-section8` 108 passed |
| `GET /api/v1/review-kpi/blind-approval` — admin only, team-scoped, counts only (no user ids) | **Verified (unit)** | `test_kpi_reads_audit_rows_for_the_team_only`, `test_kpi_splits_by_rendering_and_never_counts_unknown_as_blind` |
| Admin tile on `/dashboard/cost` | **Verified (static render)** | `section7/app/dashboard/blindApproval.test.tsx` |
| Real rate on pilot tasks | **Not measured** — no pilot reviews yet; trend not tracked; no 5-minute cache (bounded 500-row scan) |

## Sprint 5 Tasks 31–32 — collab approvals enter the KPI (2026-11-25)

Closes the seam gap documented in `docs/briefs/evidence/sprint4/task-27-collab-viewed-detail.md`
(G-X07-3). No second audit store, no section4→section8 import: section4 writes the same three
fields onto its existing `approval.approved` audit row, and section8's KPI reads one more action pair
from the same `audit_log`.

| Claim | Status | Evidence |
|---|---|---|
| `POST /runs/{runId}/approvals/{approvalId}/decision` accepts optional `viewed_detail` (bool \| null), threaded to `collab.decide_approval`; never changes the decision | **Verified (unit)** | Task 31 — `section4/tests/unit/test_collab_viewed_detail.py`; spec + Python/TS SDK + TS dist regenerated, `make check-ts-dist` clean |
| `approval.approved` audit row carries `teamId`, `rendering` (approver's `team_members.approval_rendering`, fail-open `unknown`) and `viewedDetail` (`null` when not measured) | **Verified (unit, SQLite)** | Task 32 — same file, 13/13 |
| `approval.rejected` carries `rendering` (denominator), no `viewedDetail` (same shape as `review.changes_requested`) | **Verified (unit)** | `test_reject_records_rendering_but_no_viewed_signal` |
| Rendering lookup runs in its own short session before the decision transaction, so a pre-0077 schema (or any error) cannot abort the approval on Postgres | **Verified (SQLite)**; PG path reasoned, not run (no PG in cloud) | `test_pre_0077_schema_is_unknown_and_the_approval_still_lands` |
| Collaborator event (`run_collab_events`) does not carry the approver's rendering or attention signal | **Verified (unit)** | `test_signal_and_rendering_stay_off_the_collaborator_event` |
| KPI counts `approval.approved` exactly like `review.approved`, `approval.rejected` like `review.changes_requested`; other `approval.*` lifecycle rows are ignored | **Verified (unit)** | `section8/tests/unit/test_decision_context.py` — 3 new tests; `make test-section8` 111 passed |
| Collab approvals written before Task 32 (teamId, no `viewedDetail`) count as **unknown**, never blind | **Verified (unit)** | `test_collab_rows_before_task_32_are_unknown_never_blind` |
| Live blind-KPI fetch against the compose stack | **Not run** — Task 33, owner Mac (no Docker in cloud) |

**Delta to the KPI response:** shape unchanged (`window`, `minSample`, `overall`, `byRendering`).
Counts now include shared-run collab decisions, still inside the same newest-500-row window, which
is now shared by both surfaces (ponytail note in `kpi.py` unchanged: add a team column/index if
volume makes it slow). No per-surface split is reported; add one only if the pilot needs it.
