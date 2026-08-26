# STATUS — where the project stands (updated 2026-08-23, Mission 11 wave 1)

> **Terminology (canonical — see also README.md):** "engine" = this platform's
> Section 4 execution component; "DeepSeek Harness" (or "dsh", always referring
> to that external product/SDK, never to our engine) = the upstream tool the
> engine drives; "run" (not job/task); "team" (not org/workspace).

## Mission 11 / Wave 1 (2026-08-23) — CLIENTELA remediation: six CRITICALs + flow-blocking HIGHs

Baseline d5d9619, 7 lanes (graph: docs/briefs/mission11-wave1-graph.md). Sole-owner
law held all wave: services.py include block = C1 only; compose engine env block =
B1 only; state_machine.py = D1 only. Lane-by-lane:

1. **W1-A1 — C-001/H-004** (dc9b75f): verified ownership behind OTP — rate limiter
   DEFAULT_ROUTES moved onto /auth/request-login + /auth/verify-otp (dead legacy
   route removed); unverified team-create 403, burst 429+Retry-After with window
   reset, double-redeem rejected. Legacy direct-add removal lands at ATLAS merge
   (app.py is ATLAS-owned).
2. **W1-A2 — C-002** (7a6b2b3): audit emission wiring — audit_emit.py typed helpers
   (never-throw, secret-scrubbed) called at every sensitive site (api_keys,
   invites, billing); run.started/stopped helpers shipped as a merge seam for S4.
3. **W1-C1 — C-005 backend** (281d769): plan-gate orchestrator API per the verbatim
   W1-C1↔C2 pair contract — POST /api/v1/orchestrator/plans → 201 {planId, plan};
   approve → 200 {runId} idempotent; reject → 202 persisted+audited; role gates
   admin/engineer decide, viewer/guest 403; sole include block in services.py.
4. **W1-C2 — C-005 UI + C-006** (c2b487c): PlanGate card off status.state;
   planGateApi.ts mirrors the contract DTOs field-by-field; login routes real
   post-auth identity — adopt first team project or switch to create-project flow;
   fabricated proj-test-1 / run-timestamp literals deleted from source (runs are
   minted server-side on approve now).
5. **W1-B1 — C-004/H-005/H-006** (db9e333): engine boot integrity — engine_boot.py
   validates required env and exits non-zero naming vars (never values);
   contracts-path bootstrap for bare checkouts; compose engine block free of
   ollama placeholder/dev defaults; live_mode explicit {live,dry} enum; downgrade
   ladder removed.
6. **W1-A3 — C-003 budgets** (IN FLIGHT, uncommitted tree): budget_guard.py (S3)
   + cost_ledger.py writers (S1), atomic fail-closed reserve; test_budget_atomic.py.
7. **W1-D1 — H-007/H-008 done-signal** (IN FLIGHT, uncommitted tree): signals.py;
   role gate + non-empty diff + no resurrection; reviewer_passed→DONE terminal;
   reject_plan persisted+audited (state_machine.py is D1-owned — C1 reads only).

Glue lane (this entry): services.py import hygiene — both I001 blocks sorted
(pre-existing at baseline d5d9619, proven; sort-only, zero behavior delta);
fabricated-literal final sweep CLEAN (proj-test-1 / run-${Date…} survive only in
docs/clientela audit cards as historical record and in gitignored .next build
cache — zero occurrences in any shipped source); plan-gate conformance goldens
added at section4/tests/conformance/test_plan_gate_conformance.py (golden walks
over the pair-contract routes: 201 propose → idempotent 200 approve → gated
start executing; 202 audited reject ⇒ subsequent start 409 PLAN_NOT_APPROVED;
ROLE_DENIED / PLAN_NOT_FOUND error_body envelopes frozen).

Known drift & gate notes for ATLAS:
- Migration numbering: wave pre-assigned 0012 to auth hardening, but M10 already
  used 0012 (agent_events run_id index) — next free slot is 0013.
- S4 canonical suite (`section4/.venv`): 225 passed / 1 skipped, with 3 known-red
  TestEngineSingleModel stress cases — old wrappers construct engines without
  DEEPSEEK_BASE_URL and B1's new fail-closed boot validation trips them;
  engine-boot lane reconciles before merge.

## Mission 10 (2026-08-23) — ALEX_TEAM: Phase 2 completion + Phase 3 edge-hardening begins

First full ALEX_TEAM run: ATLAS + 2 lead review ladders + 10 parallel subagent
lanes in two squads, three PAIR×2 lanes on written interface contracts
(M10-C1 keys, M10-C2 runs, M10-C3 billing). Graph:
docs/briefs/mission10-graph.md, baseline abcab51. Two provider-outage deaths
(one 43-min explore-then-timeout with zero writes; one dispatch-call timeout
that actually spawned fine) — footprint-audited before each re-dispatch.

1. **BYO keys (M10-C1)** — /api/v1/keys CRUD over frozen api_keys table:
   KeyVault-encrypted at rest, hint-only exposure, idempotent soft revoke,
   foreign-team 404 without existence oracle; S7 /settings/keys page.
2. **Cost dashboard** — S7 /dashboard/cost over live /spend: range selector,
   totals cards, pure-CSS byModel bars, SVG sparkline, honest empty state,
   explanatory notice when metering unwired (503).
3. **Run history (M10-C2)** — GET /api/v1/orchestrator/runs team-scoped with
   server-driven paging (limit≤100 clamp) + filters; startedAt/finishedAt
   honestly derived from agent_events (frozen agent_runs lacks columns); S7
   /runs page w/ status pills and relative times.
4. **Billing (M10-C3)** — /api/v1/billing/state|plan behind HARNESS_BILLING;
   flag-off renders honest upsell; local quota = cost_ledger SUM per month vs
   teams.budget_usd; Stripe adapter is an injectable protocol (no package);
   migration 0010 billing_plans; S7 /settings/billing page w/ meter.
5. **Onboarding revival** — S10 checklist API derives `done` from real state
   only (no fabricated signals); interactive S7 /onboarding page w/ progress.
6. **WAL-G PITR** — deploy/walg scripts (base/wal push, PITR restore to target
   time), compose `walg` profile, Dockerfile.walg, runbook "WAL-G operation"
   section with explicit validated/not-validated split (needs S3 creds to
   exercise).
7. **Rate limiting** — fixed-window Postgres limiter on login/OTP routes:
   atomic ON CONFLICT upsert, 429+Retry-After error_body envelope, DB failure
   fails OPEN (availability > strictness); migration 0011 rate_buckets.

ATLAS wirings: keys+billing routers registered in app.py; rate limiter
installed at assembly level (app_identity) NOT in make_app — tests build
un-limited apps and install tuned limiters (double-install caught live at
merge: 6 red tests → moved wiring → 134 green). Nav links added (Runs, Cost +
settings-hub Workspace links). Gate sweep: S1 134 / S2 19 / S3 66 / S4 134+1
known LIVEPROBE flake / S5 118+1skip / S6 38 / S8 38, S7 tsc OK + 73 vitest,
section10 15 *(historical M10 figure — re-counted 65 collected / 65 passed on
2026-08-23, Mission 12 G4 docs-truth lane; the suite grew in W1/W2)*,
ruff clean everywhere, compose valid incl. walg profile.

## Wave 9 (2026-08-23) — Phase 2 SaaS core begins: metering, budgets, orgs, notifications, security headers

Five parallel subagent nodes (graph: docs/briefs/wave9-task-graph.md, baseline
6d5e52c). Three first attempts died to provider outages (HTTP 503 ×2, 300s API
timeout ×1) with ZERO tree writes each — all re-dispatched and completed. Every
node's delivery was independently re-verified by the orchestrator (own test
reruns + live probes), then two merge wirings landed:

1. **P2-METER (S3)** — cost_ledger has real writers: record_usage on every
   successful call_model, fire-and-forget swallow-on-failure;
   GET /api/v1/models/spend aggregates totals/byModel/byDay; app_models now
   boots an asyncpg ledger pool at startup and drains pending writes on
   shutdown. S3 66 passed.
2. **P2-BUDGET (S4)** — check_budget enforced pre-spawn and on reviewer model
   calls; cap = teams.budget_usd (migration 0007) > HARNESS_TEAM_BUDGET_USD >
   unlimited; over-cap ⇒ honest blocked event. S4 123 passed + 1 known flake
   (LIVEPROBE OpenRouter outage during this session — D2 honesty guard working,
   unrelated).
3. **P2-ORGS (S1+S7)** — team_invites (0008) with hashed tokens, admin-only
   mint/revoke, atomic redeem (404/409/410 honest codes), members roster;
   S7 /settings/team page (roles pills, invite form, revoke, join-by-token);
   verified against live Postgres. S1 105 passed; S7 vitest 58 passed.
4. **P2-NOTIF (S1+deploy)** — notification_outbox (0009); templates
   run_finished(←engine_completed)/review_failed/pr_opened; worker drains with
   max 3 attempts; dev mode offline-green; EventBus gained a disclosed,
   exception-guarded listener tap so notifications can never break publishing.
5. **SEC-HEADERS (all apps + CI)** — crypto audit verdict NO GAP (regression-
   tested); pure-ASGI middleware (nosniff/DENY/referrer everywhere, HSTS only
   behind HARNESS_TLS_ENABLED=1, report-only CSP on HTML) wired into
   S1/S4/S5/S6/S8 factories; ci.yml gained security-scan job (pip-audit WARN +
   gitleaks).

Merge wirings by orchestrator: invites router registered in app.py;
set_ledger_pool wired in app_models. Gate sweep: **7 sections pytest green
(105+19+66+124+119+38+38 incl. skips), ruff clean everywhere, compose config
valid**. Known-open follow-ups: WAL-G PITR (runbook documents it), real TLS
certs (needs domain), Stripe billing flag, BYO keys UI.

## Working right now

- `docker compose -f deploy/docker-compose.yml up` → healthy stack (PG + 7
  backends + frontend). Migrations 0001–0003 applied via migrate.py.
- **388 pytest green** (incl. real-PG fanout test + 9 OTP tests + 5 confinement
  tests) and **43 vitest green**; ruff clean; tsc clean.
- Live proofs today: ticket-auth WS delivering sandbox `terminal_output`
  cross-instance in 0.7s; orchestrator flow; restart rehydration.

## Wave 6a (2026-08-22, later session) — checkpoint-aware resume + ops hygiene

1. **RESUME DONE (S4)**: `resume_agent` previously flipped PAUSED→EXECUTING without
   re-spawning any engine task — UI said "executing" over a dead run (Appendix C lie).
   Now resume re-invokes `execute_goal` with the SAME run_id passed as the
   DeepSeek Harness ("dsh") session id, so an explicit human Resume CONTINUES
   the upstream engine session instead of restarting blind.
   Pause writes no terminal result (run stays resumable); stop stays terminal with an
   honest `cancelled`; live-run double-spawn guarded; same-tick pause→resume race fixed
   by awaiting the dying task. 5 new tests (`section4/tests/unit/test_resume.py`);
   integration test_checkpoint1 re-run green (4/4).
2. **Ops hygiene**: db service gained `restart: unless-stopped` (host restart had left
   the backend DNS-crash-looping for hours); root-level debug logs gitignored;
   Part 10 ledger synced with shipped reality (H2r/E-s3/E-cat/E-codegen/M4/M5/CI struck);
   RESUME recorded done. Appendix C FAQ annotated.
3. **Wave-6 planning doc committed** (docs/EXPANSION_PLAN.md) from the parallel
   planning session; its D2 engine experiment was rolled back by that session before
   handoff — tree verified clean, fallback-chain tests green again.

Verified this session: stack healthy on all 7 ports + frontend; seed_demo idempotent;
CI green (~36s runs); nightly probe schedule present.

## Wave 6b (2026-08-23) — Phase 0 landed (D1–D4) + OpenRouter live probe

1. **D1–D4 landed** (from docs/EXPANSION_PLAN.md Phase 0, authored by the wave-6
   planning session): D1 compose model drift fixed; D2 empty-completion honesty
   guard (fake-success now falls through the fallback ladder to an honest error);
   D3 per-run publish chains (bus seq order matches causal order); D4 additive
   engine lifecycle events — contracts 0.2.0→0.3.0, `engine_completed`/
   `engine_failed`, TS mirror regenerated same commit, SM advances
   EXECUTING→AWAITING_DONE_SIGNAL on completion / BLOCKED on failure (guarded to
   EXECUTING so pause/human-edit narratives win), golden fixtures + S1 conformance
   pin updated, new publish-ordering + SM + engine tests. Failed runs land in
   BLOCKED, which RESUME can legitimately resurrect.
2. **LIVEPROBE**: flaky local-Ollama live test replaced by an OpenRouter probe
   (model stealth/ox-alpha, per user) asserting the real filesystem side effect;
   fresh workspace/session dirs per run — reusing the dsh session root had poisoned
   reruns with dead session history (completed-with-0-chars). Skips without
   OPENROUTER_API_KEY so CI stays green offline.
3. Gate: 370 pytest (S4 72/72) + 58 vitest green; ruff clean; TS mirror in sync;
   compose config valid.

## Wave 6c (2026-08-23) — Phase 0 COMPLETE: D5 authz + D6 auth wiring

Phase 0 of docs/EXPANSION_PLAN.md is now fully landed (D1–D4 in 6b; the last two
defects closed here).

1. **D5 DONE (S4)**: every `/api/v1/orchestrator/runs/{runId}` route now runs a
   single authorization dependency (`_authz_dep(action)` → `authz.py`): identity
   via app.state bearer dep, team ownership via `agent_runs.project_id` →
   `projects.team_id` join (raw SQL on frozen names; skipped in bare unit mode
   like rehydration), and role permission via frozen `roles.can()` — approve/
   reject need "approve", run control needs "write", GETs are read-open.
   `propose` rejects foreign AND unknown projectId (TEAM_OWNERSHIP 403) so a
   propose can never mint a run into another team's project. 10 regression tests
   (`tests/unit/test_authz.py`) incl. viewer-can-read-but-not-drive and
   guest-cannot-pause. S4: 82 passed.
2. **D6 DONE (S5/S6/S8 + deploy)** — implemented by Codex CLI (`--profile ox`,
   OpenRouter stealth/ox-alpha) from a written brief (`docs/briefs/2026-08-23-d6-codex-auth-wiring.md`), diff
   reviewed and independently re-verified by ox-alpha:
   - Fail-closed `get_principal` dependency on EVERY route of all three services
     (401 bad token / 503 unwired — no open endpoint anywhere).
   - Identity always from the principal: S6 body `userId` and S8 body
     `reviewer_user_id` kept wire-compatible but IGNORED; S8's spoofable
     `x-user-id` header fallback DELETED (`_resolve_reviewer` removed).
   - S5 adds a write-gate: viewer/guest roles get ROLE_DENIED 403 on all four
     task routes (executing code is a write).
   - Deploy assembly wires `make_bearer_dep(factory)` into `app_sandbox` and a
     rebuilt `app_meeting` (own factory + CORS + /health); `app_review` already
     had it — now its routes actually consume it.
   - Tests: S5/S6 conformance updated to principal dependency-overrides + new
     unauthenticated-rejection tests; new `section8/tests/unit/test_api_auth.py`
     pins forged-body and x-user-id spoof rejections. S5 118 passed,
     S6 38 passed, S8 38 passed.
3. **CI fix en route**: ruff F841/F401 in S4 engine tests had failed the
   `pytest (section4)` job on commit 15326db; dropped in 72bd255 (suite green
   since).
4. Full sweep this session: 477 pytest across 8 sections + 58 vitest + tsc +
   ruff (all sections) + sh -n + compose config + TS-mirror --check, ALL GREEN.

Phase 0 exit criteria met except live-stack re-smoke (PG stack was down locally;
nightly CI covers the real-PG probe). Next up per plan: Phase 1 production
architecture (split containers, durable run queue, cross-process state).

## Wave 7 (2026-08-23) — Phase 1 starts: cross-process ephemeral auth state

Implemented by Codex CLI (`--profile ox`) from a written brief
(docs/briefs/2026-08-23-p1a-cross-process-state.md); diff reviewed and
independently re-verified by ox-alpha, which also caught + fixed one defect in
the delegation's output (see below).

1. **P1-XSTATE DONE (S1/S2)**: WS connect tickets and GitHub OAuth states moved
   from `app.state` dicts into Postgres (migration `0005_ephemeral_state.sql`,
   tables `ws_tickets` / `oauth_states`). Stores:
   `section1/src/harness_identity/ephemeral.py` (mint/redeem with atomic
   single-use `DELETE … RETURNING`, project-bound, opportunistic expiry sweep on
   mint) and `section2/src/harness_github/state_store.py` (insert/consume,
   same pattern). In-memory dicts deleted; HTTP contracts unchanged; tickets/
   states now redeemable on ANY replica.
2. **Timestamp discipline**: both stores write UTC ISO-8601 TEXT and migration
   0005 declares TEXT columns — SQLite keeps DATETIME as text, and mixing
   adapter-formatted datetimes (space separator) with `.isoformat()` rows (T
   separator) made an EXPIRED ticket compare as valid under string comparison.
   Caught via the new `test_ws_ticket_expired_rejected` regression; fixed by
   normalizing at every boundary so SQLite and PostgreSQL run identical code.
3. Tests: S1 ticket suite extended (roundtrip, single-use replay, expired,
   wrong-project, cross-instance over one shared SQLite file) → S1 50 passed;
   S2 OAuth state suite likewise → 19 passed. Ruff clean both.

Next Phase 1 candidates: split-container compose shape (brief drafted),
durable run queue (`run_claims` SKIP LOCKED), structured logs/metrics.

## Wave 7b (2026-08-23) — split-container deployment, live-proven

Implemented by Codex CLI (`--profile ox`) from
docs/briefs/2026-08-23-p1b-split-container.md; verified by ox-alpha against a
REAL `docker compose up -d --build` boot (not just static checks):

1. **P1-SPLIT DONE**: one image, service selected by `HARNESS_SERVICE`
   (`migrate|identity|github|models|orchestrator|sandbox|meeting|review`).
   entrypoint.sh: unknown values fail fast; `migrate` runs schema+seed then
   exits 0; single-service mode execs ONE uvicorn on its port (workspace copy
   only for sandbox/orchestrator); unset var keeps the legacy all-in-one flow
   byte-identical. docker-compose.yml: YAML anchors (`x-backend-build/env/
   depends`) expand into db + migrate (one-shot, restart:no) + seven services,
   each with its own port and self healthcheck, all gated on
   `service_completed_successfully` from migrate; frontend now waits on
   identity. services.py unchanged (factory names already matched).
2. **Live proof (this session)**: full stack booted split — migrate applied
   0005_ephemeral_state.sql to real Postgres then exited; all seven backends
   healthy on their own ports + frontend 200; unauthenticated calls to S5/S6/S8
   → 401 (D6 holds in containers); login → ws-ticket mint → WS upgrade with
   ticket CONNECTED, replay of the same ticket rejected (single-use across
   processes, P1-XSTATE proven live); authed meeting create on :8006 → 201.
3. Ops note: re-running `docker compose run --rm migrate` after creating new
   teams seeds their starter project (idempotent) — used during live probing.

Phase 1 remaining: durable run queue (`run_claims` SKIP LOCKED), structured
JSON logs + request IDs, Prometheus /metrics, secrets hardening.

## Wave 7c (2026-08-23) — durable run queue (crash-resumable execution)

Implemented by Codex CLI (`--profile ox`) from
docs/briefs/2026-08-23-p1c-durable-run-queue.md; diff reviewed and independently
re-verified by ox-alpha, which caught and fixed one production-breaking defect
in the delegation's output (asyncpg rejects ISO strings for TIMESTAMPTZ —
verified live against the compose Postgres before fixing; `_persist_plan` now
binds real datetime objects, ISO text remains reserved for text-compared
columns).

1. **P1-QUEUE DONE (S4 + migration 0006)**: `run_claims` table
   (queued/running/orphaned, TEXT ISO timestamps, no FK — claims may precede
   the lazy `agent_runs` insert). Store `run_queue.py`: enqueue with
   orphan-requeue + double-start guard; ONE atomic FIFO claim statement with a
   per-team running-count cap (`projects` join); worker-guarded heartbeats;
   finish-on-settle; `reclaim_orphans` flips stale heartbeats (90s default,
   10s heartbeat cadence) — a killed container's runs become claimable again.
2. **Service integration**: start/resume enqueue instead of spawning when a
   factory is present; the SM stays EXECUTING exactly as before; `_execute`
   heartbeats and finish-runs in `finally`; `poll_once()` rehydrates the
   claimed run (crashed-process EXECUTING-with-dead-task is precisely the
   resurrection case) and drops claims for PAUSED-rehydrated runs honestly;
   bare unit mode (no factory) spawns directly — every pre-existing suite
   untouched and green.
3. **Worker + deploy**: `RunQueueWorker` loop (recover-on-start, never dies on
   one bad poll) wired into `app_orchestrator` via startup/shutdown hooks;
   `HARNESS_RUN_CONCURRENCY` (default 4) and `HARNESS_WORKER_POLL_SECONDS`
   (default 0.5) env knobs.
4. Gate: S4 90 passed (8 new queue tests incl. FIFO, team cap, heartbeat
   guard, orphan requeue, crash-resume story, service enqueue→poll→complete,
   bare-mode regression); full 8-section sweep 447 passed; ruff clean; TS
   mirror in sync. Live-PG boot of the new migration happens on next
   `docker compose up` (migrate one-shot picks up 0006 automatically).

Phase 1 remaining: structured JSON logs + request IDs, Prometheus /metrics,
reverse proxy/TLS, backup/PITR runbook, secrets hardening.

## Wave 8 (2026-08-23) — parallel subagent wave: observability + TLS + backup

Orchestration pattern (new for this wave): ox-alpha built a task-dependency
graph (docs/briefs/wave8-task-graph.md) with strict per-node file ownership,
dispatched THREE subagents in parallel, independently re-verified every
delivery, and merged at the gate with zero conflicts. Graph tracked state
live; merge checklist fully ticked.

1. **P1-OBS DONE (S4, node A)**: JSON structured logging ({ts,level,logger,
   msg,requestId,method,path,status,durMs}) via contextvars request-id +
   RequestContextMiddleware (X-Request-ID echoed/minted on every response);
   hand-rolled thread-safe Prometheus /api/v1/orchestrator/metrics — counter +
   histogram with ROUTE-TEMPLATE path labels (raw ids leak-checked clean);
   HARNESS_LOG_FORMAT env knob. Verified through the real deploy app.
2. **P1-TLS DONE (deploy + S3, node B)**: Caddyfile harness.local (tls
   internal) routing all 7 backends by path prefix incl. WS passthrough;
   compose `proxy` service gated behind a `proxy` PROFILE (default up
   unchanged); S3 public /api/v1/models/metrics counter. caddy validate green;
   compose config green in both profile modes.
3. **P1-BACKUP DONE (node C)**: deploy/backup/{backup,restore,verify_restore}.sh
   (custom-format dumps, integrity gate, retention, drop-recreate restore w/
   prompt, scratch-db verify vs live row counts) + docs/runbooks/
   backup-and-pitr.md (explicit RPO/RTO, cron, honest WAL-G/pgBackRest gap).
   Orchestrator re-ran backup + verify live: all key tables PASS identical.
4. Gate: full sweep **461 pytest** across 8 sections green; ruff clean;
   sh -n + compose config (both modes) + TS mirror OK. Zero cross-node
   conflicts (file-ownership graph held).

Phase 1 remaining after this wave: real TLS certificates cutover (config-only,
needs a domain), WAL-G/pgBackRest PITR (documented next step), secrets
hardening, frontend e2e (Phase 3 territory).


## Wave 2 (this session) — read docs/GAP_ANALYSIS_AND_FIXES.md for full detail

1. **H8 DONE**: one-time WS tickets (`POST /api/v1/ws-ticket`, 30s, project-
   bound, pop-first redemption). S7 mints a fresh ticket per connect; bearer
   tokens no longer ride URLs. 8 new S1 tests + frontend rewired.
2. **M2 DONE**: additive EventTypes `terminal_output` / `diff_progress`
   (contracts py+ts, conformance pin now "twelve types"). S5 emits via injected
   `on_stream` (per-line streaming + edit progress); deploy bridges to the bus;
   S7 deriveView exposes capped `terminalLines` → TerminalPanel renders live.
3. **G-LIVE FOUND+FIXED (FATAL)**: two EventBus instances per stack meant live
   fan-out never crossed processes; and seq-gap close(1013) was wrong because
   `agent_events.seq` is table-GLOBAL (cross-project jumps are normal).
   Fix: migration 0003 pg_notify trigger + per-instance LISTEN/NOTIFY delivery;
   overflow detection moved onto a per-subscriber `overflowed` flag set by the
   bus itself. Proven live across a 27→28 seq jump.
4. **Engine fallback chain**: `DSH_FALLBACK_MODELS` env or ctor list; primary →
   fallbacks → honest `error`; never fake success.
5. Quick wins: `/health?deep=1` DB ping; bounded `list_projects?limit=`.

Incidents caught by tests/live probes this wave (details in GAP doc Part 5):
asyncpg ≥0.30 `add_listener` is a coroutine; `expire_on_commit=True` factories
made `row.seq` lazy-load outside greenlet after commit (flush-before-commit now);
migration splitter needed $$-dollar-quote AND comment-line awareness; host 5432
already taken → compose publishes PG on 127.0.0.1:**5499**.

## Wave 3 — both public-exposure blockers closed

1. **H2r DONE**: `HARNESS_REQUIRE_OWNERSHIP` flag (default off for dev) gates the
   passwordless hole behind an OTP flow — request-login (enumeration-safe,
   hashed codes, 5-attempt lockout, single-use) → verify-otp provisions exactly
   like legacy login. Migration 0004. Verified live: flag-on legacy=403, full
   request→verify→/me→replay-401 chain; flag-off dev login still 200.
   Dev code delivery prints `HARNESS-OTP ...` to container logs; SMTP is a hook.
2. **F6r DONE**: OS confinement around LocalSandboxRunner — Seatbelt on macOS
   (deny-default profile, writes confined to workspace+tmp, network flag),
   Landlock on Linux ≥5.13 (write-family-only restriction; degrades safely).
   auto|seatbelt|landlock|off per runner; 5 macOS tests execute REAL profiles;
   container sanity passed post-rebuild.

To run the platform with ownership enforcement on:
`HARNESS_REQUIRE_OWNERSHIP=true docker compose -f deploy/docker-compose.yml up -d --build backend`

## Wave 5 (latest) — GitHub OAuth connect flow + exhibition demo readiness

1. **E-oauth DONE (S2)**: real GitHub web application flow — `GET /api/v1/github/oauth/start`
   (302 to github.com/login/oauth/authorize, CSRF `state` bound server-side to the
   session user), `GET /api/v1/github/oauth/callback` (code→token exchange, single-use
   state, token encrypted at rest via KeyVault into `github_connections`, browser lands
   on `{frontend_base}/settings?connected=<login>` or `?error=<ExcName>` — never a stack).
   Browsers can't send Authorization headers on navigation, so a one-time
   `POST /api/v1/github/oauth/start-ticket` (60s TTL) bridges fetch→redirect.
   `GET /api/v1/github/connection` → `{connected, login, scopes}`; no token ever in a
   response body. Unconfigured creds ⇒ clean 503 `NOT_CONFIGURED`. 10 new tests pin the
   flow incl. replay/unknown-state rejection and ciphertext-at-rest assertions.
   Compose maps GITHUB_CLIENT_ID/SECRET (+FRONTEND_BASE/GITHUB_REDIRECT_URI) onto S2's
   HARNESS_ prefixed settings; legacy JSON `/oauth/url` endpoint removed.
   Runners base settings (deploy/runners/services.py) gained the S2 fields so the
   shared settings class can't AttributeError on the new config.
2. **Settings page DONE (S7)**: `/settings` with Connect GitHub button driving the
   ticket-hop flow, connected state (`@login`, scopes), error banner, URL-param cleanup;
   wired via `connectGithub()`/`getGithubConnection()` in src/lib/api.ts. Added to
   AppShell nav.
3. **Working nav restored (S7)**: AppShell "Working" item re-added; links to
   `/projects/{projectId}/runs/{runId}` from localStorage; disabled with tooltip when
   either is missing.
4. **Demo seed DONE**: deploy/seed_demo.py drives only the live REST API (session,
   project match-by-name, repo connect upsert) and prints the localStorage lines for
   DevTools. Idempotency fix: S1's passwordless login mints a NEW solo team (and an
   auto-seeded `demo-repo` project) per call — the script now persists its session to
   deploy/.seed_session.json (gitignored) and reuses it while valid, verified by two
   consecutive runs converging on the same team/project.

Test totals: 396 pytest + 58 vitest green; ruff clean; tsc clean; next build green.
Live proofs: :8002 start w/o creds → 503 NOT_CONFIGURED body; bogus callback state →
400 INVALID_STATE; unauth connection → 401; seed run twice → same team/project,
"already connected".

Remaining known work: none from this task's scope. Ledger additions: E-seed recorded
fixed-in-wave-5, Appendix F documents the OAuth design (per-process oauth state is
fine single-container; move to DB if services scale out). Still open from earlier:
M4/M5 trivia, E-s3, nightly probes (CI job).

## Wave 4 — ledger cleared: consolidation + E-smtp + E-cat + CI + E-codegen

1. **S7/S9 consolidation DONE**: one frontend now. S7 gained S9's design system,
   AppShell, and pages (command center, meeting, review, login) wired to the real
   typed API layer (meeting/review/OTP clients added, TDD'd). S9 retired
   (`git rm`); deploy/frontend.Dockerfile + compose now build section7.
   Live: all four routes 200 on :3000 against the running stack.
2. **E-fe-otp DONE**: /login is flag-aware — legacy login auto-switches to the
   request→verify OTP flow on 403 OWNERSHIP_REQUIRED. Verified live end-to-end:
   request → code in container logs → verify-otp → admin session on :8001.
3. **E-smtp DONE**: `notifier.py` — SMTP via HARNESS_SMTP_* env (host/port/
   user/pass/from/starttls), sync smtplib in a worker thread, delivery failures
   logged never raised (enumeration-safety kept). No host configured → visible
   dev log printer. 6 new tests; compose env-wired.
4. **E-cat DONE**: S3 `catalog.py` — declarative provider catalog with env-var
   indirection (names only), coverage asserted against the pricing catalog
   (caught a missing qwen provider), local fallback candidates = zero-priced
   models. GET /api/v1/models/catalog exposed; S4 engine consults it as
   last-resort fallback chain (ctor > env > catalog).
5. **CI gates DONE**: .github/workflows/ci.yml — sh -n entrypoint + compose
   config + TS-mirror drift check, pytest+ruff matrix over all 8 Python
   sections, tsc+vitest for section7, nightly real-PG probe job.
6. **E-codegen DONE**: contracts/ts/generate.py — AST-based pydantic→TS mirror
   generator; --check gate in CI makes hand-editing the mirror pointless.
   Regenerated mirrors; optional fields handled; consumers updated.

Test totals: 395 pytest (+4 PG integration) + 58 vitest green; ruff clean;
tsc clean; next build green.

Remaining known work: none from the GAP Part 10 ledger. Next candidates:
checkpoint-aware resume (Appendix C), per-service containers if scale demands,
real GitHub OAuth app for repo connection.

To run the platform with ownership enforcement on:
`HARNESS_REQUIRE_OWNERSHIP=true docker compose -f deploy/docker-compose.yml up -d --build backend`

## Known gaps / next actions (GAP doc Part 10 ledger)

1. **E-smtp**: wire real email delivery into the notifier hook before enabling
   HARNESS_REQUIRE_OWNERSHIP publicly (frontend UI already done — see wave 4).
2. E-cat provider catalog via S3; E-codegen pydantic→TS contract generation;
   CI gates (sh -n, compose config, nightly security probes incl. the PG fanout
   test with HARNESS_TEST_PG=1).

## Environment notes

- PG from host: `localhost:5499` (loopback; 5432 is your local Postgres).
- Integration PG test: `HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5499/harness ../section1/.venv/bin/python -m pytest integration/test_checkpoint1.py`.
- `EventBus.publish` flushes before commit — safe under any expire_on_commit.
- deepseek-harness-sdk pinned 0.1.1rc1 (no 0.2.0 on PyPI). CORS env must be JSON.

## Session log (most recent first)

- 2026-08-22 wave 3: H2r OTP ownership proof + F6r OS confinement, live-verified.
  Tests 415→431 total. GAP doc 1095 lines.
- 2026-08-22 wave 2: H8, M2, G-LIVE(+global-seq trap), engine fallback, health/
  limit quick wins. Tests 392→415 total. GAP doc now 1055 lines.
- 2026-08-22 wave 1: 9 FATAL + HIGH fixes, event-log rehydration, live authz
  proofs. See GAP doc Appendix E.

## Mission 12 — Wave 2 CLOSED (2026-08-23)

All 20 lanes landed and gated. Final suites: S1 262 · S4 274 · S6 64 · S7
tsc-clean + vitest 281/281. Verifier teams VT-1..VT-5 next.

## Mission 12 — Wave 2 VERIFIED (2026-08-24)

All five verifier teams returned: VT-3 ROSTER/RUNS READY, VT-4 REVIEW READY,
VT-5 CONCURRENCY READY (after MINORs), VT-1+VT-2 FIX-LOOPs fixed
(e5dc56a apiKey seam + synthesis kill; 96edf35 JSONB reserve) and
re-verified READY-TO-MERGE. Final suites: S1 265 · S3 86 · S4 274 · S6 69 ·
S7 vitest 281. CI green pending gitleaks rename run 32685579474.

## Mission 13 — Wave 3 LANDED (2026-08-24)

All 10 lanes committed: pool math (6dfa3b2), WAL-G PITR executed end-to-end
(c3aead1), Playwright E2E suite (eae5a41), Makefile/CI convergence
(4cd7074), NUMERIC money + Decimal sweep (da645f4/18918d5), timeutil tz
normalization (e0c61ad group), oracle-404s + OpenAPI typing 8→39 routes,
SKIP LOCKED + zombie reaper (06b8055). Suites: S1 309 · S3 91 · S4 287 ·
S6 76 · S7 tsc-clean + 303 vitest. CI green on pushed stack. W3 verifiers next.

## Mission 13 — Wave 3 CLOSED (2026-08-24)

CI 32694440516 SUCCESS. All five verifier teams READY:
VT-1 DB-INFRA (caught + confirmed fixed: SKIP LOCKED placement before
RETURNING), VT-2 PITR (independent drill re-execution, marker resurrected),
VT-3 QA-INFRA (make/CI parity byte-proof), VT-4 MONEY/TIME (live-PG exact
reserve proof), VT-5 API-HARDENING (oracle byte-compare; resume DTO MINOR
fixed in-lane). Suites: S1 309 · S3 91 · S4 291 · S6 76 · S7 303 vitest.
Operator note: NUMERIC(12,6) caps budgets at $999,999.999999.
Waves 4-5 remain.

## Mission 14 — Wave 4 CLOSED (2026-08-24)

CI 32701744809 SUCCESS. All five verifier teams READY:
VT-1 METERING, VT-2 A11Y/RESPONSIVE, VT-3 INVITES/OPTIMISTIC (live-PG probe
36/36, migration walk through 0021), VT-4 AGGREGATE (11-key DTO parity),
VT-5 DESIGN-SYSTEM+INFRA. Suites: S1 315 · S3 91 · S4 291 · S6 76 · S7
vitest 415/415 + tsc clean. Operator note: NUMERIC(12,6) budget cap.
Wave 5 (roadmap program) remains.

## ALEX-FORCE — Wave 5 CLOSED (2026-08-24)

First mission of the 20-agent ALEX-FORCE: independent close-out verification.
FORCE-COMMITMENT REPORT verdict: PROCEED TO CLOSE. Full matrix re-verified:
S1 400 · S4 312 · S6 76 · S8 77 · S7 vitest 431 + tsc clean; guards green
(routes: 15 modules, isolation: 374 files); live-PG probes passed (FK
deletion story 3/3, ws fingerprint 15/15, KDF/WS suites 26). Ruff clean.
Wave 5 CLOSED. Mission 16 (part4 §W5+ backlog) planning next.

## Mission 16 — Wave 6 LANDED (2026-08-24)

CI 32726153473 SUCCESS. All 5 lanes committed:
X1 runs-index tuning at 100k (0026 + N+1 elimination, batched extremes),
X2 meeting message pagination (keyset cursor, bounded windows),
X3 WS replay cap + truncation markers,
X4 UI systemization completion (all pages on DS primitives),
X5 edge-case corpus wave A (19 seed-ID-traceable regression tests).
Suites: S1 430 · S4 318 · S6 99 · S7 vitest 452/452. ALEX-FORCE
verification sweep next → Wave 6 close.

## Mission 16 — Wave 6 CLOSED (2026-08-24)

ALEX-FORCE FORCE-COMMITMENT verdict: PROCEED TO CLOSE. Full matrix
independently re-verified: S1 430 · S4 318 · S6 99 · S7 vitest 452/452;
guards green (15 routes, 395 files isolation); X1 N+1 spot-check clean
(2 batched GROUP BYs per request, zero correlated probes); X5 seed-ID
traceability confirmed against raw-18; migration chain 0001→0026 unique.
Wave 6 CLOSED.

## Mission 18 — Horizon NOW LANDED & CLOSED (2026-08-25)

10x-plan Horizon NOW executed by ELITE-FORCE dual teams. CI 32843200308
SUCCESS. C1 authz fail-closed (triple fail-open dead, audited denies);
C2 one budget truth (contracts/budget.py resolve_cap canonical, fail-CLOSED
+ degraded flag); C3 cost-gate O(1) (0027 indexes + daily rollup runner);
C4 loadtest harness live (BASELINE sweep carried to M19); C5 invite/ticket
hygiene (email binding, ?token= deprecated, fingerprint enforced, bucket GC);
C6 rollback v0 rehearsed (drift gate refused old image vs 0027 DB).
Suites: S1 496 · S3 91 · S4 369 · S7 vitest 452. Next: BASELINE sweep +
Horizon NEXT per mission17-10x-plan.md.

## Mission 19 — BASELINE + Horizon NEXT phase 1 CLOSED (2026-08-25)

CI 32852228986 SUCCESS. N1 stateless orchestrator (CAS approvals, durable-
first, two-replica exactly-once); N2 slim bus (0029 signals ~70 bytes,
8KB publish cap); N3 partitioned retention (0028 monthly partitions,
outbox+audit prune, migrate walk proven); N4 canned demo (zero-key 8-min
value, recorded-truth replay); N5 Python SDK v0 (91 paths/104 ops generated);
B1 BASELINE published — ceiling ~1.9 journeys/s, wall = worker single-claim
poll cycle (worker.py), $0.000051/run metered. Suites: S1 526 · S4 399 ·
S7 vitest 460. Next: claim batching (first 10x lever) + Horizon NEXT part 2
(C12 observability, C13 docs truth) per mission17-10x-plan.md.


## Mission 20 — Horizon NEXT part 2 + FIRST 10X LEVER CLOSED (2026-08-26)

CI 32922623925 SUCCESS. T1 claim batching: 1.69 -> 37.58 journeys/s at 32 VU
(~22x), zero failures / 4521 journeys — BASELINE wall torn down; throughput
ceiling now ~3.2M runs/day theoretical (executor + provider latency become
the real limits). T2 observability minimum: request-id middleware on all 9
services, anonymized user hashing, Prometheus scrape + 5 promtool-validated
alerts. T3 resource limits 22/22 services, log rotation, /ready != /health.
T4 extensions.md (3 real seams) + glossary completion. T5 TS SDK v0 — both
SDKs from one generator pipeline. Suites: S1 574 · S4 414 · S7 vitest 460.
Remaining per 10x plan: LATER horizon (context pillar/codebase indexing,
commercial skeleton) + M23 bets.


## Mission 21 — CONTEXT PILLAR CLOSED (2026-08-26)

CI 32932601871 SUCCESS. X1 indexing core (pgvector, pluggable embeddings,
incremental re-index); X2 @refs resolution (hybrid ranking, token-budgeted
bundles); X4 retrieval-augmented planner (path:line citations in steps);
X3 UI surface (@autocomplete, chunk preview panel, searchable tree).
0030 pgvector-gated — honest degrade without extension. Suites: S1 574 ·
S4 452 · S7 vitest 500 + tsc clean. Exit criterion met: plans demonstrably
cite indexed chunks. Next: Mission 22 commercial skeleton per 10x plan.


### Mission 21 formal close (2026-08-26): CI 32948436346 SUCCESS on
b83dbfd — replay-cap flake eliminated (30/30 stability sweep). Context
pillar + all Horizon NEXT work verified at 100% green.
