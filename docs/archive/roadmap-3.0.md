# Harness Roadmap 3.0 — trustworthy, governed agent work

**Proposed 2026-10-31.** This is a sequencing and decision document, not a
production-readiness claim. It carries forward the open gates and evidence in
[Marathon 2.0](m2-continuation-plan.md), the per-week plans, [STATUS](../STATUS.md)
and the [Hx architecture](hx-execution-architecture.md). When those differ,
reconcile against the exact tested revision and raw evidence; historical tables
are not automatically current. Week labels are dependencies, not calendar dates.

## Product direction

A user describes an engineering goal and constraints. Harness proposes a
reviewable plan, optionally recommends resource assignments, obtains approval,
executes through authorized and confined tools, and presents changes, tests,
cost and recovery evidence. An optimizer can recommend; it never grants
permissions, bypasses forbidden paths or automatically merges work.

## Current position

*(Refreshed 2026-11-21; the stage table below is scope and exit evidence, not
status. Per-stage state and the freshness of every claim live in
`STATUS.md` and `docs/briefs/PROJECT-STATE.md` §2 — read those first.)*

* The recorded product verdict is **NOT READY** for production or a general
  pilot. A design-partner pilot requires its documented gates and owner decision.
* Hx M1–M8 are complete (`332d273` … `9e54132`): checked workflows, Section 4/1
  event integration, Section 5 host bridge, model relay, bounded constructs,
  budget enforcement against a host price table, mid-run grant revocation,
  honest failure replay, and multi-file programs with per-module digests. The
  recorded limits stand (CPU bounding, replay cannot undo effects, resolver
  trust) — see the 2026-10-25 STATUS entry.
* Stage 4 (benchmark): the trial harness is built and dry-verified —
  `bench_trials.py` enforces ≥5 independent trials per task per model, refuses
  less, and the 2026-11-21 dry smoke ran 5 trials at zero spend
  (`docs/briefs/evidence/w7-bench/`). The live ≥5-trial run and the
  other-harness comparison remain open (model spend = owner budget call).
* Stage 5 (governance W27): slices 1–3 committed and deployed (denial reason +
  alternative + scope request; circuit breaker; repeated-denial stop), drilled
  live on Haiku.
* Stage 6 (delegation W28): the delegate verb, child runs with own approval and
  cancel cascade, read-only synthesis and the W13 exec leg are built and
  live-drilled; `HARNESS_SUBAGENTS` still gates it inert by default. A live
  two-agent drill passed 11/11 on 2026-11-05.
* Stage 9 (W32): seam analysis only, gated on W28 usage evidence. Stage 10
  (W33): readiness pack written, independent review not done.
* The Nov 13–16 UI overhaul wave landed 2026-11-21 with the P-17 client trust
  portal verified (per-project guests, default-deny gate in both principal
  resolvers) and I-09 verification-first default-on; suites at `e12890c`:
  section1 1326/0, section4 2392/0, section5 542, section8 104, section7 1201 +
  build. The nightly backup lane was repaired to a green run the same day after
  12 red days (environmental root causes; two owner items open: demo-db port
  collision on 5499, no offsite target).

## Delivery sequence

| Stage | Scope | Required exit evidence |
|---|---|---|
| **0 — Stabilize and baseline** | Reconcile concurrent changes and review the login resolution. Capture exact commit, image digests and configuration; run suites with skip reasons. Separate environmental skips from defects and track each. | Clean, reviewable baseline; test/skip ledger; no unresolved merge conflicts; current evidence linked to revision. Do not overwrite other work. |
| **1 — Trust and recovery (W21–W22)** | Finish confinement coverage for each supported backend; validate worker/child cancellation, orphan cleanup, lifecycle truth, partial-write reconciliation, replay, restart and provider recovery. | Deployment-level drills capture before/after state and process disappearance; failures and unknowns retained; no false cancellation confirmation; repeat where reliability is claimed. |
| **2 — Reliability and ownership (W17)** | Validate terminal-event delivery, remaining failure drills, restore and monitoring. Measure dry and real-provider load on target topology. | Measured capacity and failure outcomes, backup/alert verification, named SLO and on-call owner. Provider experiments require agreed budget. |
| **3 — Daily product loop (W23–W24, W7)** | First-run readiness, drift detection, honest live status, stop and steering; review summaries; complete a real VS Code task. | Fresh setup and stale-daemon detection; real stop; installed extension completes one task through policy, approval and reviewed diff. |
| **4 — Benchmark and improve (W25–W26)** | Real-repository benchmark with independent trials, held-out verification, security tasks, latency, cost and recovery; repair largest measured defects. | Versioned protocol, raw results including failures and skips, budget/model/revision recorded; improvement shown against baseline. |
| **5 — Governance and Hx (W27)** | Version-bound plans, scope, risk and verification; understandable denials; actual versus estimated cost; inspect/rewind/resume. Address Hx cost enforcement, revocation and failure replay where required by product use. | Live approval-bound task, denial and recovery demonstrations; tests for limits, no silent downgrade and evidence integrity. |
| **6 — Delegation and remote execution (W28–W29, W13)** | Isolated subagents with quotas and ownership; remote BYO runner agent loop, cancellation, lost-lease recovery, transport hardening and operator UX. | Two-agent task with one cancelled and no orphan, reviewed synthesis; real remote-host run, isolation, lease/replay/cancel tests; no auto-merge. |
| **7 — Integration audit (W30)** | Audit Scheduler, Drift Workbench, Headroom, immunity prototype and other candidates for provenance, license, dependencies, tests, data flow, security and cost. Resolve Hermes rights. | Written adopt/pilot/defer/reject decision and evidence per candidate; no component is accepted on popularity or README claims. |
| **8 — Constraint planning (W31)** | Prototype Scheduler/OR-Tools on synthetic agent workloads after recovery is proven. Compare its timetable-specific contract with a fresh model. | Feasible/infeasible, deadlines, dependencies, capacities, timeout, explanation and independent validity tests; measured performance; no real dispatch until approved. |
| **9 — Governed integration (W32)** | If stage 8 succeeds, connect proposals to approved Hx/Harness execution. Evaluate OpenTelemetry with redaction and tenant isolation. | End-to-end plan, approval, execution, denial, cancellation, recovery and review at a verified revision; telemetry cannot leak secrets. |
| **10 — Optional ecosystem and release (W33)** | Evaluate diagnostics, context optimization, parsing and observability candidates against measured needs. Repeat readiness review and assess third-party MCP. | Per-candidate security/license/quality results, documented decisions, independent release review and authorized owner decision. |

## Detailed commitments carried forward

These details supplement the delivery sequence and are required parts of the
corresponding stages, not optional additions:

* **W23–W24:** readiness checks cover provider, model, repository, worker and
  permissions; steering states when it takes effect. Review lists changed files,
  tests, failures, cost, elapsed time and uncertainty. Provide keyboard-first
  diff review and reconcile each item in `ui-ux-audit-2026-10-25.md`.
* **W25–W26:** record time to first useful output and verified completion,
  success, cost, tool calls, interventions and recovery. Include cold/warm
  starts, concurrent live runs and p50/p95 queue and completion times. Rerun the
  fixed live set (`multi-file-refactor`, `tests-written`, `w11-grounding`) on
  the selected model; decide the model or retirement of the six locally pinned
  tasks. Compare another harness only under the same task, model, budget and
  permissions. Rerun the benchmark after fixes, retaining every failed attempt.
* **W27:** policy denials explain the reason, offer a safe alternative and let
  a user request more scope. Retry or model change is explicitly user-selected.
* **W28:** isolated subagents have conflict handling, visible ownership and a
  reviewable synthesis. Their spawning and cancellation depend on W22 recovery.
* **W29/W33:** review current Claude Code, Codex and Cursor capabilities against
  official documentation, with dated sources. Refill the W20 checklist from raw
  evidence at the final revision and hand it to an independent reviewer.
* **W30:** inventory all discovered projects, including backups, duplicates,
  unrelated projects and third-party checkouts; record whether each is audited,
  deferred or excluded and why. An inventory is not a completed code audit.
* **Evidence:** record provider spend in `cost_ledger` when available, identify
  unknown spend, keep failures and skips, do not loosen preregistered thresholds,
  and rerun vision evaluations when models change. Capture source and image
  revisions, test environments and the scope of each result.

## Owner and external gates

A named accountable owner must adopt or revise the SLOs and error-budget rules;
assign on-call, escalation and public-status responsibilities; verify alert
routing and backup-freshness monitoring; and complete a restore on the intended
deployment. An independent release-candidate review, external security review,
legal review of terms and pricing, Hermes rights clearance, required package
publishing credentials and an authorized W20 release decision remain separate
gates. Stripe remains undrilled and flag-off unless its scope is explicitly
changed. Paid provider tests require an agreed budget. No engineering milestone
implicitly closes these decisions.

## Hx scope and limitations

Hx M1–M5 are complete, not a claim that all language features exist. In addition
to the limitations in Current position: secret detection does not catch every
possible secret shape; `for` has no `break`; only one workflow runs per run;
`while`, assignment, modules, generics, traits, match and money arithmetic are
outside the documented closed set. These are design limits, not automatically
scheduled features. The evidence-based product decision in stage 5 determines
which limits, if any, require additional work. See [Hx architecture](hx-execution-architecture.md)
and [language spec](hx-language-spec.md) for authoritative detail.

## Candidate integration decisions

* **Scheduler:** pilot an adapter; its current solver models academic sessions,
  rooms and fixed slots. Keep the existing queue and policy authoritative.
* **Hx:** use the existing implementation; close its limitations when demanded
  by a use case, without claiming it supplies isolation itself.
* **Hermes:** separate project; proprietary rights and distribution review before
  integration. A shared editor experience can be explored independently.
* **Drift Workbench:** optional permissioned diagnostics through the tool gateway;
  restrict network destinations and redact evidence.
* **Headroom:** benchmark context quality, cost and privacy before use; preserve
  access to review-critical source material.
* **Agentic immunity:** compare its prototype attestation and revocation ideas
  with existing identity, policy and audit; do not create a second authority.
* **Tree-sitter:** compare symbol indexing with existing LSP/repository maps.
  **OpenTelemetry:** evaluate instrumentation and redaction. **Langfuse:** optional
  model-evaluation tool with component license review. **Temporal:** comparison
  experiment only; do not add a second workflow authority by default.
* **Odysseus and Dify:** study UX/features and license implications; no wholesale
  merge planned. **book-to-skill:** optional untrusted-document import requiring
  review. Provider proxies must respect user-selected models and secret controls.

## Non-negotiable boundaries

Frozen contracts change only with a version bump and matching mocks; schemas use
new numbered migrations. Section 1 owns identity, secrets and event delivery;
Section 4 owns event semantics; Section 5 confines execution and enforces its
allow-list and forbidden paths. No auto-merge, forced model downgrade or
per-keystroke analysis. Every section retains golden-fixture conformance tests.
External dependencies require pinned versions, license review and security
assessment. Human owners decide SLOs, legal terms, security review, provider
budget and release; implementation does not silently invent those decisions.

## Immediate next actions

1. Review the staged login resolution and local auto-sign-in with the intended
   deployment in mind; inventory the active W22 work and its owner.
2. Run relevant suites with skip reasons and record the exact tested revision.
3. Reconcile W22 drill evidence and finish recovery gates; continue W17 work.
4. Audit Scheduler's solver contract, dependencies and tests; design a synthetic
   experiment without dispatching real jobs or changing frozen contracts.
5. Maintain a decision and evidence ledger; rerun release readiness only after
   the required engineering and human gates are met.
