# Testing & QA-Coverage — Brutal Audit (2026-08-23)

Scope: `tests/` across all sections + `integration/`, conftest patterns, what is
tested vs what ships untested. Read-only analyst pass; every claim below was
verified against the tree (file:line) or by actually running the suites.

**Verified live during this audit:** S4 suite = 134 passed, 1 deselected in
2.70s; S1 = 134 passed in 2.76s; `tests/conftest.py::test_contracts_version_frozen`
is **never collected** by the standard recipe and **fails when run directly**
(pins 0.1.0; installed contracts report 0.2.0, pyproject says 0.3.0).

Test-count inventory (functions, verified by grep):

| Section | Test fns | Notes |
|---|---|---|
| section1 | 129 (+1 dead in conftest.py) | SQLite-only locally |
| section2 | 19 | thinnest backend relative to risk |
| section3 | 66 | |
| section4 | 131 | |
| section5 | 119 | |
| section6 | 38 | pure-domain only |
| section7 | 58 vitest its | lib-only, 0 components |
| section8 | 38 | |
| section9 | **0** | no folder on disk at all |
| section10 | 54 | incl. checklist goldens |
| integration/ | 4 | one file, sqlite by default |

---

## F1 — CI/local gate divergence already shipped red: S10's own README recipe is wrong and the "15 tests" gate number is unreachable
- **Where:** `section10/README.md:47` (`./.venv/bin/python -m pytest tests/ -q # 49 tests`) vs reality (54 collected today); STATUS.md Mission-10 header line 40 records the B3 gate as "section10 15"; `.github/workflows/ci.yml` python-tests matrix runs all of S10's `tests/`.
- **Severity:** HIGH (this is the exact class of failure that produced the known CI-red incident: local lane gates on a subset/recipe, CI runs the whole tree).
- **Evidence:** The B3 lane gated on `tests/test_steps.py` (15 steps) while CI's matrix job executes `pytest tests/` = unit + conformance + fixtures generator imports. When that ran for the first time it went red and needed patch commit `582c044` ("CI red — add /checklist golden fixture + case"). Nothing changed since to prevent a repeat: there is still no single documented command that equals what CI runs per section.
- **Could ship broken:** any new S10 test file or fixture drift passes the lane gate and fails CI after merge.
- **Fix:** add a `GATES.md` (or make each section README say exactly `pytest tests/ -q`) and add a tiny CI job step that fails if the recorded gate command differs from the workflow's; assert collected-test-count parity between lane recipes and CI (`pytest --co -q | wc -l` checked in).

## F2 — Dead version-pin "conformance" test hides inside conftest.py and is now lying (0.1.0 pinned; real version is 0.2.0/0.3.0)
- **Where:** `section1/tests/conftest.py:44-46` — `async def test_contracts_version_frozen(): assert harness_contracts.__version__ == "0.1.0"`.
- **Severity:** HIGH.
- **Evidence:** `pytest tests/` collects 134 tests — this one is not among them (conftest.py is not collected as a module). Run directly it FAILS: installed package reports `__version__ = "0.2.0"` while `contracts/pyproject.toml:3` declares `version = "0.3.0"` (three different versions across three files). The AGENTS.md "breaking a contract without version bump is the unforgivable sin" rule is therefore enforced by a test that cannot run and would fail if it could.
- **Could ship broken:** contract bumps land with stale TS mirror / mocks because the pin never executes; the one guard that exists is theater.
- **Fix:** move the test into `tests/test_conformance.py`, pin against a single source of truth (`harness_contracts.__version__` read from pyproject via importlib.metadata), delete the duplicate literals.

## F3 — Zero end-to-end coverage of the actual demo path (login→meeting→brief→plan→approve→run→review→PR)
- **Where:** `integration/` contains exactly one file (`test_checkpoint1.py`, 240 lines, 4 tests) covering S1+S3+S4+S5 only; nothing exercises S2 (GitHub), S6 (meeting/brief), or S8 (review gate) in composition; no test boots `deploy/docker-compose.yml` services or even wires two FastAPI apps over ASGI.
- **Severity:** CRITICAL.
- **Evidence:** grep for compose/supertest/cross-service harness found none; S6↔S4 brief handoff and S8 approve→S4 resume exist only as separate unit suites with hand-built doubles (`section8/tests/conftest.py:47` `FakeGitHubService` mimics S2's client from scratch).
- **Could ship broken:** the one flow the product exists to demo breaks across a seam (e.g. S8's approve payload vs S4's resume endpoint) and all 500+ green tests stay green.
- **Fix:** add `integration/test_full_demo_flow.py`: spin S1/S4/S6/S8 apps in-process via `ASGITransport`, share one SQLite file (the P1-XSTATE pattern), walk login→project→meeting→brief→approveBrief→propose plan→approve→run(stub engine)→review→PR-opened event; wire into the nightly PG probe job first.

## F4 — WS seq-gap / reconnect protocol is half-untested: server side has zero tests for code 1013, client side tests only the pure reducer
- **Where:** Server: `section1/src/harness_identity/app.py:450` closes with `1013` on bus overflow — grep shows **no test asserts 1013 anywhere** (`grep -rn "1013" section*/tests` → empty). Client: `section7/tests/unit/useEventStream.test.ts` tests only `mergeEvents` (dedupe/sort); the hook itself (reconnect loop `useEventStream.ts:60-79`, `afterSeq=` resend, ticket re-mint on every attempt, `onerror` path) is never instantiated in any test — environment is `"node"` (`vitest.config.ts`) with no WebSocket stub.
- **Severity:** HIGH.
- **Evidence:** overflow flag exists (`test_section1.py:165-186` covers the *flag*), but the HTTP-facing consequence (server tells the client to resync via 1013, client replays `afterSeq=lastSeq`) is an untested cross-layer contract; the skill-documented MiniWs driver (`test_ws_auth.py`) was never applied to the overflow path.
- **Could ship broken:** a slow subscriber gets a silently dropped stream forever (no 1013 sent, or client doesn't handle it → stuck "open" UI showing stale events). This is the platform's core liveness guarantee.
- **Fix:** (a) S1: extend `test_ws_auth.py` with a MiniWs case that fills the subscriber queue past maxsize and asserts close code 1013 + that replay-after-reconnect returns the missing events; (b) S7: add a fake-WebSocket harness (`vi.stubGlobal("WebSocket", FakeWs)`) driving `useEventStream` through drop→reconnect→afterSeq-resend; keep `mergeEvents` cases.

## F5 — Migrations 0010–0012 have no upgrade/execution tests; "coverage" is string-matching the SQL file
- **Where:** `section1/tests/test_billing.py:366-395` asserts the migration text *contains* substrings (`"CREATE TABLE billing_plans ("`, tab-indented columns…); `test_notifications.py:485-505` same trick; `test_ratelimit.py:31-35` copies DDL into a string constant "Mirrors migrations/0011_rate_buckets.sql". Migration **0012_agent_events_run_id_index.sql** is referenced by no test at all (repo-wide grep: zero hits).
- **Severity:** HIGH.
- **Evidence:** nothing applies the real numbered SQL files to anything — SQLite `Base.metadata.create_all` builds tables from ORM mirrors, so a typo in a live migration (wrong column name, missing index, bad CHECK) sails through every suite and explodes only on `deploy/runners/migrate.py` at container start; `deploy/runners/migrate.py` itself is referenced by zero tests.
- **Could ship broken:** fresh deploy fails at migrate step, or worse, prod schema diverges from ORM mirrors (drift invisible until a query 500s).
- **Fix:** nightly PG probe job gains a step: apply `0001..0012` in order to a throwaway PG, then run S1 suite against it (`HARNESS_TEST_PG=1`); add `tests/test_migrations_apply.py` executing each file via asyncpg and asserting table+index existence; replace substring assertions with "apply then introspect".

## F6 — Billing flag transitions are tested almost entirely on the happy path; mode flips mid-life and the adapter-failure degrade have thin/no negative coverage
- **Where:** `section1/src/harness_identity/billing.py:95-104` (`_billing_mode` reads env **per request**, no caching), `_state_payload` lines ~183-207 swallow ALL adapter exceptions into silent local fallback. Tests present: flag off (152), enabled-local (177), admin RBAC (236), validation (293), stripe-with-key (313), degrade-to-local-on-adapter-raise (line ~349 comment confirms it exists).
- **Severity:** MEDIUM-HIGH.
- **Evidence of holes:** no test flips `HARNESS_BILLING` between requests in one app instance (flag-off → local → stripe) asserting state-shape transitions and that quota numbers survive; no test that `STRIPE_API_KEY` disappearing mid-flight degrades rather than 500s; no test that PUT `/billing/plan` while flag is OFF behaves sanely (currently it writes rows regardless of flag — is that intended? untested, undocumented); no test for month-boundary rollover of `_month_start_utc()` (billing.py:150).
- **Could ship broken:** billing silently reports local numbers as Stripe numbers after an adapter outage (source field says "stripe"? no — fallback sets "local", but quota provenance after mid-month flip is untested), or plan writes accumulate while flag off.
- **Fix:** parametrized transition test in `test_billing.py`: monkeypatch env across three sequential calls, assert full body shapes; add month-boundary freeze-time test; decide+test PUT-plan-under-flag-off semantics.

## F7 — Concurrency claims rest on serial tests: no concurrent-claim execution anywhere; "stress" suites contain zero concurrency primitives
- **Where:** `section4/tests/unit/test_run_queue.py:120` `test_team_cap` enqueues/claims sequentially; repo-wide grep shows no `asyncio.gather`/`TaskGroup`/thread fan-out on `claim_next_run`; `section3/tests/stress/*` and `section5/tests/stress/*` contain no gather/Thread either (grep count 0); no load tooling (locust/k6), no latency/throughput assertions, no mutation testing (no mutmut/cosmic-ray config), no pytest-cov anywhere.
- **Severity:** HIGH.
- **Evidence:** `_CLAIM_SQL` (run_queue.py:53-71) relies on single-statement atomicity but has **no SKIP LOCKED / FOR UPDATE** — correctness under N workers depends purely on UPDATE rowcount semantics, which SQLite's serialized writer can never falsify. The claim "SKIP LOCKED queue" appears in docs (STATUS/GAP cards) but the SQL doesn't use it; only a PG-backed concurrent test could catch double-claim.
- **Could ship broken:** two workers claim the same run under PG (double engine spawn, double spend) — invisible in CI and locally forever, since the nightly probe also runs single-worker.
- **Fix:** nightly-PG test spawning e.g. 8 workers × 30 queued runs via `asyncio.gather` against real Postgres, asserting each run claimed exactly once (unique worker_ids across claims); fix forward with FOR UPDATE SKIP LOCKED if it fails; add mutmut on `run_queue.py` + `billing.py` as a pilot.

## F8 — Section2 (GitHub) is the thinnest tested surface guarding the most dangerous operation class
- **Where:** `section2/tests/` = 2 files, 587 LOC, 19 tests for 785 LOC of source including `client.py` (raw GitHub REST wrapper) and merge-guard logic (`test_service.py:17-27`). All GitHub traffic goes through httpx MockTransport fakes (`conftest.py:26` `FakeGitHub`); there is no contract test that the fake's shapes match real GitHub API responses, and no test of PR status polling used by S8 (`getPRStatus` is reimplemented independently in `section8/tests/conftest.py:47`).
- **Severity:** MEDIUM-HIGH.
- **Evidence:** the merge-guard tests cover 3 URL shapes; pagination, rate-limit responses (403/429 secondary-limit), renamed default branches, and non-`heads/` ref shapes are untested; the S8-side `FakeGitHubService.getPRStatus` returning `{draft, merged, mergeable}` is asserted nowhere against S2's real response keys — this is precisely the manual-drift class caught twice before (S7 wire.test.ts header admits fixtures were "verified via curl … 2026-08-22").
- **Could ship broken:** S8 approves/blocks reviews based on a drifted PR-status shape; S2 breaks on GitHub API changes discovered only in the live demo.
- **Fix:** record one real captured PR-status JSON (redacted) as a golden shared by S2 and S8 tests (place in `contracts/` — it's a cross-section wire shape); add S2 tests for 429/rate-limit and paginated tree listing.

## F9 — Frontend test pyramid is inverted: 58 vitest tests cover lib helpers only; 0 component tests, 0 a11y automation, 0 visual regression, and section9 has literally nothing
- **Where:** `find section7 -name "*.test.tsx"` → 0; `vitest.config.ts` environment `"node"` (no DOM); covered libs: deriveView/auth/codemirror/apiClients/useEventStream/api/parseApiError + 2 route-level api tests (`billingApi.test.ts`, `onboardingApi.test.ts`); 9 pages × components (`WorkingArea.tsx`, `PlanPanel.tsx`, `TerminalPanel.tsx`, `DiffPanel.tsx`, settings/team/keys/billing/runs/onboarding pages) ship with zero rendering tests. `section9/` does not exist on disk anymore (skill + GAP doc still describe it as mock-based shell) — docs drift too.
- **Severity:** MEDIUM-HIGH.
- **Evidence:** no jsdom/testing-library/@testing-library/react/jest-axe/playwright deps in `package.json` devDependencies (only typescript/types/vitest); no snapshot/visual tooling; the S7 pages render live money/quota/access-control data with no automated check they render at all.
- **Could ship broken:** a runtime crash in any page (bad null from a new API field — see mission-10 "null-quota guard" hotfix b04d515, which was exactly such a break) reaches users; a11y regressions (the team page advertises "a11y roles") are unverifiable.
- **Fix:** add jsdom + @testing-library/react + jest-axe to S7; smoke-render each page with mocked fetch; axe scan on the four settings pages; optional Playwright happy-path against compose for the demo script; delete stale section9 references from docs.

## F10 — S7↔backend contract tests pin 4 of ~26 client functions and were hand-synced from curl, not generated
- **Where:** `section7/tests/conformance/wire.test.ts:11` imports only `fetchWsTicket, replayEvents, requestLoginOtp, verifyLoginOtp` out of `api.ts`'s 26 exported functions; header comment (lines 1-8) says fixtures were "verified via curl against S1/S6/S8 on 2026-08-22" — a manual snapshot, not derived from OpenAPI. Meanwhile S10 proves the better pattern exists (`tests/fixtures/generate_fixtures.py` regenerates goldens from the live app + `--check` gate in CI static-gates for the TS mirror).
- **Severity:** MEDIUM.
- **Evidence:** `apiClients.test.ts` covers meeting/review call *shapes* but asserts request bodies loosely; no test pins orchestrator run-control payloads (pause/resume/stop/human-edit) or the runs-index query params against the frozen conventions; contracts `ts/types.d.ts` mirror is checked for existence, not equality with pydantic models (codegen gap acknowledged in skill: "pydantic→TS codegen later").
- **Could ship broken:** rename/drift in any unpinned endpoint passes both sides' suites (the twice-caught drift class), surfacing only as broken UI in the demo.
- **Fix:** port S10's golden-generator approach: script that boots each backend app in-process, dumps OpenAPI examples/goldens into `contracts/ts/goldens/`, and a vitest conformance that replays them through `lib/api.ts`; add missing endpoints incrementally starting with orchestrator control routes.

## F11 — The only live-model test is a daily-quota landmine wired into the default suite path
- **Where:** `section4/tests/unit/test_engine.py:46-88` `test_live_run_against_openrouter` skips only if the key file is absent; key IS present (`~/.hermes/.env`), so plain `pytest tests/` attempts a real agentic round-trip through OpenRouter (model stealth/ox-alpha) with a filesystem side-effect assertion. No timeout, no retry/backoff, no mark (not marked slow/live/integration), no cost ceiling. STATUS.md line 40 and the wave-6 notes record it failing whole-session gates twice (OpenRouter quota/outage).
- **Severity:** HIGH.
- **Evidence:** tonight's known flake matches: quota exhausted → assertion error inside the S4 suite → everything after it in that lane is noise; deselection required manual knowledge (`--deselect tests/unit/test_engine.py::test_live_run_against_openrouter`).
- **Could ship broken:** red CI/red local gates mask genuine regressions (cry-wolf), burns paid tokens on every dev run, and can hang a lane indefinitely (no timeout on `execute_goal`).
- **Fix:** gate behind env opt-in (`HARNESS_LIVE_PROBE=1`) instead of key-presence; add `@pytest.mark.timeout(240)`; move to the nightly schedule job where retries are cheap; record last-run result in STATUS via a tiny marker file so humans stop rediscovering the flake.

## F12 — Nightly real-PG probe exists in CI but has no local runner and covers 2 of 8 sections; PG-only behavior differences are structurally invisible
- **Where:** `.github/workflows/ci.yml` `nightly-pg-probe` runs only `integration/test_checkpoint1.py` + S1 suite; S4's queue (SQLite-tested, PG-deployed), S1 rate limiter (portable-SQL-by-luck, `ratelimit.py:60` comment admits SQLite exercise is a proxy), and S2 OAuth state store all have PG-specific semantics with no PG-mode tests. Locally, the recipe lives only in prose (STATUS.md:429, runbook:171) and needs a running compose PG; nothing scripts it.
- **Severity:** MEDIUM-HIGH.
- **Evidence:** asyncpg-vs-string binding bug (P1-QUEUE card) and the TIMESTAMPTZ reject were both found manually against compose PG — the exact category the nightly should automate; the ISO-text-everywhere convention is a workaround for never testing both dialects.
- **Could ship broken:** dialect-specific breakage ships because CI's nightly is narrow and locals never run PG paths.
- **Fix:** make `nightly-pg-probe` run S2/S4/S8 suites with `HARNESS_TEST_PG=1` too (they're designed portable — verify); add `deploy/scripts/pg-probe.sh` wrapping compose-up-pg + migrations + suites so the local recipe is one command; alert on nightly failure (schedule jobs fail silently otherwise — nobody watches).

## F13 — Golden fixtures are copy-pasted per section instead of shared; seeding is inline SQL strings duplicated in conftests
- **Where:** five near-identical `golden_fixtures.py` files (S3 23 LOC / S4 22 / S5 37 / S6 21 / S8 71) each re-declaring their own GOLDEN_* constants; `section8/tests/conftest.py:20-45` seeds teams/users/projects/agent_runs/pull_requests via raw INSERT strings with hardcoded `'t1'/'u1'` ids — the same shape S1 helpers build differently (`helpers.py` login()); no factory library, no `factory_boy`, no shared `tests_common` package despite `contracts/` existing as the natural home.
- **Severity:** MEDIUM.
- **Evidence:** S8's seed must be edited by hand whenever the frozen schema grows a NOT NULL column (it already hardcodes `expires_at '2100-01-01T00:00:00Z'` to dodge expiry logic); every new section repeats the ritual (S10 invented its own JSON-store fixtures instead).
- **Could ship broken:** schema change forces 5-file whack-a-mole; a missed conftest yields confusing failures far from the cause; golden constants drift between sections claiming the "same" canonical entities.
- **Fix:** move canonical golden entity ids/shapes into `contracts/src/harness_contracts/testing.py` (or a small `harness_testkit` package installed as dev-extra everywhere); provide `seed_project(factory)` / `make_principal(role)` factories; migrate S8's raw SQL first (most brittle).

## F14 — Suite-health blind spots: no timing budget, no flake quarantine, session-scoped `event_loop` fixture deprecated, and warning spam ignored
- **Where:** every conftest defines the deprecated `event_loop` session fixture (S1 `conftest.py:9-13`, S2:16-23, S8, integration) which pytest-asyncio now warns about and which caused historical hangs per the skill's TestClient note; S4 emits 113 warnings per run (observed: "Event loop is closed" thread exceptions printed during teardown of `test_resume.py`); S4 wall-clock is fine locally (2.70s measured) contradicting the "~25s" belief — meaning whatever made it feel slow was the LIVEPROBE network hang, reinforcing F11; no `-p no:randomly`/random-order plugin, so hidden inter-test order dependencies (shared `~/.hermes/.env` reads, cwd assumptions) remain undetected.
- **Severity:** MEDIUM.
- **Evidence:** teardown warnings show real resource leakage (unclosed portals/tasks) that will intermittently hang CI like the documented 15-minute hang; durations output shows top items are teardowns (0.45s × 3), i.e., cleanup cost dominates — a smell.
- **Could ship broken:** intermittent CI hangs return under load; order-dependent failures appear only on someone's machine.
- **Fix:** adopt pytest-asyncio strict mode + loop-scope fixtures, kill `event_loop` overrides; add `-W error::PytestUnhandledThreadExceptionWarning` triage; add pytest-randomly in CI (quarantine failures); set a per-suite duration budget check (fail > 30s without a `slow` mark).

## F15 — S6 (meeting area) tests never touch HTTP: routers, auth wiring, and principal dependency are exercised by zero S6-level tests
- **Where:** `section6/tests/unit/` = ai_participant (pure prefix matching), brief_generator, chat service objects; no `tests/conformance/` API suite unlike every other section (S3/S4/S5/S8/S10 all have conformance dirs; S6 has only `unit/` + `fixtures/`). Yet D6 rewired S6 routes to fail-closed `get_principal` — that wiring is only validated indirectly via S7's `apiClients.test.ts` request-shape guesses.
- **Severity:** MEDIUM.
- **Evidence:** `ls section6/tests` → conformance dir absent; grep for `AsyncClient|TestClient|ASGITransport` in S6 tests → none; the meeting endpoints (create/message/invoke/brief CRUD) have no status-code, auth-envelope, or golden tests at the section that owns them.
- **Could ship broken:** an S6 route loses its auth dependency or changes its envelope and no S6 gate notices (S7's loose shape tests might not either).
- **Fix:** add `section6/tests/conformance/test_api_conformance.py` mirroring S5's: golden request/response for each route, 401-without-token, spoofed-body-userId-ignored regression (D6 follow-through), plus @ai-invocation gating at the API layer.

---

## Cross-cutting summary

| Theme | Findings |
|---|---|
| Gates lie (local ≠ CI) | F1, F14 |
| Contract/version enforcement is decorative | F2, F8, F10 |
| The demo path & cross-section seams are untested | F3, F4, F8, F15 |
| Schema/migration/deploy untested | F5, F12 |
| Concurrency & live-cost hazards | F7, F11 |
| Frontend has no user-visible-surface tests | F9 |
| Fixture/seeding debt slows every new test | F13 |

Priority order if only three things get fixed: **F3** (demo-path E2E), **F5**
(migrations applied-for-real in nightly), **F11** (defuse the OpenRouter
landmine) — those three cover the highest-probability shipping incidents.
