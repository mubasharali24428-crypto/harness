# Changelog

All notable changes to the AI Engineering Harness are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
entries below are backfilled from `STATUS.md` + git history (Mission 12 G4
docs-truth lane, 2026-08-23) and extended through Missions 15–23 by the
Mission 23 / Z3 polish lane (2026-08-26). Migration numbers refer to files in
`section1/migrations/`.

## [Unreleased]

#### Verified live (2026-10-26)

`scripts/e2e/openrouter_smoke.py` drives the shipped engine against a real key
(one attempt, `max_tokens` pinned, `HARNESS_ALLOW_PAID_SMOKE=1` gate — it
refuses to spend otherwise). It completed a tool-using run with two live tool
calls and a real file side effect, and got `qwen/qwen3.7-flash` to read the
colour of an attached PNG back as `(220, 40, 40)`. Three facts it settled: the
profile registers one LLM route and OpenRouter needs
`deploy/patches/openrouter.patch.yml`, not a provider env var; a model declared
`input: [text]` makes the runtime silently drop the image and say so; and
providers refuse tiny images (`8x8 must be larger than 10`), so fixtures must
be real screenshots.

### W1 — a screenshot can travel with the goal (2026-10-26, contracts 1.0.0rc4)

`readImage` was already on and the runtime already admits image content blocks,
but nothing in this product ever attached one: "fix this UI, here's the
screenshot" was impossible.

#### Added

- **`goal_images.py`**: a run's verified image artifacts become SDK content
  blocks (`{"type":"image","data":<canonical base64>,"mimeType":…}`) at
  execution time. Selection is bounded twice — our own
  `HARNESS_GOAL_IMAGE_MAX_BYTES` / `_MAX_COUNT` / `_TOTAL_BYTES` and the
  runtime's own image limits — and every image that does not travel is
  reported with a reason. `HARNESS_VISION_MODELS`, when configured, refuses
  before the call rather than paying for a rejection the runtime would file
  anyway; `HARNESS_GOAL_IMAGES=0` turns the path off.
- **Upload→model path** in section 7's composer: the picked file is stored via
  the existing artifact route once approve mints the run id (no new endpoint,
  no invented contract), then the engine hands it to the model at start.
- **Contracts `1.0.0rc4`** (additive): `goal_images`, so "what did the agent
  actually see" is durable and replayable rather than process memory. The
  section 7 status panel renders attached/skipped/refused from that event.

#### Fixed

- A resumed run re-attached the same image into a continuing SDK session
  (duplicate attachments). The first attempt of a session now owns the image.
- **Review-found, test-confirmed:** the team filter was appended *after*
  `ORDER BY`, which made the SQL a syntax error on the first real run — the
  module-level tests could not see it because they never went through the
  service. Service-level tests now pin the keyword actually reaching the
  engine, the refusal-before-the-call, and the resume case.
- `teamId` is a process stamp, not a durable column, so a replica that never
  saw the bind would have selected nothing and said nothing. It now resolves
  through the same durable witness the budget gate uses, and reports
  `teamScoped: false` when only run scope is available.
- Artifact bytes were read synchronously on the event loop; they now go
  through a worker thread, and an expired-but-unpurged image is no longer sent
  after the artifacts API has already 410'd it.

### W1 — the transcript is live, and compaction is visible (2026-10-26)

The SDK emits turn events, but this wrapper dropped them: a run showed a
spinner, then a final answer, with no evidence of what the agent actually did.
Contracts had no tool-event vocabulary at all.

#### Added

- **Contracts `1.0.0rc3`** (additive): `tool_call`, `tool_result` and
  `context_compacted`, with golden payloads and a regenerated TS mirror. The
  call/result pair is correlated by `(runId, callId)`; results that arrive
  before their call are buffered by the UI rather than dropped.
- **A live transcript path**: `execute_goal(..., on_progress=)` forwards SDK
  turn events on the direct path; the isolated worker emits NDJSON `progress`
  lines so the parent process sees them; the service drains a thread-safe queue
  onto the event bus (scrubbed before persistence). `ToolActivity` renders the
  stream in section 7 and pairs each result with its call.
- **Context visibility**: the state machine records the context window at
  execution start; compaction events are summed into `GET /runs/{id}` status,
  and the status panel shows compacted tokens.

#### Fixed

- **Optional engine keywords are probed, not assumed.** `on_progress` is now
  passed only when `execute_goal` accepts it (and `**kwargs` counts), and no
  forwarder is started for engines that cannot emit. Narrow test/custom engines
  previously failed the whole run with a `TypeError`; 32 section-4 tests caught
  this before it shipped.

### W1 — the agent can use files, and output is no longer capped (2026-10-25)

The shipped `sdk-minimal` profile turns the filesystem tool off (`read`,
`write`, `edit` all false) and the wrapper additionally pinned
`max_tokens=4096`. An agent could only touch files through bash, and every
answer was truncated at 4k tokens regardless of the model.

#### Changed

- The per-run policy patch enables `tool-fs` (`read`, `write`, `edit`,
  `readImage`) and `tool-fs-search` (glob/grep), so file work no longer costs a
  shell round-trip. This is safe only because the runtime confines the tool to
  the same `workspaceRoot` the shell gate enforces, and because
  `_fs_write_capability` keeps `write`/`edit` **off** when a plan has
  `forbiddenPaths` and the confinement mode cannot freeze them (Landlock and
  `none` cannot; container read-only mounts and Seatbelt can). The reason is
  written into the patch so an operator can see why writes were disabled.
- The hardcoded `max_tokens=4096` is gone: unset lets the provider decide, and
  `HARNESS_SDK_MAX_TOKENS` pins a cap for cost control (invalid values fail
  fast).
- Seeding a workspace no longer strips `.git`, so checkpoints and diffs work
  against real history instead of an undescribed tree.

### W1 Day 0 — the sidecar that was never running (2026-10-25)

`sandbox-egress` was crash-looping and every check passed anyway.

#### Fixed

- **The entrypoint ignored `command:`.** `deploy/entrypoint.sh` never read
  `"$@"`, so the egress sidecar (whose only purpose is
  `python -m harness_sandbox.egress_proxy`) booted the all-in-one branch —
  seven uvicorns plus a migrate job inside a 128MB, 0.25-CPU container — and
  died on the master-key gate. It now execs a passed command before the
  `HARNESS_SERVICE` branch, so a sidecar needs no service identity at all.
- **`sandbox-egress` had no healthcheck.** It has one now, and it tests
  policy rather than liveness: a `CONNECT` to a non-allow-listed host must
  answer `403` (no outbound network needed), so a dead listener, a broken
  allow-list or a wedged loop all read as unhealthy.
- **Readiness no longer implies reach, and never depended on it.** The sandbox
  `/ready` body reports `egress: up|down` when the proxy is configured and
  still fails only on db + schema drift — a run that needs no network must
  execute while the proxy is down, but `ready` must not be read as
  "network available".

#### Added

- `scripts/e2e/compose_journey.py` check 0: every service this stack actually
  runs (profiles applied) must have the container it should have — running,
  healthy where a healthcheck exists, and `migrate` exited 0. It reads
  `ps -a`, because the first version of this check read `ps` and therefore did
  not see stopped containers at all: stopping `sandbox-egress` by hand still
  passed. Verified both ways against the live stack (stopped → reported,
  restarted → clean). This is the check that would have caught 708 consecutive
  failed healthchecks behind a demo replay.

#### Found while verifying

- The database had migration `0062_team_model_prefs` applied while the running
  image declared 60, so identity / github / review / meeting / webhook-delivery
  were crash-looping on the schema-drift gate — correctly: the image predated
  the migration. A rebuild from the current tree clears it. The full image
  build is currently blocked on a 60 MB `deepseek_harness_runtime_bin` wheel
  downloading at ~28 kB/s from inside the builder (~100 kB/s on the host), so
  the live verification used a source-overlay image over the previous build
  (`harness-backend:vrollback-1024` is the untouched original tag).

### Tool-output protocol — structured diagnostics instead of scraping (2026-10-25)

`docs/tool-diagnostics-protocol.md`. Hermes' `hermesc --json` contract adopted
verbatim (roadmap H1): `{path,line,col,kind,message}` on stderr, the program's
output on stdout, never mixed.

#### Changed

- `StructuredExecResult.diagnostics` is a list of records, not strings — with
  `diagnostics_source` (`protocol` / `located` / `scan` / `none`) recording how
  the parser got them, and `diagnostics_truncated` when the 20-record display
  cap drops the rest. A record with no position says so instead of carrying a
  guess; `kind` defaults to `error` so an unknown severity is never softened.
- Diagnostics are read from stderr for the two structured tiers. Only the
  keyword `scan` fallback reads stdout, because test runners print their
  summary there — and the API now labels that case.
- `parse_output` (an unused alias flagged as an orphan in
  `docs/briefs/m17-findings-pbeta.md`) removed.
- Python and TypeScript SDK clients regenerated — this also picks up drift that
  predated this change: the router clients were missing `runId`,
  `ModelPrefsBody` and `unattributed` spend, and the sandbox client was missing
  the checkpoint hunk/restore operations already served by the API.

#### Added

- `render_diagnostics_json()` — the emitter half, so a tool that speaks the
  contract round-trips through the parser (`emit → parse` is an identity).
- A seeded corpus of nine tool outputs across all three tiers, with a test that
  asserts the corpus exercises every tier — the "validate the validator" step
  applied to the parser before it becomes a review gate.
- Terminal panel renders `path:line:col`, the kind and the message per
  diagnostic, plus a plain note when positions came from scanning.

### Provider registry — 65 providers, three wire protocols (2026-10-25)

`docs/providers.md`. Until now the router had one outbound target —
`DEEPSEEK_BASE_URL` — no matter which model was named, so the six "providers"
in the catalog were six labels on one URL.

#### Added

- `section3/src/harness_router/provider_registry.py`: 65 providers / 117 named
  models across 9 regions (15 frontier labs, 16 open-weight hosts, 12 Chinese
  labs, 14 gateways and clouds, 8 local runtimes), each with its documented
  base URL, wire protocol, auth style, key env and docs link.
- Endpoint resolution with documented precedence: a deployment gateway
  (`HARNESS_PROVIDER_BASE_URL`) → the provider's own `*_BASE_URL` → the
  historical `DEEPSEEK_BASE_URL` → the registry default → the deployment
  fallback for an unregistered provider. Existing installs and suites are
  unaffected; a gateway override forces the OpenAI shape.
- Anthropic (`/v1/messages`, `x-api-key`, system lifted out, `max_tokens`
  defaulted) and Gemini (`:generateContent`, model in the path,
  `x-goog-api-key`, role mapping) adapters next to the OpenAI one.
- `GET /api/v1/models/catalog` now carries label, protocol, region,
  open-weights, `requiresOwnEndpoint` and a `priced` list; the keys page turns
  the provider `<select>` into a searchable datalist over the registry, and
  Settings → Models gained a provider filter.

#### Fixed

- **Billing no longer guesses.** `PricingEngine` returns $0.50/$1.50 per
  million for any model it does not know, which at this catalog size would
  invent spend for free local runs and under-bill frontier models. The router
  bills an unpriced model at zero and logs the gap; the UI says "cost not
  tracked" instead of implying a rate.
- The provider-key endpoint accepts any valid key: `KeyProvider` was a
  four-value union in the UI while the backend validates only the provider
  *shape*, so 61 providers were unreachable from the page that stores their
  keys.

#### Known limitations

- The sandbox egress allow-list predates the registry: a new provider is
  routable from S3 but not necessarily from inside a confined run. Deriving
  that list is a network-policy change and needs sign-off.
- Teams cannot point a provider at their own base URL (Azure resource, Bedrock
  region) — still operator env only, deliberately, since a team-supplied URL is
  an SSRF surface.
- Only 15 of the 117 model ids have a real rate card.

### UI/UX audit Batch A (2026-10-25)

First batch from `docs/ui-ux-audit-2026-10-25.md` — the eight items that were
broken or misleading rather than missing.

#### Fixed

- **A1** — the run surface stopped quoting internal engineering notes to users:
  the diff panel now names the files the agent has actually touched (derived
  from the `diff_progress` events the sandbox already emits), the plan panel
  dropped its placeholder "Actual cost: — (no live source yet)" line, and the
  "Comments & nudges" panel became **Run notes**, labelled for what it is (a
  browser-local scratchpad) with a pointer to the real direction control.
- **A2** — `TerminalPanel` no longer claims "no live terminal feed" while
  transcript lines render directly underneath it.
- **A3** — light-theme contrast: pills, phase badges, run-state badges,
  settings links and breadcrumb separators moved onto theme tokens, and
  `pillStyle()` mixes its fill with `color-mix()` so token colours are legal at
  all (the old `${color}22` concatenation is what forced every pill to hardcode
  a dark-theme hex). A headless pass over 12 pages × 2 themes went from 15 AA
  failures to zero; `src/lib/themeContrast.test.ts` guards it.
- **A4** — the demo-replay banner (a nowrap `.pill` carrying a whole sentence)
  forced the run page to 489px at a 390px viewport; it wraps now, measured 0px
  overflow at 320/390/768.
- **A6** — ticking "Edit a file myself" used to POST a human edit against a
  placeholder path immediately, pausing the run and recording an empty edit;
  the checkbox now only reveals the editor and "Save edit" does the write.
- **A7** — the sidebar TEAM line is a read-only value, not a `<select>` with
  one hardcoded option and no handler.
- **A8** — a rejected model-preference save rolls the control back instead of
  displaying a model the server refused.
- **A9** — the review page distinguishes a missing PR (404 → empty state) from
  a failed load (message + retry) instead of hanging on "loading diff…", and
  "Request changes" requires a written note and sends it as the review comment
  rather than POSTing an empty array and reporting a recorded decision.

### Team model preferences and reasoning effort (2026-10-25)

#### Added

- **Per-team model choice, persisted** — migration `0062_team_model_prefs.sql`
  plus `GET/PUT /api/v1/models/prefs` (Reader may view, Writer+admin may save,
  unknown models rejected with `UNKNOWN_MODEL`, storage failure returns
  `503 PREFS_UNAVAILABLE` rather than silently accepting). Router `/call` and
  orchestrator runs both honour the team's primary model; deployment defaults
  still apply until a team saves a preference.
- **Composer model chip and Settings → Models** — `ModelPrefsForm` (compact in
  the plan-gate composer, full on `/settings/models`) lists the provider
  catalog, auto-saves for admins, and states plainly what the choice affects.
- **Honest reasoning limits** — reasoning effort is stored for every
  deployment but only wired into the request for OpenRouter-style endpoints;
  the server reports `reasoningWireSupported` and the UI says so instead of
  implying the setting always does something.

#### Known limitations

- Reasoning effort reaches only OpenRouter-compatible endpoints; other
  providers accept no reasoning object, so the setting is stored and ignored
  there (stated in the UI, not hidden).
- The reviewer model preference is persisted but not yet consumed by the
  reviewer lane.

### Mission 23 · Program finale — polish sweep (2026-08-26)

Final mission of the 10x program (graph:
`docs/briefs/mission23-finale-graph.md`): four lanes — program AAR +
scoreboard (Z1), bet execution (Z2), repo-wide polish sweep (Z3), release
readiness for the v1.0.0-candidate tag (Z4).

#### Added

- **Z3** — `docs/backlog.md`: consolidated future-work ledger (counsel-review
  placeholders from the M22 legal skeletons, frontend API-client
  consolidation) plus the kept-after-inspection inventory from the dead-code
  scan, so nothing flagged was silently dropped.

#### Removed

- **Z3** — provably-dead symbols (zero references repo-wide, tests included):
  `_error_text` (mcp tools), `_num` (S3 budget guard), `claim_owner` +
  its probe SQL (S4 run queue — superseded by the durable spawner election),
  `get_plan_gate_service` (S4), `get_sandbox_service` (S5), `focusRing`
  (S7 tokens), and the orphaned module `section7/src/lib/onboarding.ts`
  (superseded by `app/onboarding/onboardingApi.ts` since M10-B3).

#### Fixed

- **Z3** — broken internal links in `docs/runbooks/alerting.md`
  (`queue-depth.md` → `docs/engine-concurrency.md`; `eventbus.md` →
  `docs/runbooks/ops.md`). Link-checker now reports zero broken internal
  links across all 95 root+docs markdown files.

### Mission 22 · Commercial skeleton (2026-08-26)

License, pricing tiers, billing, SSO, and an MCP server — the platform can be
commercially operated without inventing product behavior (per-tier limits are
enforced in code, not just advertised).

#### Added

- **M1** — Legal foundation: MIT `LICENSE` (decision flagged for counsel),
  `TERMS.md`/`PRIVACY.md` skeletons with explicit `[PLACEHOLDER: counsel
  review]` markers (tracked in `docs/backlog.md`), `PRICING.md` mapped to
  real code caps.
- **M2** — Tier enforcement: `PLAN_TIERS` became behavior-bearing — Free =
  capped runs/day + index size, Pro = full, Team = extra seats + audit
  retention; enforcement chains BEFORE the budget reserve at run-start and on
  the context-index API; cap hits return `{tier, cap, upgradeTo}` for
  upgrade CTAs.
- **M3** — Stripe adapter behind `HARNESS_BILLING=stripe`: checkout session
  creation, webhook route with HMAC-sha256 signature verification (>5min
  timestamp drift rejected) and idempotency by event id, subscription status
  → tier sync. Test-mode only in CI (mocked Stripe, no keys).
- **M4** — OIDC SSO: discovery-based flow with PKCE and JWKS validation,
  per-team IdP configuration, email-match provisioning on first login
  (documented rule: no verified-email match ⇒ deny + audit), OTP fallback
  when unconfigured. Raw OIDC over httpx — no vendor SDKs.
- **M5** — MCP server (`mcp/`): stdio transport exposing `start_run`,
  `get_run_status`, `search_context`, `list_projects` under a service-token
  principal, with tool schemas written for LLM consumers.

### Mission 21 · Context pillar (2026-08-26)

The engine's plans can cite your actual codebase: index once, resolve @refs
at plan time, generate steps with path:line citations backed by indexed
chunks.

#### Added

- **X1** — Codebase indexing core: pgvector-backed store, pluggable
  embeddings, incremental re-index, frozen search contract (W21-C1).
  Migration **0030** is pgvector-gated and degrades honestly without the
  extension.
- **X2** — `@refs` resolution: hybrid keyword+vector ranking,
  token-budgeted context bundles; grammar pinned by parity tests in S7.
- **X4** — Retrieval-augmented planner: generated steps carry path:line
  citations resolved from the index (exit criterion met and demonstrated).
- **X3** — UI surface: `@` autocomplete refs, chunk preview panel,
  searchable file tree in section7.

#### Fixed

- Replay-cap flake eliminated (30/30 stability sweep clean); formal close at
  CI SUCCESS.

### Mission 20 · Horizon NEXT part 2 — the first 10x lever (2026-08-26)

Claim batching tore down the BASELINE wall: **1.69 → 37.58 journeys/s at 32
VU (~22×)** with zero failures across 4,521 journeys.

#### Added

- **T1** — Claim batching in the orchestrator worker (the 10x lever above);
  theoretical ceiling now ~3.2M runs/day, with executor width and provider
  latency as the honest next limits.
- **T2** — Observability minimum: request-id middleware on all 9 services,
  anonymized user hashing in logs, Prometheus scrape endpoint + 5
  promtool-validated alerts (`docs/runbooks/alerting.md`).
- **T3** — Resource limits on all 22 compose services, log rotation,
  `/ready` separated from `/health`.
- **T4** — `docs/extensions.md` (three real integration seams), glossary
  day-one nouns completed, README/AGENTS truth pass.
- **T5** — TypeScript SDK v0 generated from the same pipeline as the M19
  Python SDK — both SDKs stay in lockstep with the shipped OpenAPI specs.

### Mission 19 · BASELINE + Horizon NEXT phase 1 (2026-08-25)

First honest performance baseline plus the stateless-orchestrator groundwork
that made the M20 lever possible.

#### Added

- **B1** — BASELINE published: ~1.9 journeys/s ceiling, wall identified as
  the worker's single-claim poll cycle, $0.000051/run metered cost, 8→64 VU
  ladder methodology in `docs/benchmarks/`.
- **N1** — Stateless orchestrator phase 1: CAS-based approvals, durable-
  first execution, two-replica exactly-once spawner election.
- **N2** — Slim cross-instance bus signals (migration **0029**: ~70-byte
  wake-up payloads, 8KB publish cap).
- **N3** — Partitioned retention: monthly `agent_events` partitions
  (migration **0028**) + outbox/audit prune runners.
- **N4** — Canned demo: zero-key 8-minute value demo driven by recorded-truth
  replay (no fabricated model output).
- **N5** — Python client SDK v0 generated from the seven OpenAPI specs
  (91 paths / 104 operations).

### Mission 18 · Horizon NOW (2026-08-25)

Six correctness/capacity fixes from the 10x plan's Now horizon, all
fail-closed by construction.

#### Added

- **C1** — Authorization fail-closed everywhere (the triple fail-open path is
  dead; denials are audited).
- **C2** — One budget truth: `contracts/budget.py#resolve_cap` is canonical,
  fails CLOSED with an explicit degraded flag.
- **C3** — Cost gate O(1): covering indexes (migration **0027**) + daily
  spend-rollup runner.
- **C4** — Load-test harness (carried forward as the M19/M20 BASELINE tool).
- **C6** — Rollback story v0 rehearsed end-to-end: the image-drift gate
  refused an old image against the 0027-schema database, as designed.

#### Fixed

- **C5** — Invite/ticket hygiene: email binding on invites, `?token=` login
  deprecated, WS-fingerprint enforcement aligned with what browsers actually
  send, expired-bucket GC.

### Mission 17 · ELITE audit → the 10x plan (2026-08-25)

Seven-lens adversarial audit, its fix wave, and the plan that governed
everything after it.

#### Added

- **Phase A** — ELITE dual-team audit banked: 1,901 lines of findings across
  seven lenses (`docs/briefs/m17-findings-*.md`).
- **B6** — Contributor bootstrap: working quick-start, first tutorial
  (`docs/TUTORIAL.md`).
- **Phase C** — `docs/briefs/mission17-10x-plan.md`: four axes, 23 missions
  across Now/Next/Later/Bet horizons (executed as M18–M23).

#### Changed

- **B1/B3** — Budget-call serialization + contracts promotion + isolation
  enforcement (section-boundary import ban wired into CI guards).
- **B2** — Live PITR wiring exercised + CI migration coverage.
- **B4/B5** — Plan-gate team scoping fixed; rate limiter keys IPv6-safe (/64);
  engine runtime hardened with the hung-provider circuit breaker
  (`docs/engine-concurrency.md`).

### Mission 16 · Wave 6 — perf/scale + corpus (2026-08-24)

Independent ALEX-FORCE re-verification before close (S1 430 · S4 318 ·
S7 vitest 452, migration chain 0001→0026 unique).

#### Added

- **X1** — Runs-index tuning at 100k events: covering index (migration
  **0026**) + N+1 elimination via one batched extremes query per page.
- **X2** — Meeting message pagination: keyset cursor, bounded windows.
- **X3** — WS replay cap + seq-window resume proof, truncation markers so
  clients know when history was clipped.
- **X4** — UI systemization completed: every page on the design-system
  primitives.
- **X5** — Edge-case corpus wave A: 19 seed-ID-traceable lifecycle
  regression tests.

### Mission 15 · Wave 5 — hardening sweep (2026-08-24)

Security posture, data-integrity, and docs-truth wave closed by verifier
teams.

#### Added

- **S1** — CSP enforce-mode rollout (report-only first; policy built from
  audited real sources) + `/api/v1/csp-report`.
- **S2** — WS ticket client binding: fingerprint bound at mint, validated at
  upgrade (migration **0025**, renumbered from 0024 after a slot collision).
- **D1** — FK deletion story: explicit ON DELETE behavior everywhere +
  cascade suite (migration **0023**); `billing_plans.team_id` CASCADE.
- **D2** — Vacuum/maintenance schedule + runbook.
- **G6a** — Architectural-law CI guards: route modules must be
  default-export-only; no cross-section imports (`make guard-routes`,
  `make guard-isolation`).
- **G6b** — Reviewer-heuristic team tuning + calibration corpus.
- **G7** — Docs truth batch: README accuracy pass, `docs/api-versioning.md`,
  `GLOSSARY.md`, env-var sync.
- **S4** — Salted KDF upgrade for stored secrets with dual-read
  re-encryption (+ `reencrypt` management command).

#### Fixed

- **S3** — Accessible names on all placeholder-only inputs; login layout
  fixed at 320px viewport.

### Mission 14 · Wave 4 (2026-08-24)

Product features: metering, a11y/responsive frontend fleet, invites,
aggregate run API + detail page, optimistic UI, design system, Docker DNS
fix. Baseline `9141e39`; closed at `baf2299` with all five verifier teams
READY and CI run 32701744809 SUCCESS. Suites at close: S1 315 · S3 91 ·
S4 291 · S6 76 · S7 vitest 415/415 + tsc clean.

#### Added

- **F1a** — Cost metering live end-to-end (W4-C1): engine completion → S3
  `record_usage` via the internal API; `GET /api/v1/models/spend?sinceDays=N`
  returns real NUMERIC-exact numbers `{total, byDay[], unwired:false}`; the
  cost page drops the "unwired" explainer and renders actual run costs.
- **F1b** — Keyboard support + focus management across section7: full
  tab-order audit page-by-page, Enter/Space on interactive cells, visible
  focus rings, skip-link (`lib/focus.ts`).
- **F2a** — Invite lifecycle admin UI: mint/resend/revoke with status badges
  (draft/sent/redeemed/expired/revoked), audited actions; server half landed
  as resend/revoke routes + status derivation (`4c09072`).
- **F2b** — Responsive layout: sidebar collapses <900px, cards stack, tables
  scroll-x; zero fixed-px layout breaks verified at 375/768/1280.
- **F3a/F3b** — Aggregate run read endpoint (W4-C2):
  `GET /api/v1/orchestrator/runs/{id}` enriched
  `{runId,state,costUsd?,prId?,url?,steps?}` with explicit nullability plus
  runs-index cursor pagination; Runs detail page consumes it with honest
  degradation per missing field (state machine view, cost line, PR link,
  step timeline from events) (`84608eb`).
- **F4** — Optimistic UI for plan approve/reject and roster remove/
  role-change: instant update, reconcile on response, rollback with toast on
  error (`hooks/useOptimistic.ts`).
- **DS** — Design-system lane: shared primitives (Pill, Button, Card, Table,
  Modal) extracted to `section7/src/components/ui/**` with tokens; cost +
  team-settings pages migrated as proof; DS owns the directory exclusively.

#### Fixed

- **P2** — Docker DNS crash-loop on the legacy all-in-one backend
  root-caused and fixed (W2/W3 carryover): orphaned detached container →
  EAI_AGAIN resolution failure; compose now declares the `backend` service
  under the opt-in `legacy-backend` profile so the state cannot re-enter;
  ops runbook documents diagnosis (`26f8c49`, `docs/runbooks/ops.md`).

### Mission 13 · Wave 3 (2026-08-23)

Hardening & performance. Baseline Wave-2 close (CI 32685579474 SUCCESS);
10 lanes committed (`d22e18c`), closed with all five verifier teams READY,
CI run 32694440516 SUCCESS, resume-DTO MINOR fixed in-lane (`d539b87`).
Suites at close: S1 309 · S3 91 · S4 291 · S6 76 · S7 303 vitest.

#### Added

- **H2** — WAL-G PITR end-to-end (H-018): archive_command wired through an
  archiving `walg-db` sidecar, credentialed restore drill executed with
  timed RTO/RPO evidence; opt-in `walg` compose profile + `.env.example`
  knobs (`c3aead1`, `docs/runbooks/backup-and-pitr.md`).
- **H3a** — Playwright E2E hero-journey suite against the compose stack:
  login→project→meeting→brief→plan→approve→run→detail→review, plus OTP-flow
  spec (`eae5a41`, `section7/e2e/`); consumed via `make e2e`.
- **H3b** — CI/local gate convergence (H-024/H-025): Makefile is the single
  source of truth — ci.yml jobs call its targets verbatim so drift between
  "passed locally" and "what CI ran" is structurally impossible;
  version-pin conformance test revived as the 4-way contracts-version rot
  detector (`4cd7074`, `test_version_pin_conformance.py`, `6254fe5`).
- **H4a** — NUMERIC money migration (H-023): cost columns → NUMERIC(12,6)
  (migration **0020**), Decimal sweep across S1/S3 money paths, budget
  guard/pricing tests Decimalized. Operator note: caps budgets at
  $999,999.999999.
- **H4b** — TIMESTAMPTZ normalization: shared timeutil helpers in
  harness_identity, S6 conversion + tz-stamp tests; naive-datetime write
  sites zeroed (`e0c61ad` group, `9575287`).
- **G5a** — Oracle-consistent 403/404s across sections + OpenAPI response-
  model typing islands grown 8 → 39 routes (`d2922ff`, hardening suites in
  S1/S3/S4/S6).

#### Changed

- **H1a** — Pool math fix (H-017): shared engine factory, documented 84 ≤
  120 connection budget (7 services × (pool_size=8 + max_overflow=4), PG
  max_connections=120 with 36 slots headroom), CI-enforced
  (`6dfa3b2`, `docs/pool-math.md`).
- **G5b** — invokeAi dead-export prune + frontend DX batch (VT-2 MINOR +
  raw-14 top items) (`d116460`, `ba05ada`).

#### Fixed

- **H1b** — Run-queue correctness (H-017 final): heartbeat comparison now
  judged by the database's own clock; `run_claims` candidate selection uses
  SKIP LOCKED inside the subquery; zombie reaper releases timed-out claims
  and moves runs to BLOCKED with honest `heartbeat_lost` events; live-PG
  race proofs (`06b8055`, `4db60d5`).

### Mission 12 · Wave 2.1 (2026-08-23)

Baseline `ebdfeb7`; campaign graph `docs/briefs/mission12-wave2-graph.md`.

#### Added

- **E1** — `@ai` meeting invocations call real LLMs through Section 3
  (`call_model`) using team BYO keys; KeyVault decrypt happens server-side;
  the regex stub engine remains only as an honest fallback that says so when
  no key is configured.
- **E2** — Frontend 401 interceptor clears the session and routes to
  `/login?expired=1`; sign-out wipes every `harness.*` storage key; backend
  error codes map to human-readable messages instead of internal jargon.
- **E3a/E3b** — Roster management API (`DELETE`/`PATCH` members; admin-only,
  last-admin guard → 409 `LAST_ADMIN`, cross-team → 404 without an existence
  oracle; audited `member.removed`/`member.role_changed`) plus Runs rows
  click-through and roster UI consuming it.
- **E4a/E4b** — PR-open persists `pr_id` on the run (migration **0014**) and
  emits `pr_opened {runId, prId, url}`; minimal `GET /runs/{id}` aggregate;
  Review page consumes `?pr=` and subscribes to `pr_opened` with an honest
  empty state.
- **F1** — Global `RequestValidationError` handler returning the unified
  `{"error":{code,message}}` envelope (detail mirrored for back-compat);
  `Idempotency-Key` middleware on mutating POSTs (migration **0015**; pilots:
  keys, run start, meetings).
- **F2** — Optimistic concurrency for plan decisions: `agent_plans.version`
  column (migration **0013**, slot re-assigned after the M11 collision),
  conditional `UPDATE … WHERE version=:seen`, mismatch → 409
  `PLAN_ALREADY_DECIDED`.

#### Fixed

- Duplicate `invokeAi` client calls removed (single invocation per `@ai`).

## Mission 11 · Wave 1 (2026-08-23)

CLIENTELA remediation: six CRITICALs + flow-blocking HIGHs. Baseline
`d5d9619`, seven lanes, closed at `b255ce2` with all verifier verdicts READY
and CI green.

### Security

- **C-001/H-004** — Ownership proof enforced by default everywhere
  (config, compose, deploy services): unverified email can no longer mint an
  admin team; plain login joined the DB-backed protected set; legacy
  direct-add invite endpoint removed (`f04650c`, `61cb1a9`); verify-OTP
  throttled, OTP codes single-use.
- **C-002** — Audit emission wired at every sensitive call site (API keys,
  invites, billing): never-throw, secret-scrubbing typed helpers
  (`audit_emit.py`) (`7a6b2b3`).
- **C-003** — Atomic budget enforcement: fail-closed reserve/release guard +
  `cost_ledger` writer, proven under an 11-test concurrency suite
  (`7cc21f8`).
- **C-005** — Plan-gate orchestrator API per the frozen pair contract —
  propose/approve/reject with role gates (admin/engineer decide;
  viewer/guest → 403), idempotent approve, audited reject (`281d769`) — and
  the PlanGate UI consuming contract DTOs field-by-field (`c2b487c`).

### Changed

- **C-004/H-005/H-006** — Engine boot integrity: compose engine env block
  stripped of placeholder/dev defaults; unset `ENGINE_LIVE_MODE` is now a
  loud boot failure (explicit `live|dry` enum); silent provider/model
  downgrade ladder removed (`db9e333`, `65525b2`).
- **H-007/H-008** — Done-signal & terminal-state hygiene: role-gated
  done-signal requires a non-empty diff and refuses resurrection;
  `reviewer_passed → DONE` is terminal (`608f0ce`).

### Fixed

- Rate limiter was wired to a route that never existed (`/auth/otp/request`);
  moved onto the real login/OTP routes (`dc9b75f`).
- CI lint: `LiveMode` → `StrEnum` (ruff UP042) and import sort (I001)
  (`70532c0`).

## Mission 10 (2026-08-23)

ALEX_TEAM Phase 2 completion + Phase 3 edge-hardening begins. Baseline
`abcab51`; mission closed with all CI jobs green (`602c505`).

### Added

- **BYO keys** — `/api/v1/keys` CRUD over the frozen `api_keys` table:
  KeyVault-encrypted at rest, hint-only exposure, idempotent soft revoke,
  foreign-team requests 404 with no existence oracle; Settings → Keys page.
- **Cost dashboard** — `/dashboard/cost` over live `/spend`: range selector,
  totals cards, by-model bars, SVG sparkline, honest empty state.
- **Run history** — team-scoped `GET /api/v1/orchestrator/runs` with
  server-driven paging (limit ≤ 100) + filters; Runs page with status pills
  and relative times.
- **Billing** — `/api/v1/billing/state|plan` behind `HARNESS_BILLING`;
  local quota from `cost_ledger` vs `teams.budget_usd` (migration **0010**);
  Stripe adapter is an injectable protocol (no package); Billing settings
  page with meter.
- **Onboarding revival** — checklist API derives completion from real state
  only; interactive onboarding page with progress.
- **WAL-G PITR tooling** — `deploy/walg` scripts + opt-in compose profile +
  Dockerfile; shipped as reviewed-but-unexercised scaffolding (no S3 creds
  in build env — honesty note in `docs/runbooks/backup-and-pitr.md`).
- **Rate limiting** — fixed-window Postgres limiter on login/OTP routes
  (migration **0011**): atomic upsert, 429 + `Retry-After` envelope; DB
  failure fails OPEN.

### Fixed

- CI red on a missing checklist golden fixture (`582c044`); gitleaks
  allowlist for the fake test key.
- Compose `CORS` env shape: origins parser now accepts both the JSON-array
  form and comma-separated lists (`96c1ffb`).

## Wave 9 (2026-08-23)

Phase 2 SaaS core: metering, budgets, orgs, notifications, security headers.
Baseline `6d5e52c`; three first attempts died to provider outages with zero
tree writes and were re-dispatched; every node independently re-verified.

### Added

- **Metering** — `cost_ledger` gains real writers: usage recorded on every
  successful `call_model`; `GET /api/v1/models/spend` aggregates
  totals/byModel/byDay; models service boots an asyncpg ledger pool and
  drains pending writes on shutdown.
- **Budgets** — spend cap checked pre-spawn and on reviewer model calls;
  precedence `teams.budget_usd` (migration **0007**) >
  `HARNESS_TEAM_BUDGET_USD` > unlimited; over-cap produces an honest blocked
  event.
- **Orgs/invites** — `team_invites` (migration **0008**) with hashed tokens,
  admin-only mint/revoke, atomic redeem (404/409/410), members roster; team
  settings page with roles, invite form, revoke, join-by-token.
- **Notifications** — `notification_outbox` (migration **0009**) with
  templates for `run_finished`/`review_failed`/`pr_opened`; worker drains
  with max 3 attempts; dev mode is offline-green; EventBus listener tap is
  disclosed and exception-guarded so notifications can never break event
  publishing.
- **Security headers** — pure-ASGI middleware on S1/S4/S5/S6/S8
  (nosniff/DENY/referrer everywhere; HSTS only behind `HARNESS_TLS_ENABLED=1`;
  report-only CSP on HTML); CI gained a security-scan job (pip-audit WARN +
  gitleaks).

### Fixed

- Merge wiring caught live: double rate-limiter install turned 6 tests red —
  limiter moved to assembly level, suite green.
