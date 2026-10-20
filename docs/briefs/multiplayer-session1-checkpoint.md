# Multiplayer pilot — Session 1 checkpoint (discovery, 2026-10-17)

Scope: read-only discovery. No code changed. Evidence = targeted greps/reads, not test runs
(no tests executed this session). Paths are relative to repo root.

## Repo state
- `master`, 22 commits ahead of origin; uncommitted: `.gitignore`, `AGENTS.md`, untracked
  `docs/agent-program/`, `docs/master-prompts/`, `docs/reviews/`, `integrations/`, `packages/`,
  `scripts/agent-program/`. Left untouched.
- Mature codebase (STATUS.md: Mission 23 closed). Existing specs: `docs/FULL_SHARED_COLLABORATION_SPEC.md`,
  `docs/GAP_ANALYSIS_AND_FIXES.md`. Commands: `cd section1 && ./.venv/bin/python -m pytest tests/ -q`; same per section.

## Baseline verification (spec §04)
| # | Claim | Result | Evidence |
|---|---|---|---|
| 1 | Roles + team-scoped authz | Verified (partial vocab) | `contracts/src/harness_contracts/roles.py` (admin/engineer/viewer/guest, `can()`); `section4/.../authz.py` default-deny, `HARNESS_AUTHZ_TEST_MODE` escape hatch |
| 2 | Durable events, seq, replay | Present, not re-tested | `section1/migrations/0001_initial.sql` `agent_events.seq`; 0003/0028/0029 notify+partitioning; replay-cap flake noted in STATUS.md risk #1 |
| 3 | UI shows plan/terminal/events | Not verified (section7 unread) | — |
| 4 | pause/resume/stop/human-edit routes | Verified | `section4/.../api.py` ~L635-700; `state_machine.py` `pause/resume/submit_human_edit` |
| 5 | Plan approval + identity | Verified | `state_machine.approve_plan(plan_id, principal_user_id)`; `plan_gate.py` CAS on `agent_plans.version` (M12-F2) |
| 6 | Audit actor/queryable | Partial | `audit_log` (actor_member_id, actor_kind, action, target, detail JSONB); `authz.py` writes `authz.denied`, `run.started`, `run.stopped`. No request/approver/outcome fields |
| 7 | Run isolation | **FAIL** | `section4/.../engine.py:50-51` workspace = `DSH_WORKSPACE` or shared `/tmp/harness-dsh-workspace`, same `cwd` for all runs. (Section5 sandbox has per-task workspace `local_runner.py:134`, symlink-aware `diff_engine.py`, but engine path above does not use it) |
| 8 | Cancellation reaches provider | **FAIL (known)** | `service.py` `stop_agent`→`_cancel_live_task` cancels asyncio task only; `engine_runtime.py:29,296` admits SDK threads cannot be killed. `stop` marks DONE (`sm.finish()`) before execution is confirmed ended |

## Known-gap checklist (spec §05)
| # | Gap | Status | Note |
|---|---|---|---|
| 1 | Session leader | Missing | no `leader` symbol in section1/3/4/contracts |
| 2 | Participants/presence | Missing | no presence code |
| 3 | General redirect/nudge | Missing | only `human-edit` (forces PAUSED); no guidance event |
| 4 | Redirect actor/ts/intent | Missing | n/a |
| 5 | Scope/permission change needs reason | Missing | n/a |
| 6 | Handoff ownership | Missing | n/a |
| 7 | Per-action file approval gate | Missing | approval is plan-level only |
| 8 | Changed proposal invalidates approval | Partial | plan CAS (version 0→decided) + TTL `approval_expired`; no digest over files/scope/permissions |
| 9 | Audit requester/approver/outcome | Partial | actor only; no outcome/approver pairing |
| 10 | Pause/stop terminate execution | Partial/FAIL | see baseline #8; resume has a wait-for-cancel guard (`service.py` ~L1078) |

## Risk-ranked gaps (P0 block pilot)
1. **P0** Shared engine workspace — cross-run file mutation (baseline #7).
2. **P0** Stop reports DONE while provider thread may still mutate files (#8); needs cancel-requested vs cancel-confirmed states.
3. **P1** No per-action high-impact approval / proposal digest (gaps 7, 8).
4. **P1** No leader/handoff/redirect model or events (gaps 1-6) — core product, all new.
5. **P2** Audit lacks requester/approver/outcome; presence absent; section7 UI unverified.

## Selected pilot scenario
Two engineers + one viewer on one run: create run → plan approved → execute small issue fix →
teammate sends guidance (redirect event) → leader handoff → agent proposes file deletion, blocked
until leader approves → stop → reviewable diff + history. Success evidence = the §27 core-flow items.

## Plan for Session 2 (not started)
1. Per-run workspace in `engine.py` (path from run id, realpath-checked, fail-closed) + 2-concurrent-run isolation test.
2. Cancellation: add cancel-requested/confirmed events; don't mark stopped until engine call returns or is declared orphaned.
3. New migration (0035+): `run_participants` (leader flag), `run_collab_events`, `high_impact_approvals` (proposal digest); append audit actions.
4. API: redirect, handoff request/accept, approval request/decide; extend `contracts/` (version bump + TS mirror).
5. Tests per §23. Open decision: extend `engine.py` workspace vs route through Section 5 sandbox runner.

## Not done / unverified
No tests run; section7 UI, WS reconnect/replay behavior, deploy isolation (compose), and migration rollback were not inspected.
No subagents used. Session 4 optional, 3 sessions of capacity remain.

## Session 2 progress (2026-10-17) — steps 1-2 of the plan done
- **Isolation (P0 #1) fixed in the live-engine path:** `section4/src/harness_orchestrator/engine.py` `run_workspace()` gives each run `<DSH_WORKSPACE>/<run_id>`; rejects non-plain ids and symlink escapes; failure returns status `error` before the SDK is constructed. Section 5 sandbox not wired (open decision unchanged).
- **Truthful stop (P0 #2) partially fixed:** `engine_runtime.py` `EngineRuntime.is_running()` tracks the real thread; `service.engine_quiesced()`; `POST /runs/{id}/stop` now returns `"stopping"` (not `"stopped"`) while the engine thread is alive. NOT done: engine threads still can't be killed, and run state is still DONE immediately; no durable cancel-confirmed event/audit row (needs contracts bump).
- Tests: new `section4/tests/unit/test_run_isolation_and_stop_truth.py` (11). `section4` full suite: 537 passed, 7 skipped; `ruff check src` clean. Other sections not re-run.
- Remaining S2: auth/leader/redirect/handoff/approval backend, migrations, audit outcome fields, further tests.

## Session 2 progress, part 2 (2026-10-17) - collaboration backend
- **Migration** `section1/migrations/0035_run_collaboration.sql`: `run_participants` (partial unique index = one leader per run), `run_collab_events` (append-only history, per-run `seq`, idempotent `request_id`), `run_handoffs` (one pending per run), `high_impact_approvals` (digest-bound). Applied cleanly on a scratch Postgres (all 35 in order); rollback = drop the 4 tables.
- **Service** `section4/src/harness_orchestrator/collab.py` + 13 routes at the end of `api.py` (`/runs/{id}/join|participants|leader|redirect|collab-history|handoff|approvals...`). Identity only from the Principal; foreign team = 404 via existing guard; non-leader attempts write `authz.denied`; 14 new audit actions added to `section1/.../audit.py` allowlist.
- **Rules**: ordinary guidance = any joined writer; scope/permission redirect = leader + reason, and voids the run's pending/approved approvals; handoff = leader proposes, recipient (joined engineer/admin) accepts, leader unchanged until then; approval decide = leader only, must echo the reviewed digest, CAS-updated; amend re-pends with new digest; claim re-checks digest+expiry before execution (requester/leader only). TTL `HARNESS_APPROVAL_TTL_SECONDS` (default 3600).
- **Verified**: 19 new tests (`section4/tests/unit/test_collab_backend.py`); section4 556 passed/7 skipped; section1 639 passed/10 skipped; ruff clean on section4 src. Same functions smoke-tested on real Postgres (JSONB, RETURNING, idempotency, handoff).
- **NOT done / open risks**: (1) approval gate is NOT yet enforced inside the engine - the SDK edits files itself, so nothing forces a claim before delete/modify (P1). (2) Redirect guidance is stored and shown, not yet injected into the running agent. (3) FK to `agent_runs`: join/leader on a run that exists only in memory (no `agent_runs` row) will 500 - needs a check that runs are persisted first. (4) No contracts/TS/OpenAPI update and no WS push of collab events (history is poll via `after=seq`). (5) Leader may approve their own request (documented, not blocked). (6) Concurrency test runs on sqlite; PG race only reasoned from the CAS UPDATE. (7) Presence, UI, cancel-confirmed event/audit still missing.

## Session 2 progress, part 3 (2026-10-17) - risk follow-ups
- **agent_runs FK (risk 3) closed:** `api.py` `_persist_run` runs before `join`/`leader`; creates the row via `bus.ensure_run`, else clean `409 RUN_NOT_PERSISTED`. Test: `test_memory_only_run_is_persisted_before_join_or_409`.
- **Engine-path gate (risk 1) - RUN-LEVEL only:** `service._execute` (every start/resume/queue path) calls `collab.pending_approval`; a pending request parks the run in BLOCKED ("awaiting leader approval", resumable). Fails closed on store errors except "collab tables not installed". The SDK is not installed here and no per-tool hook was found, so individual file actions inside a running engine call are still NOT gated - the claim endpoint remains the contract for sandbox/engine callers.
- **Guidance injection (risk 2):** `collab.guidance_since` appends unseen `guidance` + leader `scope_change` events to the goal inside a `<team_guidance>` block framed as team input. Applied at the next engine call (start/resume), not mid-call. `permission_change` is deliberately not injected (it changes no real permission yet). Cursor is in memory: a service restart re-delivers old guidance.
- **OpenAPI (risk 4, partial):** the 13 collab operations and their request schemas were merged into `section4/openapi.json`; the file's pre-existing entries were re-flowed by the JSON dump (same content, noisy diff). The stored file was already stale versus the app (21 routes missing), left as is. `contracts/ts` and `scripts/sdk-gen` specs NOT updated (frozen-contract bump not done; no new EventType added).
- **Verified:** `section4` 558 passed / 7 skipped, ruff clean; `section1` 639 passed / 10 skipped. New tests: `test_collab_backend.py` (21 total).

## Session 3 progress (2026-10-17) - shared workspace UI (section7)
- **Built** in `section7/app/projects/[projectId]/runs/[runId]/`: `collabApi.ts` (client for the 13 routes; identity = bearer only; approval decisions echo the reviewed digest), `collabModel.ts` (pure view-model: leader/role gating, blocked reason, handoff state, redirect validation, event wording), `sharedRunPanel.tsx` (`SharedRunView` presentational + `SharedRunPanel` container, 3s polling, history resumed by `seq`), mounted in `page.tsx` under the run aggregate. Server: approval view now includes `diff`.
- **Shows**: leader + your role, participants, blocked-why banner naming who decides, approval cards (action, files, diff, requester, state) with Approve/Reject for the leader only, handoff offer/accept/decline, direction composer (scope/permission leader-only + reason), ordered team history (`role="log"`). No model reasoning shown.
- **Verified**: `tsc --noEmit` clean; section7 vitest 522 passed (new `collab.test.tsx`: model, client, static render incl. escaping and per-role views); section4 suite green. A wording bug ("until you decides") was caught by a test and fixed.
- **NOT verified**: no browser run against a live orchestrator (the static-HTML snapshot could not be screenshotted by tooling); keyboard/screen-reader behaviour, responsive layout and polling load are untested; no component interaction tests (node env, no jsdom). The existing local-only `CommentsNudges` is untouched and still not connected to the backend. Presence (online/idle) and WS push for collab events remain missing; UI cannot show who is "online".
- **Next (S4)**: end-to-end pilot run on a live stack (two users), stop/cancel confirmation display, contracts/sdk-gen specs, security review items in checkpoint, readiness decision.

## Session 4 (2026-10-17) - live integration + readiness
Live stack run on scratch PG: 30/31 checks passed; the failure (repeat `stop` -> 500) was fixed
(`service.stop_agent` no-op on terminal; pin in `test_edge_cases_decisions.py` flipped; new regression
test). Re-verified live: stop1 `stopping`, stop2 200, stop3 `stopped`. section4 559 passed/7 skipped.
**Decision: not safe for real users** - blockers B1-B4 in `docs/briefs/multiplayer-readiness.md`.
Unused session capacity: none planned; further work is a new tranche starting with B1.

## P0-P3 status (2026-10-18)
P0 containers, P1 shared UI, P2 hardening, P3 contracts/retention/metrics/release are implemented with evidence under `evidence/p0..p3`.
Final deliverables: [P1](p1-shared-ui-evidence.md), [P2](p2-hardening-evidence.md), [P3](p3-contracts-data-release-evidence.md), [release plan](multiplayer-pilot-release-plan.md), [readiness](multiplayer-readiness.md), [roadmap](multiplayer-pilot-roadmap.md).
Capability matrix (source of truth, start):

| Capability | State | Evidence |
|---|---|---|
| Per-run container isolation + confirmed cancel | Done | P0 |
| Leader/handoff/approval/redirect + shared UI | Done | P1 |
| Staging gate, symlink containment, self-approval block, guidance cursor | Done | P2 |
| Spec drift check, retention, metrics, per-team API flag | Done | P3 |
| Supervised pilot with real teams | **Not done** | pilot gate |
| Presence/WS push, per-tool approval hook, UI-side flag | Not done | known limits |
