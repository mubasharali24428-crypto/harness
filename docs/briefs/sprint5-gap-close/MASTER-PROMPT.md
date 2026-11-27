# Sprint 5 gap-close + PR 27 hardening — cloud master prompt

**Repo:** https://github.com/mubasharali24428-crypto/harness (public)  
**Base:** PR **#27**, branch `w28-s2-sprints1-4`, start from **latest pushed SHA** (Task 40 landed as `326b01f` or later).  
**Read first:** `docs/briefs/evidence/sprint5/END-report-2026-11-24.md`, `task-36-live-invite-revoke.md`, `mac-gate-a55ba75f.md`.

## Verdict constraints (do not violate)

- Product verdict stays **NOT READY** unless owner explicitly moves a 10.11 line (you cannot).
- **Do not** enable payment, global P-D6, or P-D7 production paths.
- **Do not** fake live PASS for Playwright without Docker evidence.
- **Do not** rewrite audit rows to null `actor_member_id` (append-only audit law).
- One task = one commit: `Task <N>: <short title>.` Push after each task.
- OpenAPI change → `make check-ts-dist` + `contracts/ts/generate.py --check` + `make static-gates`.

## Owner decisions (already chosen for this sprint)

| ID | Decision |
|----|----------|
| **D2** | **Soft-remove memberships** — add `removed_at` (or equivalent), filter active members everywhere auth/roster cares; `remove_member` sets timestamp instead of hard DELETE when audit FK would block; return 204 as today. |
| **D3** | Add **`userId`** to roster items (`MemberItem` / OpenAPI / generated TS); team settings UI must call DELETE/PATCH with **user id**, not membership id. |

## Model routing (use exactly this)

| Model | Tasks | Why |
|-------|-------|-----|
| **Claude Sonnet** | 41, 44, 45, 47, 48, 49 | Contract + UI + CI YAML + docs; bounded diffs. |
| **Claude Opus** | 42 only | D2 touches identity DB, auth principal, roster queries, `teams_api.remove_member`, migrations, Postgres FK behavior — needs careful design. |
| **Owner Mac (not cloud)** | 43, 46, 50 | Playwright live e2e, manifest after redeploy, final `make gate` / `test-all`. |

Do **not** use Opus for doc-only or single-file CI fixes. Do **not** use Haiku.

---

## Task 41 — D3 contract + UI (Sonnet)

**Done when:**

- `GET /teams/{id}/members` exposes `userId` per item (OpenAPI + section1 + `contracts/ts`).
- Team settings Remove / role change use `userId`.
- `make check-ts-dist`, `make static-gates`, section7 vitest for team settings pass.
- Evidence: `docs/briefs/evidence/sprint5-gap-close/task-41-d3-userId.md`.

**Hints:** `section1/src/harness_identity/invites.py` (`MemberItem`, `list_members`); section7 team API client and `/settings/team`.

---

## Task 42 — D2 soft-remove (Opus)

**Done when:**

- Migration adds nullable `removed_at` on team membership (name per existing schema conventions in `section1/migrations/`).
- `remove_member` soft-removes; active roster excludes removed rows; sessions/tokens for removed member invalidate per existing patterns (read `teams_api.py`, session/principal code).
- Hard DELETE path removed or only used when no audit FK conflict (if you keep both, document in evidence).
- Postgres unit/integration test proves member who emitted audit can be soft-removed (no 500).
- Evidence: `docs/briefs/evidence/sprint5-gap-close/task-42-d2-soft-remove.md`.

**Do not** detach or update historical `audit_log.actor_member_id`.

---

## Task 43 — Un-`test.fail` on B-09 live spec (OWNER MAC — BLOCKED in cloud)

Cloud: add evidence stub `task-43-invite-revoke-live.md` stating **BLOCKED — requires owner Mac Playwright** after 41+42.

Mac done when: `section7/e2e/invite-revoke-live.e2e.ts` has no `test.fail`; full journey PASS; screenshot optional.

---

## Task 44 — CI: `changes` job / paths-filter (Sonnet)

**Problem:** Job `which jobs does this change actually touch?` fails on PRs (paths-filter + `resolve` JSON).

**Done when:**

- Fix root cause (typical: `actions/checkout` in `changes` job needs `fetch-depth: 0` for PR diffs; or hardened empty `changes` JSON).
- Push; PR #27 `changes` job **passes**.
- Evidence: `task-44-ci-changes-job.md` with link to green run or log excerpt.

---

## Task 45 — Readiness drift (split lane)

**Facts:** `make readiness-drift` fails: manifest **0076** vs disk **0081** (`docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json`).

**Sonnet (Task 45a):** Document exact owner commands to refresh manifest after RC redeploy (`readiness_drift.py --write-manifest` per runbook); do **not** bump manifest migration fields without redeploy proof.

**Owner Mac (Task 45b — BLOCKED in cloud):** After redeploy, write manifest; CI `readiness-drift` green.

Evidence: `task-45-readiness-drift.md`.

---

## Task 46 — END report + STATUS refresh (Sonnet)

- Update `END-report-2026-11-24.md` addendum **or** `END-report-gap-close-2026-11-25.md`: Tasks 41–45, honest 43/45b BLOCKED, **GitHub Actions now public** (billing note corrected).
- `STATUS.md` paragraph: D2/D3 status, Task 36 path, CI state; verdict still **NOT READY**.
- Day90 ledger: engineering-only deltas in `docs/briefs/evidence/90day/day90-decision-ledger-2026-11-24.md` if your changes warrant a line.

---

## Task 47 — PR #27 review pack (Sonnet)

Produce `docs/briefs/evidence/sprint5-gap-close/pr-27-review.md`:

- Scope summary (Sprints 1–5 + gap-close commits).
- Owner gate checklist (still BLOCKED items).
- Merge risks (open e2e, readiness drift).
- Suggested merge order: gap-close onto PR branch → green required checks → owner review.

No merge unless owner asks in chat.

---

## Task 48 — Optional: checkpoint-1 thread (Opus, stretch)

Only if Tasks 41–44 are pushed and CI is green on static/security/frontend:

- Branch `w28-s3-checkpoint1` from gap-close SHA.
- Read `docs/briefs/evidence/90day/checkpoint1-redesign-2026-11-21.md`.
- Un-skip `integration/test_checkpoint1.py`; max ~400 LOC production per commit.
- Evidence under `docs/briefs/evidence/90day/checkpoint1-*.md`.

Skip if behind schedule — note SKIP in Task 46.

---

## Task 49 — Cloud self-test (Sonnet)

Without Docker: `make gate` if env allows; else `make static-gates`, `make check-ts-dist`, targeted pytest for section1 teams/invites.

Record in `task-49-cloud-gate.md` — honest PASS/FAIL per step.

---

## Task 50 — Owner Mac truth (not cloud)

After 41+42 on branch:

```bash
make gate
HARNESS_TEST_PG=1 TEST_ALL_DOCKER=1 make test-all   # if Docker up
cd section7 && npx playwright test e2e/invite-revoke-live.e2e.ts --retries=0
```

Append results to `mac-gate-<shortsha>.md`.

---

## Anti-patterns

No second audit DB; no KPI blind counting; no merging PR 27 from cloud; no payment/P-D7 toggles; no manifest migration bump without redeploy.