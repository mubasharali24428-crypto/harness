# Brutal Audit — Orchestrator/Engine (Section 4 + engine/deploy surfaces)

**Scope:** `/Users/user/untitled folder 2/harness` — `section4/src/harness_orchestrator/*`, `contracts/src/harness_contracts/*`, `deploy/docker-compose.yml`, `section1` schema, `section3` ledger. Read-only analyst pass (re-dispatch #3); all line numbers verified directly against source this session.

**Verdict up front:** the orchestration layer *looks* governed (roles, budget gates, honest-failure events) but three pillars are hollow in production wiring: **budget caps aggregate a table nobody writes to**, **the reviewer passes anything it isn't explicitly shown**, and **the lifecycle has no real terminal path except "stop."**

---

## CRITICAL

### C1. Budget enforcement is inert end-to-end (zero writers + fail-open + TOCTOU)
- **Where:** `section3/src/harness_router/api.py:77` (only `record_usage` caller in repo); `section4/src/harness_orchestrator/budget.py:30` (reads `cost_ledger`); `section4/src/harness_orchestrator/service.py:155–181` (`_budget_gate`), `:613–623` (`signal_done` pre-check); `deploy/runners/services.py:151–154` (pool wired, never fed).
- **Scenario:** Direct DSH engine runs (`service._execute` → `engine.execute_goal`) never insert into `cost_ledger` — verified: no `record_usage` call outside Section 3's router API. The orchestrator's own reviewer/model calls don't meter either. `check_budget` therefore sums **0.00 forever** for the primary workload. Compounding: `_budget_gate` explicitly degrades to *allowed* on any budget-read failure (docstring line 160, return-path 163–167), and the check-then-spend pattern is non-atomic — N concurrent same-team runs each pass the gate, then all spend; the cap is overshot between check and call. Spawn-time enforcement *does* exist (both `poll_once`:590 and `_execute`:449 gate before spawning — the earlier "spawn-time unchecked" suspicion is disproven), but it guards an empty ledger.
- **Fix:** Meter at the engine boundary — wrap `execute_goal` and the reviewer `call_model` to write `cost_ledger` rows (run_id, model, tokens×price) via the existing S3 pool; make budget-read failure *deny* (or use a cached ceiling) instead of allow; enforce atomically with `SELECT … FOR UPDATE` on a per-team spend row or a conditional spend-increment RPC.

---

## HIGH

### H1. Silent forced model downgrade violates AGENTS.md
- **Where:** `section4/src/harness_orchestrator/engine.py:35–54` (config), `:92–105` (`_run_live_with_fallback`); `AGENTS.md:32` ("No forced model downgrade"); `deploy/docker-compose.yml:14`.
- **Scenario:** `live_mode = bool(api_key and model)` counts the compose-default literal `"ollama"` as an API key. On primary-model failure the ladder silently walks `DSH_FALLBACK_MODELS` env or `catalog.local_fallback_candidates()` — a request approved for `deepseek-chat` can be answered by whatever local model the catalog lists, with no operator consent and no surfaced warning beyond a changed engine string. This is exactly the "forced downgrade" AGENTS.md forbids.
- **Fix:** Make fallback opt-in per run/plan (`plan.allowFallbackModels`), require explicit env for the list (kill the catalog last-resort), and emit an `ENGINE_DOWNGRADED` event recording primary→actual.

### H2. Deploy defaults pin Ollama and eat overrides on recreate
- **Where:** `deploy/docker-compose.yml:14–17` — `DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-ollama}`, `DEEPSEEK_BASE_URL: …host.docker.internal:11434/v1`, `DSH_MODEL: qwen3.5-9b-128k…`.
- **Scenario:** A `docker compose up -d --force-recreate` where the host shell didn't export the vars silently reverts every container to the local-Ollama placeholder key + qwen model. Runs then either hit the wrong endpoint or fall through H1's ladder. (Confirmed by ops analyst; defaults re-verified here.)
- **Fix:** Remove sentinel defaults (`${DEEPSEEK_API_KEY:?required}`), move deployment config to a versioned env file, add a startup assertion that logs the resolved base_url/model and refuses placeholder keys in "production" mode.

### H3. Empty-diff review passes vacuously; the diff itself is client-supplied
- **Where:** `section4/src/harness_orchestrator/api.py:303–304` (`DoneSignalRequest.diffText = ""`), `:420–424`; `service.py:604`, `:625–629`; `section4/src/harness_orchestrator/reviewer.py:36–52`.
- **Scenario:** `POST /runs/{id}/done-signal` with `{}` → `diff_text=""` → heuristics find no `<<<<<<< HEAD`, no `SyntaxError` → `passed=True`. The pass/fail gate is pure substring matching; there is no requirement that any diff exist at all, and the caller (agent *or* any reader-role user, see H4) chooses what text gets reviewed — omit the broken hunk, pass the review. `brief_aligned` exists in the schema (reviewer.py:13) but is never computed — goal alignment is decorative.
- **Fix:** Reject done-signals with empty diffs (400) unless the run recorded zero changes server-side; derive `diff_text` from the workspace/VCS instead of trusting the request body; compute `brief_aligned` or drop the field.

### H4. done-signal is ungated: any state, any reader, infinitely repeatable
- **Where:** `api.py:420–424` (`actor: RunReader`); `state_machine.py:111–115` (`signal_done()` has **no state precondition** — `transition_to` is an unconditional assignment, :46–47); `service.py:604–608` (cancels the live task, no state check, no idempotency key).
- **Scenario:** Three failures in one endpoint. (a) **Role asymmetry:** a viewer cannot pause/resume/edit (`RunWriter` gates) but *can* fire done-signal — which cancels the running engine task (`_cancel_live_task`, service.py:608) and drives the state machine. Lowest-privilege role holds a kill switch. (b) **Double-POST while REVIEWING/after:** no dedupe — each call re-runs the reviewer (a real model spend when the router is wired) and re-emits `DONE_SIGNAL_RECEIVED`. (c) **Resurrection:** fired on a `DONE` (stopped) or `BLOCKED` run, it flips to `REVIEWING` → possibly `EXECUTING`, bypassing plan-check and resume semantics entirely.
- **Fix:** Guard `sm.signal_done()` to legal source states (`AWAITING_DONE_SIGNAL`, `PAUSED` w/ human-edit, maybe `REVIEWING` for idempotent replay-return); require `RunWriter`; make repeat signals within a review return cached findings (idempotency keyed on run_id+review-generation).

### H5. No terminal path: REVIEW_PASSED → EXECUTING is a zombie dead-end
- **Where:** `state_machine.py:117–119` (`reviewer_passed` → `transition_to(EXECUTING)`); `service.py` — no handler spawns work on `REVIEW_PASSED`; the only `finish()` call is `stop_agent` (:553). `open_pr`/`DONE` are unreachable via the API surface.
- **Scenario:** Review passes → state machine says `EXECUTING` but `run_data["task"]` is None and the queue claim was already deleted (`_execute.finally` → `finish_run`). Status endpoint reports "executing" forever with nothing executing. The oscillation isn't an infinite *work* loop (nothing respawns), it's worse: a permanent lie. The only exits are `stop` (which skips `PR_OPENED` entirely) — so `PR_OPENED` and a legitimate `DONE` are dead states.
- **Fix:** `reviewer_passed` should advance to a `VALIDATED`/`PR_OPENED`-bound transition (or re-enqueue a finalize stage), and `get_status` should derive `executing` from an actual live task, not the SM enum alone (it partially does — but the SM state itself is what UIs render).

### H6. Plan rejection leaves no trace: no event, no audit row → state resurrection after restart
- **Where:** `state_machine.py:65–67` (`reject_plan` emits **nothing**, unlike `approve_plan`:58–63); grep-verified: **zero** `write_audit`/`audit_log` references in all of `section4/src`; persistence is `agent_plans.status` mirror only (`service.py:657–666`) while `_REHYDRATE_SQL` (:30–35) replays `agent_events` as "source of truth."
- **Scenario:** User rejects a plan → SM goes IDLE silently. Process restarts (or another replica serves the next request) → `_ensure_run` rehydrates from `agent_events`, last event is `PLAN_PROPOSED` → run presents as `awaiting_plan_approval` again. The rejection — a governance decision — is invisible on the event bus and in `audit_log` (Section 1's append-only table, `section1/migrations/0001_initial.sql:89`, goes completely unused by the orchestrator: no rows on approve/reject/start/signal_done/stop).
- **Fix:** Emit `PLAN_REJECTED` (payload already has the rejecter's id — it's accepted then dropped), map it in `_EVENT_STATE_MAP` (service.py:46–58), and write `audit_log` rows for approve/reject/start/done/stop via S1's `write_audit`.

---

## MEDIUM

### M1. Per-team concurrency cap is advisory under concurrent claimers
- **Where:** `section4/src/harness_orchestrator/run_queue.py:53–91` (`_CLAIM_SQL` count-subquery + `claim_next_run`).
- **Scenario:** Fairness is fine (FIFO by `claimed_at`, set at enqueue). But the `< :max_per_team` guard is a plain COUNT inside a single-statement UPDATE — under READ COMMITTED, two workers polling concurrently both count the same N running rows, both see headroom, both claim → team runs N+2. With `HARNESS_RUN_CONCURRENCY=4` (deploy/runners/services.py:193) and multiple replicas this is the normal case, not an edge case.
- **Fix:** Advisory-lock the claim per team (`pg_advisory_xact_lock(team_id)`) or maintain a counter row per team updated in the same statement.

### M2. Claim-ownership races around orphan reclaim (zombie deletes successor's claim)
- **Where:** `run_queue.py:94–108` (heartbeat guarded by worker_id ✓) vs `:111–116` (`finish_run` deletes **by run_id regardless of worker_id**); `service.py:28` (heartbeat 10 s), `:562–566` + worker.py:20 (reclaim at `stale_seconds=90`), `:488–492` (`finally: finish_run`).
- **Scenario:** Worker A's heartbeat stalls 90 s (GC pause, event-loop block) → claim orphaned → re-enqueued → Worker B claims and spawns. A's original `_execute` eventually completes and its `finally` deletes the `run_claims` row — B's row — leaving B's live execution untracked (heartbeats no-op, orphan detection blind, and a third enqueuer can start a *third* copy since the row is gone). Two engines can also legitimately double-execute the same goal in the reclaim window because nothing kills A's task on reclaim.
- **Fix:** `DELETE … WHERE run_id=:r AND worker_id=:w`; on reclaim, publish a cancel signal (event-bus `RECLAIMED`) the stale task checks; refuse re-enqueue while the previous task generation hasn't been reaped.

### M3. Resume-after-BLOCKED is an unlimited free retry loop
- **Where:** `service.py:508–547` (`resume_agent`: plan-check + `sm.resume()` + respawn), `state_machine.py:96–100`; failure landing spots `state_machine.py:85–89`, `:121–123`.
- **Scenario:** Engine fails all models → `ENGINE_FAILED` → BLOCKED. Any writer can immediately POST resume → same goal, same ladder, same failure — forever, with no attempt counter, no backoff, no escalating review. Same for `reviewer_failed` → BLOCKED → resume → re-execute full goal. Each cycle is real compute (and would be unbilled per C1).
- **Fix:** Persist `attempt_count` per run (in `agent_runs` or the event payload); after N failures require `approve`-role to resume, and add exponential backoff on `ENGINE_FAILED` (vs reviewer failures, which arguably shouldn't auto-respawn the engine at all).

### M4. `agent_events` unbounded: no retention, replay-per-cold-lookup, ever-growing memory dict
- **Where:** `section1/migrations/*` (indexes added — `0002`, `0012` — but no pruning job anywhere; only backup scripts mention the table); `service.py:30–35` (`_REHYDRATE_SQL` loads the run's **entire** event history on every cache-miss `_ensure_run`); `service.py:225–248` (`self.runs` dict never evicts).
- **Scenario:** Long-lived deployments: every cold touch of an old run replays its full history; the in-process dict grows with every run ever touched until restart. Combined with pg_notify fan-out on every insert (`0003_pg_notify.sql`), a chatty fleet degrades linearly.
- **Fix:** Snapshot/rename strategy (compact events older than T into per-run checkpoints), `LIMIT`-ed rehydration reading tail-first, and an LRU cap on `self.runs`.

---

## LOW

### L1. Result misattributes the model that actually answered after downgrade
- **Where:** `engine.py:76–81` (`_engine_name` reports `self.model`) vs `:92–101` (candidate passed per-call precisely to avoid mutating `self.model`).
- **Scenario:** Primary fails, fallback succeeds → returned `engine` field says `deepseek-harness-sdk:<primary>` while the text came from the fallback. Downstream cost attribution and audits keying off this field are wrong. Fix: return the winning candidate in the result dict.

### L2. `propose_plan` has identity but no role gate
- **Where:** `api.py:330–334` (`principal: RequirePrincipal`, never role-checked; ownership checked only when `projectId` supplied, :337–347).
- **Scenario:** Guest/viewer principals can mint plans and create `AWAITING_PLAN_APPROVAL` noise (and bind runs to their team's projects). Harm alone, but inconsistent with the write/approve matrix everywhere else. Fix: `RunWriter` dependency.

### L3. Human-edit role gate is correct — but document the asymmetry it creates
- **Where:** `api.py:412–414` (`submit_human_edit` → `RunWriter`); `contracts/src/harness_contracts/roles.py` (`READ_ONLY` denied write). Verified working as intended — the *gap* is that done-signal (H4) uses `RunReader`, so the permission matrix lets a viewer trigger the exact same pause-and-review flow the viewer can't invoke directly. Fixing H4 resolves this; until then the matrix is incoherent.

### L4. Reviewer heuristics produce false positives/negatives by construction
- **Where:** `reviewer.py:41–47`.
- **Scenario:** A legit docs change containing a quoted `>>>>>>>` fails review; a diff with a runtime-only break passes. Acceptable as a triage gate, dangerous as the sole quality gate — worth a comment/ROADMAP note distinguishing heuristic-gate from model-review once metering exists (see C1).

### L5. Module-global mutable service singleton in the API layer
- **Where:** `api.py:30` (`_service = OrchestratorService()`), `:231–234` (`configure_orchestrator` swaps the global).
- **Scenario:** Deploy wiring mutates shared module state post-import; tests that import the router get whichever service was configured last. Fix: app-state injection (`request.app.state.orchestrator`) like `get_principal` already does for auth.

---

## Verified-clean items (audited, no finding)
- **Spawn-time budget gate exists**: both `start_execution`-spawned tasks (`_execute`:449) and queue-worker spawns (`poll_once`:590) call `_budget_gate` before spawning — the earlier suspicion of a missing spawn-time check was wrong (the *real* budget problem is C1).
- **Approve-plan identity**: approver id comes from the session token, never the body (`api.py:358–362`) — forgeable-approver attack is closed; it's only the *persistence* of that decision that's broken (H6).
- **Team-binding on propose** (D5): foreign-team projectId rejected with 403 (`api.py:337–347`, `_project_belongs_to_team`:312–327).
- **Auth-not-wired fails closed**: missing principal resolver → 503, not open access (`api.py:244–250`).
- **Empty engine response counted as failure** (Wave 6 fix holds, `engine.py:87–105`), and injected-backend/stub modes never fake success (`status="not_configured"` → BLOCKED honestly).

## Finding index
| # | Sev | Title | Anchor |
|---|-----|-------|--------|
| C1 | CRITICAL | Budget caps inert: no writers, fail-open, TOCTOU | service.py:155–181, 613–623 |
| H1 | HIGH | Forced model downgrade vs AGENTS.md | engine.py:38–50, 92–105 |
| H2 | HIGH | Compose pins Ollama; overrides lost on recreate | docker-compose.yml:14–17 |
| H3 | HIGH | Vacuous empty-diff review pass; client-supplied diff | api.py:304; reviewer.py:41–52 |
| H4 | HIGH | done-signal: any state, reader role, no idempotency | api.py:420–424; sm:111–115 |
| H5 | HIGH | REVIEW_PASSED → zombie EXECUTING; DONE/PR unreachable | sm:117–119 |
| H6 | HIGH | reject_plan: no event/audit → state resurrection | sm:65–67; zero S4 audit writes |
| M1 | MEDIUM | Per-team cap race under concurrent claimers | run_queue.py:53–91 |
| M2 | MEDIUM | Orphan-reclaim ownership races; finish_run deletes any claimant's row | run_queue.py:111–116 |
| M3 | MEDIUM | Unlimited BLOCKED→resume retry loop | service.py:508–547 |
| M4 | MEDIUM | agent_events unbounded; full replay; leaky runs dict | service.py:30–35, 225–248 |
| L1 | LOW | Post-downgrade model misattribution | engine.py:76–81 |
| L2 | LOW | propose_plan lacks role gate | api.py:330–334 |
| L3 | LOW | Role-matrix asymmetry (edit=writer, done-signal=reader) | api.py:412–414 vs 421 |
| L4 | LOW | Substring reviewer false pos/neg | reviewer.py:41–47 |
| L5 | LOW | Global service singleton mutated at deploy | api.py:30, 231–234 |

*16 findings (1 critical, 6 high, 4 medium, 5 low) + 5 verified-clean controls. No repository files were modified.*
