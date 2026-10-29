# Backlog — consolidated future work

> Created by Mission 23 / lane Z3 (final polish sweep, 2026-08-26) from the
> repo-wide TODO/FIXME triage and the dead-code/duplication scan. Items here
> are real, deliberate future work — nothing was silently dropped from the
> tree. Owner tags are suggestions, not assignments.

## From the TODO/FIXME/XXX triage

The code tree carries **zero** TODO/FIXME/XXX/HACK markers outside two
intentional ones (see "Intentional markers" below). The only unresolved
future-work items found live as explicit placeholders in the legal documents
shipped by M22-M1:

### Legal: counsel-review placeholders (M22-M1)

`TERMS.md` and `PRIVACY.md` ship with `[PLACEHOLDER: counsel review]` markers
by design — the docs themselves forbid deleting a placeholder without
resolving it. Each must be resolved by qualified counsel before any commercial
operation; they are NOT code work and cannot be closed by an agent.

- [ ] TERMS.md — contracting entity legal name + jurisdiction
- [ ] TERMS.md — operator liability model (self-hosted vs vendor-hosted)
- [ ] TERMS.md — vendor-hosted variant terms (if/when that exists)
- [ ] TERMS.md — industry-specific prohibitions
- [ ] TERMS.md — pricing terms: price changes, refunds, proration, tax
- [ ] TERMS.md — liability cap structure (fees-paid vs unlimited)
- [ ] TERMS.md — suspension/termination rights and cure periods
- [ ] TERMS.md — amendment mechanism (notice period, versioning)
- [ ] PRIVACY.md — data-controller identity and contact
- [ ] PRIVACY.md — retention schedule per data class
- [ ] PRIVACY.md — sub-processor list and transfer mechanism
- [ ] PRIVACY.md — regional disclosures (GDPR/CCPA or equivalents)

### Engineering carry-overs (pre-existing roadmap, unchanged by Z3)

These predate Mission 23 and remain the standing roadmap order from the
2026-08-22 analysis session:

- [ ] Consolidate S7/S9-style frontend duplication: S7 now has TWO parallel
      conventions for API clients (`app/**/*Api.ts` per-page modules vs the
      `src/lib/api.ts` core). The duplicated bearer/error-envelope block is
      ~12 copies today (see duplication inventory below). Consolidation is a
      refactor across live UI modules — deliberately NOT done in the v1.0
      polish window.
- [ ] CI pipeline hardening beyond static gates (nightly security probes,
      dependency-audit budget), per GAP doc Part 6.

## Intentional markers (left in place on purpose)

- `section10/demo_repo/src/textstats/stats.py` — `TODO(demo-task)` is the
  shipped demo task itself; the platform's canned demo exists to complete it
  (`section4/src/harness_orchestrator/demo_replay.py` pins the canonical diff;
  `section10/demo_repo/tests/test_stats.py` pins the failing test). Removing
  it would break the demo.
- `section4/src/harness_orchestrator/live_mode.py` — `"todo"`/`"fixme"`
  appear inside the engine's diff-scanning vocabulary (suspicious-diff
  detection), not as work markers.

## Dead-code scan: removals made

Seven provably-dead symbols removed in this sweep (zero references repo-wide,
including tests, verified by scripted reference count):

| Symbol | File | Note |
|---|---|---|
| `_error_text` | `mcp/src/harness_mcp/tools.py` | superseded error helper |
| `_num` | `section3/src/harness_router/budget_guard.py` | unused coercion helper |
| `claim_owner`, `_CLAIM_RUNNING_PROBE_SQL` | `section4/src/harness_orchestrator/run_queue.py` | M19-N1 probe never wired; spawner election uses `queue_available` instead |
| `get_plan_gate_service` | `section4/src/harness_orchestrator/plan_gate.py` | unwired accessor twin of `_require_service` |
| `get_sandbox_service` | `section5/src/harness_sandbox/api.py` | unwired accessor |
| `focusRing` | `section7/src/components/tokens.ts` | DS primitive never adopted (focus rings shipped inline) |
| whole module | `section7/src/lib/onboarding.ts` | superseded by `app/onboarding/onboardingApi.ts` (M10-B3 moved it next to the page); zero importers |

## Kept after inspection (inventory, not deletion)

Flagged by the scanner but verified alive or load-bearing:

- **Pydantic route/response models** across every section's `api.py` — used
  via FastAPI decorator wiring; not name-referenced anywhere.
- **ORM models** `ModelConfig`, `ReviewComment`
  (`section1/src/harness_identity/models.py`) — mirror live tables
  (`model_configs`, `review_comments`; rebuilt by migration `0023`). ORM
  metadata is load-bearing even where no Python call site queries them.
- **Decorated route handlers** with zero plain-text references (SSO config,
  billing webhook, telemetry, onboarding checklist, metrics endpoints…) —
  registered by FastAPI at import time.
- `chunk_python_text` (`section11/src/harness_context/chunking.py`) —
  candidate upgrade surface for the AST-aware chunking bet; file owned by
  lane Z2 this mission, so untouched here.
- `SKIP_HREF` (`section7/src/lib/focus.ts`) — pinned by
  `tests/unit/focus.test.ts`.
- **Test fixtures/helpers** under `mcp/tests/conftest.py` — pytest-collected.

## Duplication inventory (accepted convention, not touched)

One dominant cluster: the fetch wrapper block (bearer header + JSON parse +
unified `{code,message}` error envelope) repeated across the per-module API
clients — `costApi.ts`, `planGateApi.ts`, `billingApi.ts`,
`projectRulesApi.ts`, `teamApi.ts`, `keysApi.ts`, `treeApi.ts`, `runsApi.ts`,
`onboardingApi.ts`, plus `src/lib/api.ts`, `src/lib/contextApi.ts`,
`src/lib/demoApi.ts` (~12 copies, ≥10 identical lines each). This is S7's
deliberate "each page module self-contained" convention; consolidation into a
shared client helper is logged above as backlog engineering work, out of
scope for a freeze-window polish sweep.

No cross-section copy-paste blocks were found between backend sections,
contracts, or mcp.

## From the 2026-10-25 provider-registry work

The registry (`docs/providers.md`) made 65 providers routable from the model
router. Three follow-ups were left open deliberately, not by omission:

- [ ] **Egress allow-list derived from the registry** (owner: S5). The sandbox
  proxy's list predates the registry, so a newly routable provider works from
  S3 but not necessarily from inside a confined run. Needs a network-policy
  sign-off, and `harness-sandbox-egress` is currently crash-looping on the dev
  stack — investigate both together.
- [ ] **Rate card beyond the 15 priced models** (owner: S3). 117 model ids are
  listed; an unpriced call bills zero and logs the gap. Per-provider price
  ingestion (or a documented "bring your own rate card" table) closes this.
- [ ] **Per-team endpoint override** (owner: S1 + S3, security review). Teams
  still cannot point a provider at their own base URL (Azure resource, Bedrock
  region); it stays operator env because a team-supplied URL is an SSRF
  surface. Needs an allow-list or an out-of-band approval step first.

## From the 2026-10-25 H1 tool-output protocol

The contract itself is shipped (`docs/tool-diagnostics-protocol.md`). Three
pieces of it are deliberately later, and none should be improvised mid-week:

- [ ] **Promote `Diagnostic` into `harness_contracts`** (owner: S4 + S5, with
  the W2 tool-event bump). It stays section-local until a second section
  consumes it — the promotion would otherwise force the atomic four-way
  contract version bump outside the two bumps the roadmap schedules.
- [ ] **Differential gate as review evidence** (owner: S4, W2). Baseline vs
  patched output over the run's corpus, plus a scheduled planted-defect drill
  so the gate's detecting power is measured rather than assumed. Today the
  pattern is applied to the diagnostic parser only.
- [ ] **Our own sandbox tools emit the contract** (owner: S5, W1). fs, glob/grep
  and web wrappers should print `{path,line,col,kind,message}` on stderr so
  `diagnostics_source` is `protocol` by construction for first-party tools.

While regenerating the SDKs above, the committed `clients/ts/dist` turned out to
be stale for several sections (identity/agents operations and the SDK version
string predate it). Only the two `dist` files this change touches were rebuilt —
`dist` for the other sections is still behind `src` and needs one dedicated
rebuild commit so it does not ride along inside an unrelated PR.

## From W1 Day 0 (2026-10-25)

- [ ] **Backend image build cannot be trusted to finish** (owner: deploy). The
  `pip install` layer comes *after* `COPY section*/`, so any source change
  invalidates it and re-downloads the 60MB `deepseek_harness_runtime_bin`
  wheel — measured at ~28 kB/s in the builder and ~100 kB/s on the host, with
  connection resets. Move dependency install ahead of the source copy (a
  `requirements`/pyproject-only layer) and support an offline wheel dir, or CI
  will keep timing out on a build that has nothing to do with the change.
- [ ] **Wheel cache for air-gapped/CI builds** (owner: deploy). Same root
  cause; a `PIP_FIND_LINKS` build arg plus a mounted wheel dir makes repeat
  builds minutes instead of half an hour.

## From W1 Day 5 (goal images, 2026-10-26)

- [ ] **The composer takes one image; the backend accepts three** (owner: S7).
  `HARNESS_GOAL_IMAGE_MAX_COUNT` defaults to 3 and `goal_images.py` honours it
  oldest-first, but the picker holds a single `File` and the client cap is
  4 MiB while the server cap is env-tunable. Either widen the picker or serve
  the limits from the API so the client stops mirroring a default.
- [ ] **Upload window between approve and start** (owner: S4+S7). The artifact
  route needs a run id, and the plan-gate response only returns `planId`, so a
  screenshot can only be stored after approval. A run started from another
  client in that window goes text-only (visible as `requested: 0`, not hidden).
  Returning `runId` from `POST /plans` removes the window.
- [ ] **A resume on a different replica re-attaches the image** (owner: S4).
  The "already sent" marker is per-process because the frozen schema records no
  per-session attachment fact. Either add that fact to `agent_events` or accept
  the duplicate and say so in the run page.
- [ ] **Worker stderr tail lands in `finalResponse`** (owner: S4). Bounded to
  500 chars and scrubbed, but a runtime that echoed the prompt in a debug mode
  would put truncated base64 into the durable result. Confirm the runtime never
  logs prompt bodies before widening image limits.
- [x] **Live evidence that a vision model received one** (2026-10-26,
  `scripts/e2e/openrouter_smoke.py`, `m07-openrouter-smoke.json`). The image
  reached `qwen/qwen3.7-flash` and the colour was read back correctly.

## From W1 M07 (isolated worker, 2026-10-26)

- [x] **The isolated worker has never streamed on a real host** (owner: S4+S10).
  Closed 2026-10-26: `HARNESS_ENGINE_CONFINEMENT=container` with a worker image
  built from this tree (`harness-engine-worker:m04-1026`) produced two
  call/result pairs arriving at 34.85 s and 48.89 s against a 68.42 s completion,
  using the native `read` and `write` tools, with a real file side effect. No
  spend (local Ollama), and `off` was never set. Evidence
  `docs/briefs/evidence/pilot-prep/m04-container-streaming.json`.

## "Section 1 is not green" was wrong — root-caused to this host's NO_PROXY (2026-10-26)

The entry below this one claimed 11 pre-existing Section 1 failures that blocked
any PR. **It was misdiagnosed, and Section 1 is not red.** Corrected rather than
quietly amended, because a phantom blocker was gating the W1 branch.

- [x] **SSO outbound clients inherit environment proxies** (owner: S1 + human
  decision). **Closed 2026-10-26 with option (b)**: `harness_identity.http_client.env_client`
  keeps proxy support and, only when construction fails on a bracketed entry,
  rebuilds with the brackets stripped (`[::1]` and `::1` name the same address).
  Any other `InvalidURL` still raises. Option (c) was not available — 0.28.1 is the
  newest httpx — and `test_plain_httpx_still_rejects_the_shape` fails the day it
  is, so the helper can be deleted then. Used by both SSO clients and the Stripe
  adapter (same exposure). `test_sso_oidc.py` 24/24 with and without the Docker
  Desktop `NO_PROXY`; Section 1 992 passed. **The remaining sections were closed
  in W15**: S2 GitHub (client + OAuth exchange), S4 context resolver and S6
  meeting now use the same `harness_contracts.http_client.env_client`, and
  `section1/tests/test_outbound_client_wiring.py` fails on any direct
  `httpx.AsyncClient`/`httpx.Client` construction under those src trees.
  S10 onboarding and `mcp` already used the contract factory. `tests/test_sso_oidc.py` fails 11 / passes 13 **only when this
  host's `NO_PROXY` is present**, and passes `24/24` under
  `env -u NO_PROXY -u no_proxy`. The raise is not in the app's request and not
  in URL building — it is in **httpx client construction**:
  `_client.py` builds `self._mounts` from `_get_proxy_map(None, trust_env and
  transport is None)` → `get_environment_proxies()`, which turns every
  `NO_PROXY` entry into a mount key. Docker Desktop's value here is
  `localhost,localhost.,.localhost,127.0.0.1,127.0.0.0/8,::1,[::1]`, and the
  IPv6 entries become `all://*[::1]`; `URL("all://*[::1]")` reads the port as
  `:1]` and raises `InvalidURL: Invalid port: ':1]'`. `all://127.0.0.0/8` (a
  CIDR) parses only by luck.

  Why it still matters, and is smaller than the original claim: `sso.py:566` and
  `sso.py:681` construct `httpx.AsyncClient(timeout=…)` with `trust_env` at its
  default, so **a deployment host carrying that `NO_PROXY` shape fails SSO login
  with a 500 before any IdP call**. Not the tests' problem, and not CI's —
  GitHub's ubuntu runners don't set it — but a real fragility. `webhooks.py:547`
  already passes `trust_env=False`, so the repo has a precedent for opting out.

  The fix is a decision, not a line: honouring env proxies is what a
  self-hostable product behind a corporate proxy needs, and it also means IdP
  token traffic can be routed through a proxy nobody reviewed. Choose one —
  (a) `trust_env=False` on SSO's clients, consistent with webhooks; (b) keep
  proxy support and normalise/reject unusable `NO_PROXY` entries ourselves; or
  (c) pin an httpx that fixes the `*[::1]` expansion and report it upstream. Not
  chosen here because it changes enterprise-visible behaviour and AGENTS.md puts
  that with the human, not under a W1 deadline.

### The superseded claim, kept for the record

> **11 failures in `section1/tests/test_sso_oidc.py` — blocks any PR** (owner:
> S1). … the error is raised **inside the ASGI app's own outbound request**, not
> in the test … So something in the SSO login path builds a URL whose host/port
> split is wrong …

Wrong on the location (client construction, not the app's request), wrong on the
cause (no malformed URL is built anywhere in `sso.py`), and therefore wrong on
the consequence: it does not gate the PR. What was right in it — not the live
stack, not the mock IdP, not W1's, `72cf901` last touched that file — is what
made the real cause easy to find once the environment was inspected.


## From W1 M07 (confined vision attempt failed, 2026-10-26)

- [ ] **A stuck run has no budget but the wall clock** (owner: S4, W2). The
  confined vision attempt made 37 `bash` calls over 516.75 s and ended in an empty
  model response at the deadline. W2's "loop fixture stops within explicit budget"
  is now a prerequisite for trusting any live run, not a feature.
- [ ] **The metering gap made a runaway unpriceable** (owner: S4+S3). `usage` is
  `null` on the openai-completions transport, so after 37 paid calls the cost
  cannot be stated from evidence. This turns "actual usage distinguished from
  estimates" (M05) from a nicety into the thing that lets you stop spending.
- [ ] **Tool choice is model-dependent, not policy-dependent** (owner: S4+S14).
  Same worker image, same `tool-fs` policy: local `qwen-local` used native
  `read`/`write`, `qwen/qwen3.7-flash` used `bash` 37 times and never touched the
  file tools. Do not claim file tools are the agent's default without naming the
  model, and re-check the routing work in W14 against this.
- [ ] **Goal images are unproven through the container boundary** (owner: S4+S1).
  The attachment crossed (`goalImagesAttached: 1`) and the in-image vision patch
  resolved, but no colour came back, so M06's live evidence still stands only for
  the direct non-isolated path. The stack's real path is the isolated one.

## From W1 M04 (container boundary, 2026-10-26)

- [ ] **A host `DSH_PATCH_FILES` path is invisible to the confined worker**
  (owner: S4). `DSH_` is allowlisted across `confine.scrubbed_env`, so the value
  crosses the boundary but the file does not: `openrouter_smoke.py` wrote its
  patch into the host temp dir and the worker died with `DSH_PATCH_FILES entry
  not found` before any model call. Worked around for the drill by committing
  `deploy/patches/openrouter-vision.patch.yml`, which is baked into the worker
  image — the modality declaration is now a reviewed artifact rather than a
  runtime temp file. The underlying trap remains for any other host-side path
  the parent passes down.
- [ ] **The confinement refusal names Seatbelt on Linux** (owner: S4).
  `engine.py:554` is a fixed string for "mode is neither container nor off", so
  on a Linux kernel it reports that seatbelt breaks the PTY — true, but not the
  reason a reader should care there. Reword to name the resolved mode and the
  accepted ones.
- [ ] **Container mounts fail on freshly recreated macOS temp trees** (owner:
  deploy). Re-running with the workspace tree deleted mid-flight gave `invalid
  mount config for type "bind": bind source path does not exist` against
  `/host_mnt/private/var/folders/...`. Reproduce with a stable path
  (`~/.harness-m04`-style) until it is understood.
- [ ] **`compose_journey.py` check 0 races the daemon on a stop** (owner: S10).
  The first trip attempt PASSED with `sandbox-egress` stopped because `ps -a`
  was read before the exit was reflected; three seconds later it reported
  `[FAIL] container health`. Detection works; the trip test should settle or
  poll before asserting so a documented drill never has to be re-run to be
  believed.
- [x] **The status panel reports a context window the model does not have**
  (owner: S4+S7). `state_machine._context_window()` returned `DSH_CONTEXT_WINDOW`
  (default 1,000,000) while the OpenRouter transport patch declares
  `contextWindow: 65536`, and both live drills showed 1M — roadmap ground rule 1
  forbids an affordance that does not do the thing. Closed in `a5148e0`:
  `context_window.configured_context_window()` returns the value only when an
  operator actually set it, rejects empty and non-positive values, and yields
  `None` otherwise, which the `StatusPanel` "window unknown" branch already
  handled but had never been able to reach. The three duplicated env readings
  (`state_machine.py`, `engine.py`, `execution.py`) now share it, so the run page
  and the durable `context_compacted` rows cannot drift.
  Still not done: reading the window the *selected model* actually declares, so a
  configured value is still operator-asserted rather than provider-verified.
- [ ] **`usage` is `null` on the openai-completions transport** (owner: S4).
  Carried over from the OpenRouter drill: without usage, `cost_ledger` accrual
  on that path is unproven, so the cost footer and per-run spend are both
  unfunded there.

## From W1 M01 (confinement spike, 2026-10-26)

- [x] **Scope enforcement needs mounts, not parsing** (owner: S5+S4, gates M03).
  **Closed 2026-10-26 (M03).** Container worker: a narrower intent mounts the
  workspace read-only and binds only existing `scopePaths` back writable
  (`confine.scope_targets`; a scope at or under a forbidden path is dropped, a
  missing one stays read-only). Shell gate: the scope is the OS boundary's write
  set (`confine_argv(write_roots=…)`, Seatbelt and Landlock), so the M01 bypass
  is refused in host mode too. Native write/edit are now ON under a narrower
  scope when the workspace is observed read-only (`engine._read_only`), and the
  worker is told its boundary (`HARNESS_ENGINE_CONFINEMENT` crosses), which
  also stops an `off` worker on macOS reading itself as `seatbelt`. Live:
  `scripts/e2e/m03_scope_drill.py` → `m03-scope-mounts.json`, native `write`
  into `src/` succeeded and into `docs/` failed `EROFS`. Limits: a scope that
  names a *file* is not writable through the native tool (it stages a temp
  beside the target, in the read-only parent); `.git` is read-only unless in
  scope. Landlock write roots were then measured inside the worker image
  (linuxkit 6.12, `m03-landlock-scope.json`): the three M01 writes refused
  `EACCES`, `src/` allowed, an unscoped control allowed.
  Original entry:
  The approved scope is unenforced: with `scopePaths=["src"]`, writes to `docs/`
  succeeded as a literal, a concatenation and an `os.path.join`
  (`m01-confinement-spike.json`). `python3` is the only file-writing binary on the
  allow-list and redirection is banned, so code strings are not an edge case —
  they are the entire write surface. Close it with OS/mount restrictions on the
  scope subtree; do not extend the string matcher.
- [x] **Landlock column of the spike is unmeasured** (owner: S5). Measured
  2026-10-26 inside a container on the same Docker Desktop kernel, where
  Landlock *is* available (`m03-landlock-scope.json`); the `ENOSYS` below was
  not reproduced there. A bare Linux host run is still worth having. Docker
  Desktop's linuxkit kernel returns `ENOSYS` for `landlock_create_ruleset`, so
  `scripts/m01_confinement_spike.py` can only answer for Seatbelt until it runs on
  a Linux host. Re-run there and record both backends before M03 is designed.
- [x] **Native `tool-fs` read/write/edit are not in the matrix at all**
  (owner: S4+S5). Write measured by the M03 drill (container, scope `src`):
  in-scope write succeeded, out-of-scope write refused by the kernel. Edit not
  separately driven. They bypass the shell gate and run inside the worker. The
  confined worker now runs, so add a scope-forbidden write through the native
  tool to the spike rather than assuming it matches the shell result.
- [ ] **A forbidden path with no read-only mount disables the shell tool**
  (owner: S5). `unprotected_forbidden` refuses every command, including ones that
  touch nothing forbidden. Fail-closed is right; the cost is that the tool is
  unavailable rather than scoped, which should be a stated product consequence,
  not a surprise in the run page.

## From W1 M07 (acceptance blocked on topology, 2026-10-26)

- [x] **The deployed topology cannot create a confined worker** (owner: S4+S10,
  blocks M07). **Closed 2026-10-26 with the worker-daemon option**: see
  `docs/sandbox-isolation.md` "Engine worker daemon (M07)" and
  `scripts/e2e/m07_daemon_drill.py` → `m07-daemon-topology.json`.
  Original entry: The orchestrator container has no Docker socket and no CLI,
  `container` is one of only two modes the runtime accepts, and
  `HARNESS_ENGINE_ISOLATED` / `_CONFINEMENT` / `_IMAGE` are set in no env file or
  compose service — so isolation is unset (in-process) or refuses. Choose the
  worker-daemon option; do not mount the socket, and do not ship `off`.
- [ ] **Evidence collected on a host-only topology** (owner: S10). M04 and both
  M07 attempts ran as host processes with the host's `docker` CLI. Valid as
  behaviour, not as proof the stack can do it. Re-run through the stack once the
  daemon exists.
- [ ] **A stuck run has no tool-call budget in practice** (owner: S4, W2).
  `remaining_tool_calls` exists (`execution.py:437`) but nothing applied it to
  these runs, and it counts shell-plugin calls — native `tool-fs` calls may not
  consume it. 14 and 37 tool calls against one trivial goal, stopped only by the
  wall clock, is the symptom.

## From W1 M07 closure (API-driven run, 2026-10-26)

- [ ] **A settled run's `/engine` result loses model and usage** (owner: S4,
  M05). `GET /runs/{runId}/engine` answers the in-RAM dict while the run is live,
  but a settled run is evicted and the route rebuilds the result from the
  `ENGINE_COMPLETED` / `ENGINE_FAILED` event payload
  (`service.py:_load_engine_result`), which carries only the summary. The
  replayed shape is `{sessionId, engine: "replayed", status, finalResponse}` —
  so "which model ran" and "what did it cost" cannot be answered after the fact
  even though the live dict had both. Cost metering is unaffected: it reads the
  live dict before eviction. Evidence: `m07-api-run.json`
  (`engine.replayLosesModelAndUsage: true`). Fix shape: carry `usedModel` and
  `usage` in the completion event payload (additive; check the event schema and
  golden fixtures before touching the contract) and restore them in the replay.

- [x] **M07's two remaining legs** (owner: S10 + S4). Closed 2026-10-26 over
  HTTP: `m07-api-run.json` (plan → approve → start → confined worker fixes and
  re-reads a fixture repo), `m07-vision-api.json` + `m07-vision-api-purple.json`
  (screenshot uploaded as an artifact, named correctly for two different
  colours). Drills: `scripts/e2e/m07_api_run_drill.py`,
  `scripts/e2e/m07_vision_api_drill.py`.

- [ ] **`.env` model drift** (owner: deployment). The local stack set
  `DSH_MODEL=qwen3.5:9b` while the configured Ollama only had
  `qwen-local:latest`, so any API-driven run would fail on a model that is not
  installed. Fixed in the gitignored `.env` on this host; `.env.example` should
  state that the name must match an installed model, and a startup probe could
  refuse a live deployment whose model the endpoint does not list.

## From the 2026-10-26 full-project test sweep

- [ ] **The bracketed-IPv6 `NO_PROXY` crash is not only Section 1's** (owner:
  S10 + mcp). The SSO fix (`b99b316`) added a local `env_client()` that strips
  the bracket when httpx chokes on Docker Desktop's `NO_PROXY=...,[::1]`. The
  same crash still hits **Section 10** (`tests/unit/test_state.py`, 4 tests) and
  the **MCP server** (`mcp/tests/test_tools.py`, 18 tests), which build their own
  `httpx.AsyncClient` without it. Verified on this host: both fail as-is, both go
  fully green with `[::1]` removed from `NO_PROXY` (section10 91 passed, mcp 18
  passed). This is environmental — it does not fail on a clean shell — but three
  sections now carry the same latent bug. Decide where the helper lives: a shared
  `harness_contracts` client factory (one fix, contract bump) versus a copy per
  section (the cross-section import law forbids Section 10 importing Section 1's).
  Recommend the contracts route so a fourth section cannot rediscover it.

- [ ] **`test_index_lifecycle_over_http` is intermittent under load** (owner:
  S11). Seen once (`assert 7 == 8`) during a full sweep that ran every section
  suite back to back; it did not reproduce in 5 isolated runs or 2 full-suite
  runs afterwards. The suspect is line 90, `assert int(n) ==
  status_body["chunksIndexed"]` — a `SELECT COUNT(*)` racing the indexer's own
  writes, the same class as the Section 4 races fixed in `4632550`. Not a
  deterministic order dependency (the full suite and the single test both pass in
  isolation). Pin the count to the job's committed output rather than a live
  table count.

### What was green in that sweep
section1 992 (after `56af081` fixed engine-worker's missing resource limits + log
rotation), section2 48, section3 128, section4 1722, section5 319, section6 123,
section8 91, section10 91 and mcp 18 once `NO_PROXY` is sanitized, section11 50.
The M07 `engine-worker` compose service had quietly broken Section 1's two
stack-law tests; that was this branch's regression and it is fixed.

## From the whole-project test run (docs/test-report-2026-10-26.md)

- [x] **`env_client` belongs in `harness_contracts`, not in three more sections**
  (owner: S1 + affected sections). The bracketed-IPv6 `NO_PROXY` failure that
  turned SSO login into a 500 is reproduced in **section10 (4 tests), section11
  (1) and the MCP server (3 failed + 15 errors)** — all green with the variable
  unset, all dying at `httpx` client construction with it set. That is a runtime
  shape, not a test shape: a deployment inheriting Docker Desktop's
  `NO_PROXY=[::1]` breaks the same way. Copying Section 1's helper into each
  service multiplies exactly the drift the isolation guard exists to prevent, so
  the fix is a contracts addition — which requires a version bump, regenerated
  TS mirror and golden/mock conformance in the same change. **Done 2026-10-26**:
  `harness_contracts.http_client` (async + sync), contracts `1.0.0rc5`, TS mirror
  header regenerated, Section 1's module is a re-export, section10 and the MCP
  client use it, and both suites pass with the bracketed variable still set.

- [ ] **Section 11's index job can report one more chunk than it stored**
  (owner: S11). `test_index_lifecycle_over_http` failed 1/5 with `assert 7 == 8`:
  the job's `chunksIndexed` said 8 while `context_chunks` held 7 rows. Not caused
  by `NO_PROXY` (0/5 failures with it unset is correlation on one sample, not a
  cause) and not a flake to re-run until green: the race is between the async job
  finishing and its own report reading the row count.

- [ ] **`test_ws_replay_cap.py::test_resume_never_resends_at_or_below_afterseq_during_concurrent_publish`
  is load/order dependent** (owner: S1). Passes 5/5 in isolation; failed once in
  a full section1 run and passed in another. Same class as the two Section 4
  flakes fixed today, and it sits in the reconnect/replay-dedup area W1 recorded
  as verified only in the reducer. Reproduce by widening its timing window rather
  than re-running, as with the Section 4 pair.

- [ ] **Every gate edit rebuilds Chromium and every wheel** (owner: S4/deploy).
  `section4/worker.Dockerfile` installs Chromium in the `worker` stage, which is
  `FROM base` — and `base` ends with the `COPY` of `section4/src` and the Section 5
  gates. So touching one gate line invalidates the apt layer: W11 measured a
  40-minute re-download of ~200 Debian packages for a one-line plugin change, at
  the same hour the backend image re-resolved every Python wheel because
  `deploy/backend.Dockerfile` copies all eight sections before `pip install`.
  The images are correct; the layer order makes the boundary files the most
  expensive thing to change, which is the opposite of how often they should be
  reviewed. Fix is layering, not caching: install Chromium and the wheels in
  stages that depend only on lockfiles/`pyproject.toml`, and copy the sources and
  gates last.
