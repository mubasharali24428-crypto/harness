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

- [ ] **The isolated worker has never streamed on a real host** (owner: S4+S10).
  `--isolated` against local Ollama returned `error` in 0.0 s with zero tool
  calls: the confined worker refuses because Seatbelt breaks the runtime's PTY
  and no container boundary was available. Correct guard behaviour — running it
  with `HARNESS_ENGINE_CONFINEMENT=off` would have proven nothing worth
  claiming. M04's NDJSON `progress` transport is unit-tested but not
  live-verified, and M07 stays open until it is, on Docker or native Linux.
  Evidence: `docs/briefs/evidence/pilot-prep/m07-ollama-isolated-refused.json`.
- [ ] **The status panel reports a context window the model does not have**
  (owner: S4+S7). `state_machine._context_window()` returns `DSH_CONTEXT_WINDOW`
  (default 1,000,000) while the OpenRouter transport patch declares
  `contextWindow: 65536`, and both live drills showed 1M. Roadmap ground rule 1
  forbids affordances that do not do the thing. Fix in W2's context work: read
  the window for the selected model, or label it `configured` rather than
  presenting it as the ceiling. Do not replace it with another guess.
- [ ] **`usage` is `null` on the openai-completions transport** (owner: S4).
  Carried over from the OpenRouter drill: without usage, `cost_ledger` accrual
  on that path is unproven, so the cost footer and per-run spend are both
  unfunded there.
