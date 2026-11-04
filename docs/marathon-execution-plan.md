# Harness marathon — execution plan

Prepared 2026-10-25 against commit `83e8562`. Companion to
[the product roadmap](roadmap-2026-10-25.md) and
[the Week 1 work packages](week-1-plan.md).

## Delivery decision

Recommendation: ship a limited design-partner pilot after the core agent, CLI
and Hermes demonstration pass their gates, then continue the full roadmap.
This is a recommendation, not a recorded user decision or release approval.
The 20-week scope remains the ambition; calendar dates follow evidence and capacity.

The original eight-week pilot and twenty-week full-release estimates are
unvalidated. The capability assessment itself estimates 3–4 weeks for the IDE
extension and about three weeks for a BYO runner, while the roadmap assigns
each one week. Resolve those differences after spikes; adding a second engineer
does not shorten serial dependencies or external reviews automatically.

## Baseline established in this review

Statuses below mean source inspected, not a successful deployment or live task.
No runtime tests, model calls, container changes or external releases were made
as part of this planning revision.

| Area | Observed source | Status and consequence |
|---|---|---|
| Sidecar startup | `deploy/entrypoint.sh` dispatches explicit arguments with `exec` | Fix exists; do not schedule the original missing-key hypothesis as new work |
| Proxy health | `deploy/docker-compose.yml`, `sandbox-egress.healthcheck`, expects denied CONNECT to return 403 | Policy probe exists; current image and health still need live evidence |
| Stack smoke | `scripts/e2e/compose_journey.py` enumerates configured services and uses `ps -a` | Detects stopped/missing services in source; rerun against deployment before closing gate |
| Runtime profile | `section4/src/harness_orchestrator/engine.py`, `_execution_policy_patch` | Uses restricted `sdk-minimal`, workspace-write and Section 5 shell plugin; native file-tool policy still needs investigation |
| Live tool events | Same module, `_run_live_events`, calls `harness.run(goal, session_id=session_id)` | Notification callback is absent on this path; inspect worker transport before implementation |
| Tool-event contract | `contracts/src/harness_contracts/events.py` and generated `contracts/ts/src/events.ts` | No `tool_call` or `tool_result` event variants; versioned contract package must land before streaming consumers |
| Contract version | `contracts/pyproject.toml` | Current package is `1.0.0rc2`; derive next version and all mirrors from current package, not the old AGENTS layout note |
| Git context | `engine.py`, `provision_workspace`, excludes `.git` in `copytree` | Design safe history provisioning before changing this behavior |
| Output limit | `engine.py`, `_run_live_events`, sets `max_tokens=4096` | Make provider/model limits explicit; runtime default context size is not verified model capacity |
| Intent scope | `section5/src/harness_sandbox/shell_gate.py`, `scope_violation` | Skips code strings and unparsed flag paths; whole-workspace write confinement does not enforce a narrower approved scope |

Historical live results in the Week 1 document remain historical evidence.
Do not represent them as current stack health.

### Reconciliation — 2026-10-26

The baseline above is a snapshot of `83e8562` and is kept verbatim. Work landed
after it supersedes five rows; the rest still stand.

| Row | Now | Where |
|---|---|---|
| Live tool events | `_run_live_events` takes `on_progress` and forwards turn events; the isolated worker emits NDJSON `progress` lines | `section4/.../engine.py`, `engine_worker.py` (e610191) |
| Tool-event contract | `tool_call`, `tool_result`, `context_compacted` exist in contracts `1.0.0rc3`, TS mirror regenerated, golden payloads in section1 | `contracts/`, `section1/tests/golden/` (e610191) |
| Contract version | The package is `1.0.0rc6`; rc3 added the transcript events, rc4 `goal_images`, rc6 `model_routed` (W15). It is pinned in two places that must move together: `contracts/pyproject.toml` and `contracts/src/harness_contracts/__init__.py` | `contracts/pyproject.toml`, `contracts/src/harness_contracts/__init__.py` |
| Git context | `.git` now travels, after `_prepare_seeded_git` strips hooks, worktrees, submodule pointers and credential-bearing config keys | `engine.py` (8438b19) |
| Output limit | The `4096` cap is gone; `HARNESS_SDK_MAX_TOKENS` sets the provider ceiling explicitly | `engine.py` (8438b19) |

**M00 closed 2026-10-26** on `harness-backend:vm00-1026` built from `df4096e` —
the earlier candidates `vdev-local` and `vbuild-1025` were checked first and
contain none of this week's code, so they could not have produced
current-revision evidence (`m00-stack-verification-2026-10-26.json`). **M04
closed the same day**: a confined worker streamed a native `read` at 34.85 s and a
native `write` at 48.89 s against a 68.42 s completion, correlated by `callId`,
with a real file side effect, on local Ollama at no spend and with `off` never set
(`m04-container-streaming.json`). M06 landed 2026-10-26 (`15a3a66`) and its image
reached a real vision model the same day. **M07 is open and was attempted**: the
confined screenshot run made 37 `bash` calls in 516.75 s, never returned the
colour, and cannot be priced because `usage` is `null` on this transport
(`m07-confined-vision-failure.json`). M01/M03 confinement is still open exactly as
written, and now has a live symptom — the loop-detection work W2 assumed is what
would have bounded that run. **Update, end of 2026-10-26:** M03 closed:
the scope is enforced by mounts in the container and by Seatbelt/Landlock write
roots in the shell gate (`m03-scope-mounts.json`, `m03-landlock-scope.json`). M07's
topology blocker closed with the worker daemon. The deployed orchestrator, which
has no socket, ran a confined worker that fixed and re-read a fixture repo
(`m07-daemon-topology.json`). **M07 closed the same day**, both remaining legs
driven over HTTP against the deployed stack: a run created by `POST /plan` →
approve → start reached a worker at `999:999` with a read-only root, no
capabilities and volume-subpath mounts, and fixed a fixture repo
(`m07-api-run.json`); a screenshot uploaded as a run artifact was named correctly
twice, with two different solid fills, under a 4-call budget set before the first
call (`m07-vision-api.json`, `m07-vision-api-purple.json`). Two residual
limitations are recorded rather than claimed away: a settled run's `/engine`
replay carries no model and no usage (backlog, owner S4), and browser-session
transcript replay is still unverified. The proxy row holds for the revision it was re-probed
on: `github.com → 200`, `example.com → 403` on `vm00-1026`, reproduced after a
forced sidecar restart.

Also superseded in the queue text: M04 describes the transcript as future work;
the transport and UI landed in e610191. What remains from M04 is reconnect/replay
deduplication evidence, not construction.

M06 (goal image attachment) landed on 2026-10-26 in the same branch, and the
package moved again to pay for it: contracts are now `1.0.0rc4` with a
`goal_images` event. The queue's prerequisite line — "inspect existing
upload/artifact authorization and SDK content blocks" — resolved as follows:
the artifact route already did uploads, team scoping, magic-byte MIME
verification and retention, so no new endpoint was added; the SDK's `run()`
accepts a content-block list and the bundled runtime admits
`{"type":"image","data":<canonical base64>,"mimeType":…}` for png/jpeg/webp/gif
before rewriting it into its own attachment store. Evidence still owed on M06
is the live one: an image actually reaching a vision model (M07).

## First implementation queue

Owners are proposed section responsibilities, not assignments to people. Effort
ranges below are provisional focused engineer-days, including review and relevant
verification; they do not include waiting for external decisions.

| ID | Proposed owner | Deliverable | Prerequisite | Completion evidence | Estimate |
|---|---|---|---|---|---|
| M00 | Deployment / S10 | Verify deployed image and stack health against the landed startup fix | Running dev stack | Image revision, migration compatibility, proxy allow/deny result, stopped-sidecar detection and recovery; no secrets in evidence | 0.5–1d |
| M01 | S5 + S4 | Timeboxed confinement spike across shell and native tools | None; run in disposable fixture workspaces | Inside-workspace but outside-intent writes, forbidden files, symlinks and code-string paths tested on each supported enforcement backend; document actual SDK policy hooks | 1–2d spike |
| M02 | S4 + S1 + S7 | Versioned tool event contract and fixtures | Inspect SDK notification shape and worker boundary | Stable call identity, lifecycle semantics, bounded scrubbed output, replay ordering, generated TS, version bump, mocks and golden fixtures in same change | 1–2d |
| M03 | S5 + S4 | Enforce approved scope and enable reviewed file/search tools | M01; remediation design if existing boundaries fail | Native tools and shell obey workspace/scope/forbidden-path policy; failed confinement refuses execution | Re-estimate after M01 |
| M04 | S4 + S1 + S7 | Live transcript from worker through persisted bus to UI | M02 | Call/result arrives before turn completes; reconnect has no duplicate rows; error/denial/cancellation visible; secret scrub and bounded output verified | 3–5d |
| M05 | S4 + S3 | Safe repo history and model-limit handling | History/credential review; model metadata inspected | Useful history without inherited hooks, credential config or escaping gitdir references; limits valid for selected model; actual usage distinguished from estimates | 1–3d |
| M06 | S1 + S4 + S7 | Goal image attachment path | Inspect existing upload/artifact authorization and SDK content blocks | Size/MIME limits, team authorization and retention apply; image reaches compatible model; unsupported models report limitation; any contract bump includes fixtures | 2–3d |
| M07 | S10 + affected sections | Core live acceptance run | M00, M03–M06 | Real task reads, edits and verifies a fixture repo; screenshot input and streamed evidence visible; record revision and remaining failures | 0.5–1d |

M00, M01 and M02 investigation can overlap only with actual available capacity.
M03 and M04 then follow their own gates. M05/M06 must not displace a failed
confinement gate. This queue is larger than five engineer-days; calling it
"Week 1" does not make the work fit. On one engineer, finish the spikes and
contract package first, then publish revised estimates for the implementation.

The M01 spike is not the scope fix. If intent restrictions cannot be enforced
by the native SDK policy, keep those tools disabled and implement mediation or
OS/mount restrictions covering the approved scope. Parsing arbitrary Python
strings is not a sufficient security boundary.

## Transcript design questions to settle in M02

1. Trace both direct and isolated worker execution; a callback in one process
   does not automatically reach the API or persistent event bus.
2. Define `(runId, callId)` correlation, result-before-call handling, missing
   results on crash and idempotent replay. S1 assigns persistence/replay ordering;
   S4 owns semantics.
3. Bound output and scrub it before persistence and rendering. Do not forward
   raw SDK events, credentials or model reasoning into the public transcript.
4. Define callback failure, slow-consumer backpressure and persistence failure
   behavior. A tool must not execute twice because event delivery was retried.
5. Use structured diagnostic output inside the event envelope where appropriate;
   a compiler's `{path,line,col,kind,message}` format cannot represent every
   binary artifact, approval request or tool lifecycle transition.

## Remaining marathon dependencies

The labels below map back to the original roadmap; implementation duration is
subject to the preceding gates. Each milestone also inherits the common weekly
checks and applicable pilot safety gates.

| Milestone | Prerequisites | Exit evidence |
|---|---|---|
| W2 context and recovery | Core tools, M02/M04 event semantics | Representative repo task finds relevant files; project rules reach executor; loop fixture stops within explicit budget |
| W3 interrupt, steer, approval, rewind | Worker-control spike, checkpoint semantics, user policy decision | Worker and child tool stop; resume reconciles partial writes; rejected action stays blocked; steering does not silently expand approved scope |
| W4 web, packages, MCP | Scope enforcement, reviewed egress/tool policy | Authorized request works; forbidden destination/tool denied; credentials stay outside model context; installs confined and audited |
| W5–W6 CLI | Shared API/auth and event/control paths | CLI and web satisfy same policy and audit invariants; token storage and reconnect behavior verified |
| H0–H2 Hermes demonstration | External rights/license evidence, structured diagnostics, reviewed toolchain image | Seeded Hermes bug fixed; both backends run against fixed expected results; evidence records corpus limitations |
| Pilot release | W1–W6, H0–H2, operational prerequisites, applicable external gates | Every pilot checkbox in roadmap backed by evidence; docs/support and live demonstration ready |
| W7 / H3 IDE | Shared CLI/API/control path; LSP scope spike | Review in editor passes through policy; Hermes hover/definition/rename meet declared semantics; second-language integration evaluated |
| W8 isolated concurrency | Workspace/checkpoint isolation and cancellation | Three concurrent runs do not share writable state or overwrite checkpoints; cleanup handles crashes |
| W9 artifacts/preview/deploy | Versioned artifact contract, approval path, tenant authorization | Preview and share URLs enforce access; deployment approval binds reviewed revision; revocation works |
| W10–W11 browser and vision | Artifact limits, egress isolation, image path | Reproduce/fix/recheck UI with before/after evidence; browser credentials and network remain confined |
| W12 / H4–H5 memory/hooks | Reviewed permission model; Hermes isolation/resource spike | Learnings reviewable; hook/playbook cannot grant capability; time/memory/output exhaustion contained |
| W13 BYO runner | Auth/token threat model, worker lifecycle, attestation design | Revocation, replay protection and team isolation verified; documented trust assumptions for customer-controlled host |
| W14 / H6 evaluation/routing | Fixed expected outputs, held-out corpus, metering | Reproducible model comparisons; routing requires opt-in; failure never forces a downgrade |
| W15–W17 operations/enterprise | Measured tenant/load envelope; external review scheduling | Isolation/backup/failure drills, required security review, SLOs and on-call ownership; enterprise claims have evidence |
| W18 / H7 full release | Full-release gate including promised Hermes deliverables | All offered workflows pass documented gates; distribution/license and terms prerequisites resolved |

Tree-sitter supplies incremental syntax and queries; scope resolution, definition,
references and rename still require language semantics. H3's estimate must include
that work. H4 must not treat absence of host functions as proof that an untrusted
C++ VM can safely run inside the API process. H6 demonstrates correctness over
its declared corpus, not correctness of arbitrary agent changes.

## Scope and capacity reconciliation

- Semantic repo search (gap B2), notebook execution (A10), persistent shell
  sessions (C7) and the remainder of the UI audit lack complete allocations in
  the twenty-week roadmap. They remain backlog candidates; no universal parity
  claim is justified until selected workflows and exclusions are explicit.
- Section 11 already contains context ingestion, chunking and search code.
  Evaluate that service before commissioning a new semantic index; this review
  has not verified its runtime integration or quality.
- H6 benchmark work competes for the same engineering time as the core agent.
  Schedule it explicitly rather than treating it as free background progress.
- W16's counsel, penetration testing and enterprise provisioning can involve
  external lead times. Start scheduling early; engineering cannot close external
  evidence gates by replacing them with documentation.

## Evidence and release discipline

For each package record: commit, supported environment, expected behavior,
observed result, sanitized evidence path, remaining failure and reviewer.
Track source landed, local checks passed, live acceptance passed and released
as separate states. An unchecked gate stays open when a dependent week begins.

For implementation changes, run the affected section suites and lint plus
`make static-gates`. Run Section 7 typecheck/unit/build for UI changes and a live
compose journey at the milestone gate. Frozen-contract changes additionally
require generated mirrors, version bump and golden/mock conformance across
consumers. The required Section 1 tests and lint must pass before any PR.

Reversible documentation-only revisions need a diff/format/link review, not
the full application suite. This revision creates no implementation-completion
or production-readiness claim.
