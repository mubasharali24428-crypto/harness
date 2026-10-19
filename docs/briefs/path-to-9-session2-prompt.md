# Session 2 prompt — Pilot residuals (parts 01, 02, 05–08, 10–12, 22–24, 27, 32)

Paste everything below the line into a new Claude Code session in `/Users/user/untitled folder 2/harness`.
Prerequisite: Session 1 finished (`docs/briefs/path-to-9-checkpoint.md` exists). Nothing else.

---
[Paste the block from docs/briefs/stable-prompt-prefix.md here, unchanged, then this tail.]

## SESSION 2 OBJECTIVE
Raise parts 01, 02, 05, 06, 07, 08, 10, 11, 12, 22, 23, 24, 27, 32 to 9/10 per the definition in `docs/briefs/path-to-9-plan.md`,
and pull forward the sandbox isolation fixes (threat model T15–T17, part 09 integrity) because T15 is rated HIGH. Do not regress parts
04, 09, 20, 21, 30. Read `docs/briefs/capability-matrix.md` rows for these parts and `docs/briefs/threat-model.md` first; directives are in
`docs/master-prompts/harness-multiplayer-master-build-prompt.md` (grep `^## 0[1-2]\.|^## 05\.|...`).

## DELIVERABLES (order matters: 1 first, 2–7 can be lanes)
1. **Clean the baseline** (no lane): review the diffs lane C made in Session 1 (`logging_utils.py`, `run_queue.py` advisory lock,
   `observability.py`) and keep or fix them; root-cause and fix the two section4 flakes (`test_stop_kills_worker` EPERM at `engine.py:180` —
   `os.killpg(pid,0)` must treat PermissionError as "gone"; `test_claim_query_contention_skip_locked_guard` `fake_exec() missing 'params'`);
   fix `make frontend-tsc` TS2344 (`runs/[runId]/page.tsx:42` params typing for Next 15); make `make pg-probe-s1` self-contained
   (no hardcoded :5499 / `docker exec harness-db-1`). Re-run affected suites.
2. **Lifecycle (10, 22, 23)**: explicit durable state-transition table for pause/resume/stop/timeout/failure/completion; tests for races
   (pause vs stop vs complete), idempotent repeated stop, worker crash and service-restart recovery, no duplicate execution on reclaim; the
   UI/API never says "stopped" while mutation is possible; document any provider-cancellation limit.
3. **Events & audit (11)**: tamper-evident audit (hash chain or equivalent) with a verify command and a test that fails when a row is edited;
   before/after values for consequential changes; separate product events from security audit decisions in docs; event export for
   audit evidence (authorized, team-scoped, bounded) with a test; WebSocket push of collaboration events with cursor resume and
   duplicate handling (keep polling as fallback); test unauthorized actions produce audit evidence.
4. **Approval & redirect rules (06, 07, 08, 05)**: confirm each directive against code and tests; add missing: documented rule for simultaneous
   instructions (and a test), reason required for scope/permission changes (test), workspace identity in run metadata without secrets, safe
   workspace cleanup after terminal states. Map the 05 gap checklist to rows with a pass/fail.
5. **Sandbox isolation (09 integrity, T15–T17, T7b)**: refuse taskId reuse across teams (and same-team reuse unless explicit resume); validate
   taskId charset/length (no `..`, separators); resolve and reject symlink escapes in forbidden-path checks; per-task private temp dir with
   restrictive permissions. Test each with the probes from the threat model.
6. **Shared UI (12, 24)**: pending decisions show the responsible person; changed files show approval status; readable history; keyboard and
   accessible status language; no private reasoning shown. Vitest for each; extend the Playwright proof (`section7/e2e/multiplayer.e2e.ts`)
   with: changed proposal re-requires approval, cross-run isolation (second run's page cannot see first's data), and refresh/reconnect survival.
7. **Release (32)**: alert rules file that actually loads (create `deploy/observability/alerts.yml` and wire `rule_files`), metrics for failed
   approvals, cancellation, isolation violations and event delivery; tested rollback of the collaboration flag (`HARNESS_PILOT_TEAMS`)
   leaving runs readable; document feedback-after-each-task and operator-present rules in the pilot runbook.
8. **Acceptance map (27)**: one table in `docs/briefs/evidence/s2/acceptance-core-flow.md` mapping each of the 10 directives to a test path and a
   fresh run result.
9. Update matrix rows (state + evidence path), extend `threat-model.md` statuses for fixed threats, write `evidence/s2/`, then overwrite
   `path-to-9-checkpoint.md` (keep Session 1's key facts in one line each).

## METHOD
Numbered plan with verify steps first. At most four lanes, no shared files: (A) lifecycle + audit chain (section4/section1), (B) sandbox
isolation (section5), (C) UI + Playwright (section7), (D) release/alerts/docs. You do deliverable 1, integrate, and own the matrix.
Run docker only when needed; note that `run_smoke.py` fails for ~1–2 min after a previous run (port TIME_WAIT) and overwrites
`docs/briefs/evidence/p1/*`. Never run two smoke runs at once.

## DONE WHEN
All listed parts have matrix rows `verified` or an explicit documented reason; every suite touched passes in this session with commands and
counts recorded; flakes fixed or root-caused; no claim without a run. Use the final response format from the prefix.
