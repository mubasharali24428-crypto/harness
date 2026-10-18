# Multiplayer pilot - readiness decision (Session 4, 2026-10-17)

**Decision: NOT yet safe to begin with real users** (mechanisms exist but were not exercised against the real runtime). Safe for a supervised internal demo on
synthetic data with an operator present (single-project team, or `projectId` sent). B1-B4 are implemented; the real DeepSeek runtime integration (see above) is the remaining gate.

## What was run (live)
Real `services:orchestrator_app` (uvicorn) on a scratch Postgres with all 35 migrations; four
seeded users (3 in team A: engineer x2, viewer; 1 in team B admin) with real bearer sessions.
Engine = the built-in echo backend (`ENGINE_BACKEND_ECHO=1`, 6s busy thread) - NO provider and NO
real file edits. The driver script lived outside the repo (not committed). 31 checks: 30 passed,
1 failed (second `stop` -> HTTP 500), fixed in this session and re-verified live.

## Acceptance (spec s27/s28)
| Item | Result | Evidence |
|---|---|---|
| Create/join shared run | PASS (API) | live join/leader/roster. Dashboard `/plans` path: FAIL, see B1 |
| One consistent plan/state | PASS (status, roster) | live `/status`, `/participants`; UI not exercised |
| Foreign team blocked | PASS | live 404 on participants, history, approvals, stop, redirect |
| Redirects attributed + per run | PASS | live history shows actor+kind; idempotent `requestId` |
| High-impact blocked until leader | PASS at run level | live: run BLOCKED with pending approval; per-action NOT enforced (B3) |
| Approval = exact proposal | PASS | live: stale digest 409, amend voids, old digest unclaimable |
| Handoff explicit/visible | PASS (API) | live: proposer leads until recipient accepts; UI not exercised |
| Stop honest | PASS | live: `stopping` while thread alive, `stopped` after; repeat stop 200 (fixed) |
| Stop prevents mutation | NOT GUARANTEED | thread cannot be killed (B4) |
| Run outputs isolated | NOT VERIFIED live | per-run cwd covered by unit tests only; echo backend bypasses it (B2) |
| Review artifact + history | PARTIAL | ordered history + cursor resume PASS; artifact review not checked |
| Ops: migrations | PASS | 35 apply on PG; 0035 rollback = drop 4 tables |
| Ops: health | PARTIAL | `/health` ok; dependency-vs-app failure split not checked |
| Ops: cold start from docs | PARTIAL | needed `HARNESS_ALLOW_DEV_MASTER_KEY=1` + manual env; docker compose not run |
| Guidance reaches agent | PASS | live: engine goal contained the `<team_guidance>` block (next start/resume only) |

## Blockers (stop real-user pilot)
- **B1 (P0) RESOLVED for single-project teams (2026-10-17, re-verified live):** `POST /plans` now takes an optional `projectId` (validated, foreign/unknown = 404) and, when omitted, binds the team's only project; the dashboard's `{goal}` request then produces a persisted, collab-capable run (live: status/join/leader/redirect/start/engine all 200, foreign team 404, no plan-persistence errors). **Still open:** a team with several projects that omits `projectId` gets an unbound run as before, and the dashboard client now sends the locally stored `harness.projectId` when set (`planGate.tsx`), so multi-project teams must have selected a project first. Original finding: Dashboard-created runs were unusable. `POST /plans` mints a run with no project
  binding and no `agent_runs` row (plan persistence FK-fails, logged); every run-scoped route then
  returns 404 for it, including all collab routes. The UI creates runs this way. Fix: bind a
  validated `projectId` in `/plans` (or create the run via the bound path).
- **B2 / B3 / B4 - built and verified with a STAND-IN worker (2026-10-17); NOT verified with the
  real DeepSeek runtime.** Live (Postgres + real seatbelt, 18/18) and unit (15 new tests):
  - **B4:** live SDK calls run in a worker subprocess in its own process group
    (`engine.py` `_run_isolated`/`cancel`); pause/stop/timeout/shutdown SIGKILL the group and
    confirm it is gone; `stop` reports `stopped` only after confirmation (live: grandchild killed,
    0.01s). Escapes: a process that calls `setsid` leaves the group (seatbelt still confines it).
  - **B2:** worker runs under a macOS seatbelt profile (`confine.py`): writes only to its own
    workspace/session/tmp; reads of other runs' dirs and `~/.ssh` etc. denied; env is an allowlist
    (no master key/DB URL/cloud creds). Live: two concurrent runs could not touch each other, other-run
    read/write and outside write denied. Linux/landlock NOT implemented (`auto` runs unconfined with a
    warning; `HARNESS_ENGINE_CONFINEMENT=required` fails closed).
  - **B3:** `staging.py` baselines the run workspace, withholds deletions, substantial edits
    (>= `HARNESS_SUBSTANTIAL_LINES`, default 50) and any symlink, files digest-bound leader approvals
    (`requester system:staging`), and lands them only via `POST /runs/{id}/approvals/{aid}/apply`
    (claim -> digest re-check against the staged files -> apply -> complete). Enforced at the
    filesystem boundary, not via SDK hooks. A pending approval also stops the engine from running again.
  - **Real runtime (2026-10-18, SDK/runtime 0.2.1rc2, installed as the pinned `live` extra):**
    `engine.py` now uses the real surface (`dsh_home` per run, `sdk-minimal` profile, optional
    `DSH_PROFILE`/`DSH_PATCH_FILES`, missing patch file fails closed; no `cordis`/`session_root`
    kwargs, no env mutation). **Unconfined**, a real hello-world against local Ollama (`qwen3.5:9b`)
    created `hello.txt` = `task0 ok` (20 events). **Under seatbelt it does NOT work:** the runtime
    boots and the model runs, but its persistent-shell PTY backend fails, so no file is created.
    Bisect: the same failure occurs with an allow-default profile that has no restrictions at all,
    so `sandbox-exec` itself breaks the runtime's PTY (no runtime setting disables it); sandbox
    denial logs were not readable. Consequence: for the real runtime, isolation today = separate
    process group + scrubbed env + staging gate, WITHOUT OS confinement (`auto` mode would break
    real runs on macOS; use `HARNESS_ENGINE_CONFINEMENT=off` knowingly, or a container/VM boundary).
    Still unproven / required before real users: (1) OS-level confinement of the real runtime;
    (2) low-impact changes (additions, small edits) pass without approval, file-level granularity
    only, `.git` and empty dirs ignored, baseline is a full copy (200MB cap); (3) done-signal/PR open
    is not yet blocked while staged approvals are pending (the withheld content is simply absent from
    the workspace); (4) no cancel-requested/confirmed audit event yet.

## Other open risks (not blockers for a supervised demo)
Collab UI never run in a browser or against a live backend; no presence; polling (3s) not push;
guidance cursor is in memory (re-delivered after restart); leader may approve own request;
collab race tests are sqlite (PG relies on CAS UPDATEs, smoke-tested only); contracts/TS and
sdk-gen specs not updated; `openapi.json` was already stale; pilot metrics/telemetry plan below
is not implemented; no real-provider run.

## Metrics plan (not implemented; no baseline exists)
Per run: created/completed/accepted-review, elapsed vs team's current workflow (baseline must be
measured first), redirects count + kind, approval request->decision latency, time spent BLOCKED,
stops, failures. All derivable from `run_collab_events`, `high_impact_approvals`, `audit_log`.
No traction or ROI claims are made.

## Next steps (priority)
1. Fix B1 (bind project in `/plans`) + browser-verify the shared panel against a live stack.
2. Wire Section 5 sandbox (or container) as the engine workspace; add concurrent two-run file test; run with the real SDK.
3. Add per-action claim enforcement at the file-op seam; durable cancel-requested/confirmed events.
4. WS push + presence; contracts/TS/OpenAPI regeneration; PG concurrency tests for approvals.
