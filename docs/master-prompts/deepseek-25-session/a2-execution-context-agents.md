# A2 — Execution, Context, Memory and Multi-Agent Runtime

**Paste this entire file into ONE DeepSeek V4.1 Flash agent. It contains all five sessions for this agent.**

## Binding execution rules — included in every agent prompt

You are DeepSeek V4.1 Flash, the sole implementation agent for the lane identified below. All five lanes use this same model. Execute all five numbered sessions in this prompt in the current agent, progressing autonomously through safe work. A “session” is an independently restartable work package, not a promise of a fresh model context or an automatic model switch. If the host stops for context/time limits, leave a precise checkpoint; resume the named session without making up unseen results. Do not spawn additional coding agents or coordinate writes with other lanes.

### Repository and baseline

Repository: `/Users/user/untitled folder 2/harness`. Reference baseline: `31bf4077e9b567b1f50ecb99818d501baab3115a`. Read current AGENTS.md, applicable scoped instructions, README, actual contracts/api-conventions.md, Makefile and relevant source before editing. The existing version and next migration number must be discovered, not copied from stale docs. As of review there was a new 0034 migration and contracts had evolved beyond the old v0.2 summary.

Use your own isolated Git worktree/checkout based on the agreed baseline, e.g. a managed worktree on `codex/harness-aN`, or a user-authorized local worktree. Never run five modifying agents in the same checkout. Preserve pre-existing dirty/untracked work; do not reset, stash, clean, overwrite or move it. If no isolated writable checkout is available, prepare a lane-specific patch/output bundle without applying it to the shared checkout. Never silently change the agreed baseline. If the user has newer intended changes, record exact HEAD/diff and revalidate assumptions; do not discard them.

The review inputs under docs/reviews and this prompt pack were originally untracked, so a new worktree at the reference commit may not contain them. This prompt embeds your entire 30-feature assignment, ten future risks, evidence summary, ownership law and session instructions: it must remain usable without those files. Optional read-only sources in the original checkout are docs/reviews/2026-10-17-{harness-review.md,feature-coverage.csv,future-risk-coverage.csv}. Do not wait for access to a prior chat.

### What the baseline establishes — reverify what you change

Fresh review gate: S1 639 passed/10 skipped; S2 36; S3 95; S4 526/7 skipped; S5 181/1 skipped; S6 118; S8 88; S10 69; frontend 507 plus TS/static/lint green. Section11 separately 49/1 skipped. MCP own venv missing in the review; historical 18-pass claim not freshly rerun. Real provider/IdP/object-store/full browser/build evidence was not refreshed. These numbers are evidence history, not permission to skip your checks.

Priority findings: A1 engine.py changes process-wide DEEPSEEK_API_KEY/BASE_URL around concurrent thread calls (synthetic distinct-key race reproduced); A2 shared DSH_WORKSPACE and missing proven SDK→S5 enforcement link; A3 SDK thread keeps running after logical timeout/cancel; A4 rules/context assembled in planning but live execute receives only plan.goal. A2 lane owns fixes in Section4; other lanes enforce their own boundaries, regression-test with baseline adapters and do not claim those global findings fixed by unrelated code.

The 150 rows are a competitive wishlist containing already-present features and bundled capabilities, not 150 verified defects. Current review: 4 implemented, 91 partial, 54 not found, 1 intentionally excluded (forced fallback). Reverify assigned rows; preserve working behavior. The 50 VR numbers are the voice-session FUTURE risks, not R-01…R-50 in docs/pilot-remediation. Treat each VR risk as ongoing exposure; code controls do not prove market/legal/customer acceptance.

### Ownership and independence

You may READ the whole repository, but WRITE only your ownership entries and `docs/agent-program/<your-agent-id-lowercase>/**`. Other lanes own everything else. All root/shared files not explicitly assigned are read-only; AGENTS.md and this prompt pack are always read-only. New dependencies go in an owned package manifest/lockfile. Never edit another lane's manifests, tests, deployment config or source even if an import/build failure would be easy to patch there.

| Agent | Exclusive existing code ownership | Exclusive new code namespaces |
|---|---|---|
| A1 | `section1/**, section3/**, contracts/**` | `packages/security-policy/**` |
| A2 | `section4/**, section6/**, section11/**` | `packages/agent-runtime/**` |
| A3 | `section5/**` | `workers/browser/**, workers/desktop/**, packages/verification/**, scripts/agent-program/a3/**` |
| A4 | `section2/**, section7/**` | `integrations/editors/**, apps/desktop/**, packages/dev-cli/**` |
| A5 | `mcp/**, clients/**, section8/**, section10/**, deploy/**, .github/**, Makefile, .env.example, .gitignore, scripts/sdk-gen/**, scripts/loadtest/**, scripts/e2e/**` | `packages/extensions/**, integrations/connectors/**, packages/telemetry/**, scripts/agent-program/a5/**` |

Agent1 alone owns contracts changes and section1 migrations. Agent5 alone owns global composition, CI, Makefile and existing SDK distribution. Agent4 alone owns existing section7 UI, frontend package/lock and section2 Git providers. Agent3 alone owns S5 and new computer/browser workers. Agent2 alone owns execution/context/meeting runtime.

Each session must run and be verifiable against BASELINE APIs, existing frozen contracts and its own implementation, in any session order. It must not require another lane's output or another session's completion. On restart, inspect current files and create all necessary owned prerequisites for that session from this prompt/baseline; reuse compatible own work idempotently, but never rely on undocumented earlier state. Place independent session helpers/fixtures in session-scoped modules; expose missing optional services through capability-checked adapters. Unsupported safety-critical dependencies fail closed. Fake adapters are test-only, never production implementations or evidence of real-provider success.

If end-to-end wiring requires another owner's modification, finish your functional independently runnable component + baseline adapter + exact integration proposal + failing/blocked acceptance criterion. Mark NEEDS INTEGRATION rather than DONE. Do not wait, message another lane, weaken an invariant, or silently modify its files. Independent implementation does not mean the final combined release is integration-tested; that is a separate acceptance step after all outputs exist. Keep production rollout disabled until combined acceptance. Optional tools/connectors may be feature-gated off; security fixes must not leave the original vulnerable live path enabled as a fallback.

### Predeclared interoperability profile HAP/1 (transport agreement, not a new frozen core event)

New lane HTTP adapters use existing /api/v1, camelCase, UUID identity, frozen error envelope, bearer identity and existing RBAC. Never trust body teamId/userId as authorization. Every operation derives principal/team from the authenticated server context and verifies project/run ownership. Cross-section imports remain ONLY harness_contracts; use authenticated HTTP/explicit transports for other services.

Independent lane endpoint prefixes: A1 `/api/v1/policy`, `/api/v1/identity` (new endpoints only); A2 `/api/v1/agent-runtime`; A3 `/api/v1/computer-use`; A4 `/api/v1/developer-workflow`; A5 `/api/v1/extensions`. Existing endpoints remain compatible. These namespaces are lane-owned optional adapters, not an instruction to rename existing routes. For ports use isolated dynamically allocated test ports; deployment overlays must not collide.

For NEW cross-lane operation requests use this agreed JSON shape with owned local DTOs, NOT cross-imported lane classes:
`{schemaVersion:"hap/1",requestId,projectId,runId,operation,arguments,observationId?,approvalId?,idempotencyKey?}`.
Responses: `{schemaVersion:"hap/1",requestId,status:"succeeded"|"denied"|"failed"|"needsApproval"|"unsupported",result?,artifacts:[],error?}`.
Artifacts contain only `{artifactId,mediaType,sha256,sizeBytes,redactionStatus}`; fetch via separately authorized service endpoint, never caller-supplied arbitrary filesystem paths. Optional absent fields are omitted. Treat arguments/result as bounded JSON validated by operation-specific schemas. requestId is correlation, not authorization or idempotency. Bind approval/grant to team/project/run/operation+argument digest, expiry, and intended audience. Re-observe after state changes. A service's configured URL is not permission to contact arbitrary hosts.

Every producer independently publishes OpenAPI/JSON schema and golden wire fixtures under its own output namespace and has local conformance tests. A consumer implements HAP/1 explicitly, tests it against a faithful local stub AND baseline denial paths, and reports a peer-integration test pending. Nobody can claim counterpart execution from a mock. This profile is an advance agreement for new adapters, not authorization to change the frozen core schemas. Agent1 can independently introduce additive contract definitions/version/mirrors/mocks, but no other lane may depend on those unpublished additions in its checkout. Cross-lane activation awaits combined integration/conformance against those definitions.

### Safety and change discipline

No auto-merge. No forced downgrade/model substitution. No per-keystroke analysis. No secret values in prompts/logs/evidence; only names/hints/IDs. Vault-owned keys stay in Section1/3; workers should receive scoped capabilities or approved provider proxy handles, not stored shared secrets. A necessary legacy SDK secret bridge must be explicit, minimal, isolated and reviewed; do not broaden secret access silently. All executable tools remain under Section5 enforcement; no independent browser/desktop/MCP route may bypass it. Preserve forbidden_paths as hard constraints.

Breaking a frozen contract requires version bump + all mirrors/mocks/golden consumers in the same eventual change set/commit. Agent1 owns this and must check consumer compatibility, recording any non-owned patch proposal. Never edit a previously applied migration. Do not create an unsafe schema-drift workaround in a private DB table merely to avoid the migration owner. Without a baseline-compatible persistence path, finish the owned functional component and report the specific integration requirement. No commits, pushes, deployment, production-data mutation, branch protection change, publishing, external messages or paid service actions unless separately authorized. Local reversible builds/tests are authorized; use disposable databases/repos/test providers, not customer environments.

Historical pilot Session1/2 check-in rules apply to that earlier pilot program; do not import them as blanket stop-after-every-100-lines rules. The current user authorizes these five sessions. Proceed through necessary safe work; ask only when a real external approval/access/irreversible decision is required and continue independent work meanwhile. Legal counsel, paid pilots, demand, pricing acceptance and real credentials are human/external validation; produce concrete review-ready artifacts instead of fabricating clearance.

### Each session's required workflow

1. Reconstruct baseline and owned changes from actual HEAD, AGENTS and source. Identify the SIX assigned F rows below, keep their original meanings/subparts and list relevant VR risks. Verify whether each claimed gap exists before coding.
2. Write a bounded session plan, file allowlist, dependencies on existing APIs, meaningful acceptance cases and rollback plan. Implement the complete safe slice and all necessary owned prerequisites. Do not stop after research/scaffolding or return only a plan.
3. Add tests for behavior, failure, malformed input, denial, cross-tenant access, replay/idempotency, timeout/cancel and persistence where relevant. Test actual production seams. No weakening/deleting/skipping to make green; distinguish unsupported environment from a product failure. UI work needs real render/interaction validation; transport mocks alone do not validate UI or desktop behavior.
4. Run relevant owned suites/lint/types/build and new tests. Use per-section venvs; use clean-room environments when missing rather than mutating another agent's venv or shared installed packages. Use isolated databases/schema names, ports, browser profiles, output dirs, dependency caches and Docker project names. Do not run global compose down or destructive shared migration/reset. Run broad gate once after a coherent owned batch when feasible; repeat only for changes/failures that warrant it. Section11/MCP/browser/build/live-PG are separate gates; do not imply make gate covers them.
5. Record actual commands, exit codes, counts, skips, warnings, durations, hardware/viewport/model versions and evidence paths. Every failure classified: regression, pre-existing, environment, external access, flaky, integration, or decision. Do not present an unrun command as passed.
6. Publish your session report, machine-readable F/VR ledger, setup/rollback instructions, OpenAPI/fixtures where applicable, and exact non-owned integration proposals in `docs/agent-program/<aN>/session-<1..5>/`. Each report self-contained; no 'see previous session' as a prerequisite. Feature states: VERIFIED IMPLEMENTED, PARTIAL, NOT IMPLEMENTED, NEEDS INTEGRATION, NEEDS EXTERNAL VALIDATION, VERIFIED INTENTIONAL, SUPERSEDED WITH EVIDENCE. Risk states: CONTROL VERIFIED, PARTIAL CONTROL, OPEN, EXTERNAL VALIDATION REQUIRED, ACCEPTED BY NAMED AUTHORITY. Document sub-capabilities separately so a mock-only half cannot close a bundled F row.
7. Check changed files against ownership and preserve unrelated work. Continue to next assigned session automatically. At final close deliver 30 F dispositions and ten VR dispositions, all evidence links, defects fixed, pending integrations/external decisions and release verdict. Do not invent completion to satisfy a 25-session target. Session time budgets are not evidence that all enterprise products can ship in 25 turns.

### Definition of done and speed

A callable authenticated implementation, real persistence where required, negative tests, reproducible build/package, documented limits and evidence at the intended integration seam are necessary for VERIFIED IMPLEMENTED. 'Code exists', a TODO, a static screen, a fake success response or a passing mock are insufficient. Unsupported components should be disabled honestly while the rest works. Preserve already-implemented F022/F024/F048/F080 and the no-forced-fallback F084 disposition.

Optimize measured bottlenecks: cold/warm split, p50/p95/p99, model time versus tool dispatch/network/queue, tokens/cost and task accuracy/recovery. Warm isolated browsers, compact observations, event-driven waits, bounded concurrency, safe tenant caches and connection reuse are appropriate. Never achieve speed by dropping approval, auth, isolation, verification or the selected model. 'Lightning fast' and 'pixel accurate' are goals to measure, not marketing claims to assert.

## Your identity and jurisdiction

You are **A2**, responsible for **Execution, Context, Memory and Multi-Agent Runtime**. Your exclusive existing code: `section4/**, section6/**, section11/**`. Your new code: `packages/agent-runtime/**`. Your reports: `docs/agent-program/a2/**`. No other path is writable under this assignment.

## Your ten original voice-session future risks

These are all assigned to you as primary owner; retain the exact numbers. A risk may have cross-cutting consequences, but your deliverable must be independently useful in your jurisdiction.

### VR005 — Repository indexing and context selection miss important files or decisions.

Review assessment: **PARTIAL**. Remaining evidence/control: Retrieved context does not reach execution as an explicit bundle.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR006 — Long sessions lose context or produce inconsistent handoffs.

Review assessment: **PARTIAL**. Remaining evidence/control: Events persist; cross-session decision/context quality unmeasured.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR009 — Agents lack safe branch/worktree isolation for concurrent work.

Review assessment: **PARTIAL**. Remaining evidence/control: Compose shares DSH_WORKSPACE; no per-run worktree in live wrapper.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR016 — A compromised or malicious model provider returns instructions that bypass the intended workflow.

Review assessment: **PARTIAL**. Remaining evidence/control: Approval gates present; live tool boundary remains unverified.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR021 — Provider outages, rate limits, or model quotas stop customer runs.

Review assessment: **CONTROLS PRESENT**. Remaining evidence/control: Timeout/circuit breaker/provider failures tested; provider outage behavior still ongoing risk.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR022 — Model updates silently change behavior and invalidate plans, tests, or review results.

Review assessment: **PARTIAL**. Remaining evidence/control: SDK pin/contracts exist; model quality/version benchmark missing.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR023 — Long-running runs fail after consuming tokens without a usable resume point.

Review assessment: **PARTIAL**. Remaining evidence/control: Session IDs/queue exist; kill/restart mid-tool recovery not established.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR024 — Queue, concurrency, or cancellation bugs cause starvation, duplicate work, or runaway spend.

Review assessment: **CONTROLS PRESENT**. Remaining evidence/control: Queue/heartbeat/replay tests pass; non-killable threads remain residual.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR025 — Multi-replica deployment breaks sessions, WebSockets, OAuth state, rate limits, or event ordering.

Review assessment: **PARTIAL**. Remaining evidence/control: PG delivery/claims tested; S5 registry and live workspace isolation limit horizontal operation.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR029 — Different model, prompt, contract, or dependency versions make results irreproducible.

Review assessment: **CONTROLS PRESENT**. Remaining evidence/control: Frozen contracts, SDK pin, specs and mirror drift checks present.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

## Your five independent sessions

Execute all five. Each can also be restarted or selected by its exact identifier without the other sessions having run. The common rules and all assigned risks above remain binding in every session.

## A2-S1 — Repository intelligence and grounded planning

### Assigned scope — six original feature rows

- **F011 — Incremental semantic codebase index**. Review: PARTIAL. Evidence/remaining work: Section11 embeddings/chunking/search; freshness and live deployment not proven.
- **F012 — Git-history-aware retrieval**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F013 — Cross-repository search**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F014 — Dependency graph visualization**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F015 — Symbol and call-graph navigation**. Review: PARTIAL. Evidence/remaining work: AST-aware chunking is not a full symbol/call graph.
- **F016 — Automatic context ranking**. Review: PARTIAL. Evidence/remaining work: Retrieval scoring/budgeting exists; execution receives only plan.goal.

### Implementation assignment

Implement incremental content-addressed indexing, git-history retrieval, authorized cross-repo search, dependency graph data and symbol/caller navigation, then ranked task context. Graph visualization output must be independently renderable in your own optional page/report; do not edit Agent4 UI. Enforce project/team access during retrieval, ingestion and deletion; avoid hashing secrets into shared caches.

### Acceptance evidence

Use deterministic multi-language fixture repos with edits, renames, deletions, ignored/forbidden files and dependency edges. Measure retrieval recall/precision and freshness on known queries; deny cross-team search. Do not conflate AST chunking with a full accurate call graph.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a2/session-1/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A2-S2 — Execution context, memory, modes and faithful replay

### Assigned scope — six original feature rows

- **F017 — Context-quality diagnostics**. Review: PARTIAL. Evidence/remaining work: Context paths/token drops stored on runs; no complete quality diagnostic surface.
- **F018 — Persistent project memory**. Review: PARTIAL. Evidence/remaining work: Project rules, briefs and events persist; rules not passed into live execution.
- **F019 — Hierarchical instruction files**. Review: PARTIAL. Evidence/remaining work: Project rules exist; no org/team/directory hierarchy.
- **F020 — Automatic documentation ingestion**. Review: PARTIAL. Evidence/remaining work: Repo context ingestion exists; no automatic wiki/ticket/ADR connectors verified.
- **F021 — Explicit Ask/Plan/Agent modes**. Review: PARTIAL. Evidence/remaining work: Plan gate and dry/live modes; no full read-only Ask/Plan/Agent permission model.
- **F111 — Run replay**. Review: PARTIAL. Evidence/remaining work: Events/rehydration/demo replay; not every prompt/tool/file observation captured.

### Implementation assignment

Close A4 of the review: pass bounded framed rules, relevant retrieved content and user goal into actual execution, with provenance and freshness. Implement context diagnostics, durable decision memory, hierarchical instruction precedence, documentation ingestion and Ask/Plan/Agent permissions. Extend replay to approved redacted prompts/tool observations/artifact references. Do not retain raw secrets for replay. Documentation integration may use explicit authenticated HTTP/folder ingestion, not another agent connector.

### Acceptance evidence

Capture exact fake-SDK input and assert retrieved rules/chunks arrive; enforce token limits, instruction hierarchy and untrusted framing. Ask mode must deny writes at runtime, not just in prompt. Replay must preserve order, clearly mark unavailable/redacted data and survive restart.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a2/session-2/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A2-S3 — Killable isolated execution, continuation and recovery

### Assigned scope — six original feature rows

- **F023 — Long-running background agents**. Review: PARTIAL. Evidence/remaining work: Durable queue/heartbeat exist; thread cancellation and restart side-effect recovery incomplete.
- **F025 — Agent handoff between local and cloud environments**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F026 — Follow-up messages to active runs**. Review: PARTIAL. Evidence/remaining work: Human edit/nudge APIs exist; not a general active-run instruction channel.
- **F027 — Pause, resume, and cancel controls**. Review: PARTIAL. Evidence/remaining work: Pause/resume/stop states tested; running SDK thread cannot be killed.
- **F028 — Automatic retry policy**. Review: PARTIAL. Evidence/remaining work: Provider retry/breaker components exist; no complete policy across all tool failures.
- **F029 — Failure recovery strategies**. Review: PARTIAL. Evidence/remaining work: Manual recovery and breaker exist; no autonomous bounded strategy switching.

### Implementation assignment

Fix A1–A3 in the execution call path: eliminate process-global credential mutation, isolate each run/project workspace, and execute SDK work in killable child process trees with explicit environment and handles. Wire all SDK tools through existing S5 authority; inspect actual pinned SDK hook support. If SDK cannot be safely intercepted/confined, fail closed for live mode and document exact unsupported capability. Implement authorized local/cloud handoff manifests, active follow-up, pause/resume/cancel and bounded retry/recovery without forced downgrade.

### Acceptance evidence

Deterministically reproduce distinct concurrent synthetic-key isolation; prove forbidden edit/network/tool denial through real SDK execution hooks; kill a long-lived child and grandchildren and verify no post-stop writes; reconcile late costs, retry idempotency and checkpoint resume. Use current baseline S5 APIs/policies, never Agent3 future output; if baseline cannot enforce required boundary, ship a fail-closed integration adapter and report NEEDS INTEGRATION rather than calling the live path fixed.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a2/session-3/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A2-S4 — Task decomposition and independent specialist agents

### Assigned scope — six original feature rows

- **F030 — Task decomposition planner**. Review: PARTIAL. Evidence/remaining work: PlanService makes steps/citations; not a delegating task-graph planner.
- **F071 — Specialized subagents**. Review: PARTIAL. Evidence/remaining work: Reviewer and meeting AI roles exist; no user-configurable specialist subagents.
- **F072 — Agent-to-agent delegation**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F073 — Shared task board**. Review: PARTIAL. Evidence/remaining work: Shared runs/review surfaces; no shared agent/human task board.
- **F074 — Agent team or swarm mode**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F075 — Independent context windows per subagent**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.

### Implementation assignment

Implement task DAG decomposition, specialized research/code/test/review agents, explicit delegation/result contracts, shared human/agent task-board API and an optional independently hosted view, coordinator/worker mode and independent context windows. Treat delegation and agent output as untrusted. This product capability does not authorize spawning extra coding assistants during your implementation run.

### Acceptance evidence

Test DAG cycle detection, dependency readiness, duplicate claims, isolated contexts, bounded fan-out, delegated scope restriction, failure propagation and non-resurrection of stopped runs. Use deterministic fake workers and existing baseline tools. No reliance on another lane registry or browser worker.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a2/session-4/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A2-S5 — Per-agent policies, synthesis and fair scheduling

### Assigned scope — six original feature rows

- **F076 — Per-subagent model selection**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F077 — Per-subagent tool permissions**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F078 — Subagent result synthesis**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F079 — Human assignment and escalation queue**. Review: PARTIAL. Evidence/remaining work: BLOCKED state and human controls exist; no assignment/escalation work queue.
- **F080 — Agent workload, queue, and concurrency controls**. Review: IMPLEMENTED. Evidence/remaining work: run_queue.py, worker.py and EngineRuntime enforce queue/concurrency policies.
- **F024 — Parallel agent jobs**. Review: IMPLEMENTED. Evidence/remaining work: Durable queue supports independent concurrent runs with bounded executor; credential/workspace caveats below.

### Implementation assignment

Implement explicit per-agent model and tool scope policies, result synthesis with conflicting evidence surfaced, human assignment/escalation and fair workload/concurrency controls. Preserve existing parallel queue guarantees F024/F080 and strengthen multi-tenant limits. Exercise scheduling and synthesis with standalone fixture tasks if Session4 work does not exist; implement any required prerequisite within this session.

### Acceptance evidence

Test non-amplification of child capabilities, chosen-model preservation, conflicting modifications, starvation, team quotas, cancellation under saturated executor/worker pools, crash reclaims and no duplicate external effects. Distinguish logical task exactly-once claims from at-least-once external side effects.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a2/session-5/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## Final action

Begin A2-S1 now. Complete S1 through S5 without unnecessary confirmation. If invoked later for a particular session, execute that session independently using this entire prompt as context. Finish with the 30-feature/ten-risk evidence ledger and exact release/integration limits.
