# Multiplayer pilot-ready roadmap

Source: master prompt sections 01-61; status from `multiplayer-readiness.md` (2026-10-18).
Current estimate: pilot (01-35) ~6/10, full product (36-61) ~2/10. Goal of this roadmap:
**P0-P3 = safe, supervised pilot with real users.** P4+ = full product, scheduled after pilot data.
Rule: a phase is done only when its exit evidence exists (a test, a live run, or a doc), not when code is merged.

## P0 - Unblock real runs (isolation) ~4-5 days  [sections 09, 10, 29]

**Complete 2026-10-18.** All 18 live container checks passed, including real
`hello.txt = task0 ok`; Section 4: 592 passed / 25 skipped; lint clean.
[P0 exit evidence and operating instructions](p0-container-isolation.md).
The remaining P1–P3 pilot gate still applies.

| # | Work | Exit evidence |
|---|------|---------------|
| 0.1 | Dockerfile for the worker (pinned `live` extra, non-root, read-only root, tmpfs /tmp) | image builds, runs hello-world |
| 0.2 | `confine.wrap_command` gains `container` mode: mount only run workspace + session dir rw, egress allowlist to the model endpoint, `--cap-drop=ALL`, no-new-privileges, pids/mem/cpu limits | unit test on the argv |
| 0.3 | Cancel = container kill + confirm gone (replaces killpg for this mode) | stop test: container gone, status `stopped` only after |
| 0.4 | Rerun real hello-world + the 18-check isolation suite inside the container | `hello.txt` = `task0 ok`; two concurrent runs cannot read/write each other |
| 0.5 | Lift the fail-closed gate for `container` mode only (gate stays for bare `off`) | test: real runtime refuses without container or explicit `off` |
Risk: Docker/Colima availability on the pilot host. Fallback: microVM, or run the pilot only on Linux hosts with landlock (not built).

## P1 - Prove the shared UI end to end ~3-4 days  [sections 12, 24, 25, 27]

**Complete 2026-10-18.** [P1 exit evidence](p1-shared-ui-evidence.md). P2–P3 still required.

| # | Work | Exit evidence |
|---|------|---------------|
| 1.1 | Start stack (API :8104 + section7) and drive two users in the built-in browser: create, join, leader, redirect, handoff, approve, stop | recorded checklist, zero console/network errors |
| 1.2 | Fix whatever 1.1 finds, each with a test first | tests green |
| 1.3 | Add a browser smoke script (Playwright or the browser tool) for the 6-step flow | script passes on a clean DB |
| 1.4 | Staged-approval UI: show withheld diffs, approve/apply, pending state blocks done/PR | live run with a deleted file approved and applied |

## P2 - Hardening and review ~4-5 days  [sections 29, 30, 11, 53]

**Implemented 2026-10-18.** [P2 evidence](p2-hardening-evidence.md) (focused review, not a full audit). P3 still required.

| # | Work | Exit evidence |
|---|------|---------------|
| 2.1 | Security review by lane: routes, authz, participant-approving-own-request, digest replay, path/symlink escapes in `staging`, cross-project IDs, guidance injection | findings list; each fixed with a failing test first |
| 2.2 | Block leader self-approval (policy decision: configurable) | test |
| 2.3 | Audit events: cancel-requested / cancel-confirmed / apply / reject | allowlist updated, tests |
| 2.4 | Chaos on Postgres (not sqlite): kill worker mid-edit, restart with pending approval, two leaders racing, guidance cursor persisted (not in-memory) | all pass; cursor survives restart |
| 2.5 | Done-signal/PR open blocked while staged approvals pending | test |

## P3 - Contracts, data rules, metrics, release ~4-5 days  [sections 03, 14, 31, 32, 34, 35]

**Implemented 2026-10-18.** [P3 evidence](p3-contracts-data-release-evidence.md); [release plan](multiplayer-pilot-release-plan.md) awaits human review. The supervised pilot gate is still outstanding.
| # | Work | Exit evidence |
|---|------|---------------|
| 3.1 | Regenerate `openapi.json`, TS contracts, sdk-gen specs from code; diff against UI calls | CI check that fails on drift |
| 3.2 | Retention/deletion rules for events, diffs, staged files, session dirs; cleanup job | doc + test |
| 3.3 | Pilot metrics (created/completed/accepted, elapsed, redirects, approvals) + baseline capture | dashboard or query doc with real numbers |
| 3.4 | Release plan: flag, allowlisted teams, rollback, support runbook, known limits page | doc reviewed |
| 3.5 | Update checkpoint + final deliverables list | docs committed |
**Pilot gate:** P0-P3 exit evidence all present, then run a supervised pilot with 1-2 teams for 2 weeks.

## P4 - Phase 2 of master roadmap: collaboration depth (after pilot data) ~3-4 wks  [41, 43, 46, 47, 50]
Presence, comments/discussion, push delivery (SSE/WebSocket replacing 3s polling), notifications with user control, search/history/export.

## P5 - Phase 3: org, integrations, controls ~4-6 wks  [37, 38, 48, 49, 51, 57]
Real org/role/permission policy model, admin console, artifact/version control, scoped integrations with revocation, quotas and cost controls.

## P6 - Phase 4: scale and governance ~4-6 wks  [52, 54, 55, 56, 58, 59]
Load/perf work, observability and support tooling, DR/availability, accessibility + localization, compliance and data lifecycle, migration/rollout paths.

## P7 - Phase 5: extensibility ~later  [36, 45, 49, 60, 61]
Capability matrix as source of truth (start it in P3.5 as a table), agent management, ecosystem, only after a security review. Full acceptance (61) declared last.

## Order and dependencies
P0 -> P1 (real runs make the UI flow meaningful) -> P2 -> P3 -> pilot -> P4..P7 re-prioritised from pilot data.
P1.1 can start in parallel with P0 (it uses the stand-in worker). Everything else is sequential per file area.

## Estimate
Pilot-ready: ~15-19 working days. Full product: add ~12-16 weeks, replanned after the pilot.
