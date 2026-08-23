# Raw audit wave 2 — engineering/code residual domains (areas 4–9)

Auditor: ox-alpha subagent · 2026-08-23 · READ-ONLY (no repo modifications).
Scope: areas 4–9 of the harness monorepo, excluding everything already carded in
`docs/GAP_ANALYSIS_AND_FIXES.md` Part 10, `docs/clientela/raw-02/raw-06/raw-08/raw-10`,
and the archived security audits. Every line = one finding: `[area] issue → fix`.
Verified by reading code/manifests/CI; [INFERENCE] where noted. Skipped per brief:
invite-redeem race and rate_buckets upsert (confirmed atomic), metering swallow +
budget fail-open (known), heavy API-contract ground (covered).

## Area 4 — Code quality / architecture

- [4] `OrchestratorService` is 670 LOC fusing queue mgmt + budget gating + event publishing + rehydration + plan persistence + engine lifecycle → split into RunLifecycleService / RehydrationService / EventPublisher before adding features.
- [4] service.py does raw SQL (`_REHYDRATE_SQL`, `_PLAN_SQL`, `_persist_plan`) inline in the orchestrator — a de-facto second persistence layer with no store module → extract an `event_store.py` like S8's `store.py`.
- [4] `_EVENT_STATE_MAP` in service.py duplicates SM transition knowledge; adding one state requires edits in 2 files → move the map next to `AgentState` as the single transition authority.
- [4] `deploy/runners/services.py` is a 395-line god module: sys.path surgery, master-key policy, CORS parsing, Settings class, and 7 app factories in one file → split per-service factory modules.
- [4] services.py re-declares a local `base_settings()` Settings duplicating S1/S2 field sets ("replaces section Settings wholesale") → generate settings from each section's own pydantic class instead of mirroring fields.
- [4] services.py reaches into S3 module globals (`get_ledger_pool`, `pending_usage_tasks`, `set_ledger_pool`) at import time — assembly coupled to S3 internals → expose a lifecycle hook on the router instead.
- [4] CROSS-SECTION VIOLATION: `section4/engine.py:46` imports `harness_router.catalog` directly (S4→S3) behind try/ImportError → route fallback candidates via contracts or injected config, not a peer-section package.
- [4] CROSS-SECTION VIOLATION: `section8/api.py:91` + `tests/conftest.py:127` import `harness_identity` directly (S8→S1); deploy must install section1's src for review to boot → move security_headers into contracts or vendor it.
- [4] `harness_contracts.__version__` says **0.2.0** while `contracts/pyproject.toml` says **0.3.0** — same frozen package, two versions → sync to one source of truth (importlib.metadata).
- [4] `OrchestratorService.runs` dict grows unboundedly for the process lifetime (one entry per run ever touched, incl. rehydrated ones) → add LRU eviction or persist-only mode for finished runs.
- [4] `PlanService.plans/plan_approvals` are unbounded process-global dicts rehydrated from DB but never pruned → cap or back them with the agent_plans table only.
- [4] `app_orchestrator()` creates `worker` inside an `if` then references it unconditionally in both lifespan handlers — refactor or config change can NameError at startup → construct unconditionally, no-op internally.
- [4] `simple_app` vs `app_meeting` vs `app_review` builders copy-paste FastAPI+CORS+health+security-header boilerplate 3× (and app_review drops allow_credentials silently) → one `build_app(title, router, authed=True)` helper.
- [4] app_review's CORS omits `allow_credentials=True` while every other app sets it — cookie flows through :8008 will fail inconsistently → align with simple_app's CORS block.
- [4] `MetricsCollector` (57 lines) + `RequestContextMiddleware` (53) + `_route_template` live inside api.py — observability is ~30% of the file → extract `metrics.py` (S3 already has its own counter; third hand-rolled Prometheus impl).
- [4] Three separate hand-rolled Prometheus text renderers exist (S4 api.py, S3 metrics route, S10 telemetry) → share one tiny metrics module via contracts or accept drift consciously.
- [4] `runs_index.py` duplicates D5 ownership join logic from authz.py as a row filter with a comment admitting reuse → factor the team-scope predicate into authz.py so guard and filter can't diverge.
- [4] S6 `MeetingService` keeps meetings/rooms/briefs/prompts in 4 parallel in-memory dicts with no persistence — restart loses every meeting (S4 fixed this exact class of bug as GAP-H3) → event-source or table-backed store.
- [4] S6 `post_message` ignores its own `PostMessageRequest.userId` body field (principal used) yet the model still declares it — dead wire field invites client confusion → drop from schema like D6 did elsewhere.

## Area 5 — Error handling (silent failures beyond known metering/budget swallow)

- [5] `engine.py:133-149` mutates process env (`DEEPSEEK_API_KEY`) around every live call with save/restore — concurrent runs in other threads read the mutated env mid-flight → pass credentials via constructor kwargs, never os.environ.
- [5] `engine.py:150` dead statement `_ = base64` after importing base64 solely "to use it" — leftover cruft signals unfinished cleanup → delete import + line.
- [5] `service.py:143` `_publish_ordered` swallows predecessor publish failure with bare `pass` — causal-chain breakage is invisible (only the failed link logs) → record chain-break metric/flag on the run.
- [5] `services.py:161` shutdown drain swallows all ledger-write failures with `pass` after 5s timeout — in-flight cost rows silently vanish on restart → count + log dropped writes at WARNING.
- [5] `services.py:281` sandbox stream-event publish failure swallowed with `pass` (no log) — UI terminal silently freezes while execution continues → log once per N failures.
- [5] S5 `service.py:39/75/95` three identical `except Exception: pass` around on_stream callbacks — telemetry loss undetectable → wrap in one helper that logs.
- [5] `billing.py:195` Stripe adapter failure degrades to local quota with `pass` — user sees stale numbers with zero signal that billing backend is down → surface degraded-mode flag in payload.
- [5] `billing.py:130-137` budget override read returns None on ANY exception incl. real bugs (SQL typo = "no override") — silent cap removal → log exception before returning None.
- [5] S2 `oauth_callback` catches ALL exceptions during token exchange and redirects to `?error=<ExceptionClassName>` — root cause (timeout? 400? DNS?) never logged server-side → logger.exception before redirect.
- [5] S2 `connection_status:243` corrupt scopes JSON silently becomes `[]` — tampered/partial rows look like "connected with no scopes" → log + treat as disconnected.
- [5] S2 `oauth_start_tickets` dict entries never cleaned when unredeemed — small unbounded memory growth keyed by attacker-mintable tickets (auth required, so slow) → TTL sweep like oauth_states got.
- [5] S1 `app.py:454` WebSocketDisconnect swallowed with bare `pass` — fine, but the generic handler above it means unexpected WS errors also die silently → add logging for non-disconnect exceptions.
- [5] S1 `eventbus.py:88` malformed pg_notify payload silently skipped (`return`) — one bad row poisons fan-out invisibly → increment a malformed-counter and log at WARNING.
- [5] EventBus `_fanout_local` overflow flag set via `q.overflowed = True` guarded by `except AttributeError: pass` — duck-typed attribute contract with no type anywhere → declare Queue subclass with the attribute.
- [5] S4 `api.py:156` metrics route-template fallback loops app routes swallowing every match error with `continue` — a broken route table yields constant "unmatched" labels with no signal → debug-log first failure.
- [5] `confinement.py:149` landlock apply failure returns False and runner falls back to policy-only while execution proceeds — sandbox downgrade is a WARNING nobody reads → fail-closed option flag (HARNESS_REQUIRE_CONFINEMENT=1).
- [5] `local_runner.py:44-56` requested seatbelt/landlock unavailable ⇒ logs "running unconstrained" and continues — same fail-open pattern as above, invisible to the caller → return confinement mode in TaskHandle and assert in tests.
- [5] S6 `chat.post_message` AI-invocation reply errors would propagate AFTER the human message was already appended — partial success state, no rollback → wrap AI step in try and mark message metadata.
- [5] S8 `get_review_data` PR-meta fetch catches only PermissionError/RuntimeError — httpx timeouts/JSON errors crash the whole review page → broaden to httpx.HTTPError with pr_meta={} fallback.
- [5] `run_queue.claim_next_run` subquery counts running claims WITHOUT FOR UPDATE SKIP LOCKED on the outer row — under PG two workers can serialize on the same row update but the count window still admits >cap bursts ([INFERENCE] needs PG load test) → switch to SKIP LOCKED claim pattern.
- [5] `heartbeat_run` returning False (claim lost/stolen/reclaimed) is ignored by `_execute`'s heartbeat loop — a zombie engine keeps burning tokens after orphan reclaim → cancel engine task when heartbeat stops matching.
- [5] `poll_once` drops claims for PAUSED/unapproved runs with only `logger.error` — runs vanish from queue with no event emitted to the bus/UI → emit ENGINE_FAILED or BLOCKED reason event.

## Area 6 — API contract residuals (WS drift + OpenAPI response models)

- [6] Hand mirror `contracts/ts/types.d.ts` header says v0.2.0 and its EventType union lacks `terminal_output/diff_progress/engine_completed/engine_failed` — generated `ts/src/*` is current but the stale hand file sits beside it → delete types.d.ts or regenerate header, two sources of truth again.
- [6] TS `EventEnvelope.payload` is optional (`payload?:`) while Python EventEnvelope has `default_factory=dict` (always present) — clients branch on `payload ?? {}` forever → make both sides required-present.
- [6] `contracts/ts/src/api.ts` embeds S4's AgentState enum by hand with a comment admitting "Flagged, not fixed" drift from contracts PlanState — runtime status vocabulary owned by nobody → move AgentState into contracts (py+TS) properly.
- [6] No typed per-event payload schemas anywhere: payloads are `dict[str,Any]` on the wire, keys governed only by state_machine.py conventions (Appendix A admits golden fixtures pin placeholders) → define pydantic payload models per EventType in contracts.
- [6] `deriveView.ts` casts payload fields blindly (`payload.question as string`, `findings.summary as string`) — one server key rename breaks UI silently at runtime → zod-validate envelope payloads against contracts.
- [6] S7 `useEventStream` treats EVERY incoming message as an EventEnvelope (`JSON.parse(...) as EventEnvelope`) — server error frames/close-reason changes surface as undefined.seq NaN poisoning lastSeqRef → validate shape before merge.
- [6] WS close codes are a folk contract: 4401/4403/1013 appear only in S1 server code; TS side has zero constants/tests for them → export WS_CLOSE_* constants from contracts/ts.
- [6] OpenAPI census: of 88 routes across 8 services only ~8 expose concrete response schemas (S1: 0/21, S2: 0/13, S6: 0/12, S10: 0/10, S8: 1/7, S4: 2/14, S3: 3/7, S5: 2/4) → response_model everywhere clients consume.
- [6] S1 identity — the service every other section authenticates against — publishes fully untyped OpenAPI (17 bare dict routes incl. /me, projects, ws-ticket) → highest-value single fix in this audit.
- [6] S8 defines ReviewDataResponse but 5 of 7 routes still return free-form dicts (approve/request-changes/comments/risks/deep-link) → finish the job, one response model each.
- [6] S4 done-signal returns `{runId, findings}` where findings mixes camelCase aliases (syntaxOk) with plain snake_case leftovers (`modelUsed` set via attr, `issues`) — alias discipline half-applied → single by_alias dump path tested end-to-end.
- [6] S6 error mapping inconsistent: approve_brief maps ValueError→400 but reject_brief has NO ValueError branch — double-reject returns 500 instead of INVALID_STATE → copy the except clause.
- [6] S2 OAuth endpoints return RedirectResponse/HTML-ish shapes documented nowhere in OpenAPI (response_class unset) → declare responses= so SDK consumers know.

## Area 7 — Concurrency

- [7] Two-actor approve/reject is last-write-wins with no actor/version stamping: reject after approve silently flips plan_approvals False while EXECUTING may already be enqueued → gate transitions on current SM state, reject only from AWAITING_PLAN_APPROVAL.
- [7] `reject_plan` calls `sm.reject_plan` which NEVER validates prior state — rejecting mid-EXECUTION resets plan_approved under a running engine (start_execution already passed its check) → state-guard reject_plan like pause().
- [7] Done-signal double-click: `signal_done` transitions AWAITING_DONE_SIGNAL→REVIEWING unconditionally; second click from REVIEWING re-runs reviewer and emits duplicate DONE_SIGNAL_RECEIVED + review events → idempotency guard on current state.
- [7] `sm.reviewer_passed` flips state REVIEWING→EXECUTING without checking current state — a stop() between signal_done and review completion gets overwritten back to EXECUTING → conditional transition.
- [7] `submit_human_edit` cancels the live task but leaves run_data["task"] pointing at the cancelled task; poll_once's `live.done()` check races the CancelledError delivery → await task death before returning from submit_human_edit.
- [7] Meeting message ordering: ChatMessage.timestamp is client-independent `datetime.now()` at append time; two concurrent posts can append out-of-clock order and list_messages sorts by nothing — history order is list-order only → monotonic per-room sequence number.
- [7] MeetingService is sync def routes on the shared event loop — post_message→invoke_ai→summary generation blocks ALL sections' requests in the single-process deployment → asyncify or offload to thread.
- [7] `enqueue_run`'s orphan-requeue UPDATE resets claimed_at but doesn't check the SM isn't already EXECUTING in another live worker — resume storm after reclaim_orphans + rapid human resume can double-spawn ([INFERENCE] poll_once live-task guard is per-process only) → cross-process spawn fence (e.g. lease token echoed by worker).
- [7] `recover_orphans` runs ONCE at worker start only — a worker paused (SIGSTOP) >90s then resumed never re-scans; orphans sit until next full restart → periodic reclaim inside _loop.
- [7] `_budget_gate` read-then-spawn is TOCTOU: two queued runs for one team pass the cap simultaneously (both see spent<X, both spawn) — cap is advisory under concurrency ([INFERENCE], matches docstring "advisory at storage layer") → reserve row in cost_ledger or serialize per-team claims.
- [7] `os.environ` credential swap in engine._run_live (also area 5) is the only true cross-thread corruption vector in the execution path — asyncio.to_thread makes it real, not theoretical.
- [7] MetricsCollector uses threading.Lock correctly but METRICS is a module global replaced by none — configure_orchestrator swaps _service globally without any lock while requests read it → document single-threaded-wiring assumption or use a cell object.

## Area 8 — Dependency health

- [8] deepseek-harness-sdk pinned 0.1.1rc1 ONLY in Dockerfile; section4/pyproject doesn't declare it at all — local dev silently degrades to stub engine and tests never exercise the SDK path → declare optional extra `[project.optional-dependencies] engine`.
- [8] 0.1.1rc1 is a pre-release pin of a 2026-08-13 project with no lockfile anywhere (zero requirements*.txt / uv.lock / poetry.lock repo-wide) → adopt uv.lock or pip-compile per section.
- [8] All deps are open-ended floors (fastapi>=0.111, sqlalchemy>=2.0, asyncpg>=0.29, pydantic>=2.7): fresh clone today installs newer majors-breaking-minors than CI ever saw → upper-bound or lock.
- [8] asyncpg>=0.29 floor contradicts the codebase's own dual-path handling of add_listener coroutine-vs-sync (eventbus._attach_listener supports BOTH ≥0.30 and older) — floor should be >=0.30 since that's what prod runs.
- [8] section5 declares docker>=7.0 but imports it NOWHERE (local_runner spawns subprocesses) — dead dependency shipping the whole docker-py stack into the sandbox image → remove or implement the docker isolation it implies.
- [8] Next.js pinned `^15.0.0` and React `^19.0.0` — caret allows minor churn but no lockfile-audit story beyond package-lock; Next 15.x had multiple security releases since → enable Dependabot/Renovate (none configured).
- [8] CodeMirror is NOT legacy here: codemirror ^6.0.1 + @codemirror/state/view ^6.x are current-gen — no abandoned dep found; the risk is the reverse (hand-rolled wrapper `codemirror.ts` untested against CM upgrades) [verified, no finding].
- [8] Zero LICENSE file at repo root (AGENTS.md asserts MIT for dsh only) — internal platform has no license declared at all → add LICENSE + per-package license headers.
- [8] No dependency license inventory: pip-audit runs continue-on-error WARN in CI and gitleaks covers secrets, but no license scan of the vendored/installed tree → add pip-licenses report to nightly probe.
- [8] cryptography>=42 floor is broad (42→current major 45+); Fernet usage is stable but ABI-sensitive wheels on py3.13 deserve a tested range → pin tested minor in CI matrix.
- [8] httpx>=0.27 used for GitHub OAuth + S3 probes + S10 probes — trust boundary (token exchange) rides an auto-updating floor dep → include in lockfile discipline above.

## Area 9 — Test coverage gaps

- [9] Section2 = 19 tests, thinnest suite, yet owns OAuth + token vault + merge guards: zero tests for scope escalation (repo read:user only), token refresh/expiry, or getPRDiff error paths → add adversarial OAuth matrix.
- [9] S2 has no test that github_connections rows are actually encrypted-at-rest readable round-trip through KeyVault with a rotated master key → key-rotation test missing (S1 KeyVault has one; S2 consumer doesn't).
- [9] Zero E2E of the hero journey (login→project→plan→approve→execute→done→review→PR) spanning two+ services — integration/test_checkpoint1.py is a single checkpoint file, not the journey → scripted compose E2E in nightly probe.
- [9] conftest.py:43 `test_contracts_version_frozen` asserts `__version__ == "0.1.0"` but the package exports 0.2.0 — the pin test is DEAD (fails if collected) and proves version assertions aren't in the loop → fix value or drive from pyproject.toml.
- [9] Migration upgrade path untested: migrate.py applies 0001→0012 but no test ever starts from 0001 and walks forward asserting each step (the splitter bug in GAP Part 5 #3 was exactly this class) → fresh-PG migration walk test in nightly.
- [9] No downgrade/idempotency check for migrations either (re-run migrate twice = ?) → idempotency assertion test.
- [9] All concurrency tests serial: S4 stress dir contains zero gather/create_task/TaskGroup usage (grep-verified) — double-start guard, publish chains, queue contention never raced → property/race tests with N workers × M runs.
- [9] run_claims claim SQL has no multi-worker test even at store level (single claim_next_run calls in sequence) → two-factory interleaved claim test on sqlite+PG.
- [9] No test covers heartbeat-loss → zombie-engine cancellation because the cancellation DOESN'T EXIST (see area 7) — write the failing test first.
- [9] S6 meeting area: 38 tests but none cover concurrent message posting or AI-reply ordering; room history assumed append-safe → threading smoke test.
- [9] S7 vitest covers units + stress but no component test renders WS reconnect (1013 close → ticket remint → replay) end-to-end → fake-server integration test for useEventStream.
- [9] No contract test pins WS close codes 4401/4403/1013 — server can renumber freely and every client breaks → conformance fixture in contracts/ts wire.test.ts.
- [9] S10 has NO venv and runs tests only via PYTHONPATH=src recipe (documented workaround, STATUS admits it) — its 15 tests don't run in the standard per-section flow → give section10 a pyproject dev extra + venv like siblings.
- [9] Golden fixtures pin `{"fixture": true}` placeholders instead of real payload shapes (Appendix A admission) — payload drift between SM and fixtures is undetectable → pin actual payload keys.
- [9] No performance/regression budget anywhere: metrics endpoint exists but no test fails on latency/ordering regressions (publish chains were added precisely for such a bug) → assert seq monotonicity under load in stress suite.

## Cross-cutting observations

- The three confirmed atomicity wins (invite redeem, rate_buckets upsert, oauth_states DELETE…RETURNING) show the pattern works; run_claims is now the LAST unfenced hot path — prioritize the SKIP LOCKED conversion there.
- Version-truth rot is systemic: contracts __init__ 0.2.0 vs pyproject 0.3.0 vs types.d.ts header 0.2.0 vs conftest pin 0.1.0 — four files, three different answers → single source + generated mirrors.
- Fail-open is the house style (budget, limiter, confinement, billing adapter). It's documented and deliberate in each case, but NOTHING aggregates these degradations — one `/health?deep=1` style degradation endpoint listing active fail-open states would convert silent risk into visible ops signal.

*End of raw report — 88 findings.*
