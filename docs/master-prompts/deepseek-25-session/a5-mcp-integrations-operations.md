# A5 — MCP, Integrations, Observability, Operations and Pilot Evidence

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

You are **A5**, responsible for **MCP, Integrations, Observability, Operations and Pilot Evidence**. Your exclusive existing code: `mcp/**, clients/**, section8/**, section10/**, deploy/**, .github/**, Makefile, .env.example, .gitignore, scripts/sdk-gen/**, scripts/loadtest/**, scripts/e2e/**`. Your new code: `packages/extensions/**, integrations/connectors/**, packages/telemetry/**, scripts/agent-program/a5/**`. Your reports: `docs/agent-program/a5/**`. No other path is writable under this assignment.

## Your ten original voice-session future risks

These are all assigned to you as primary owner; retain the exact numbers. A risk may have cross-cutting consequences, but your deliverable must be independently useful in your jurisdiction.

### VR014 — Malicious MCP servers, skills, plugins, or dependencies enter through the agent supply chain.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: No external MCP/package trust registry; future exposure needs scope/signatures.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR020 — The product cannot produce sufficient security evidence for a customer’s review, procurement, or insurance requirements.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: No customer procurement/evidence package validated.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR031 — Buyers cannot see a measurable return on investment beyond “the agent feels useful.”

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: No measured accepted-change productivity ROI from real pilot.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR036 — GitHub, Anthropic, OpenAI, Google, or Microsoft bundle similar governance into tools customers already pay for.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Competitive risk cannot close through code changes.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR037 — The category is too crowded and the phrase “AI engineering harness” is not understood by buyers.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Positioning needs buyer validation.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR038 — Enterprise security and procurement reviews take longer than the product’s sales runway.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Sales/procurement cycle unmeasured.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR040 — Documentation, examples, support, and community are too thin to create independent adoption.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Runbooks exist; customer support intake/SLA/community not validated.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR042 — Pricing is guessed, underpriced, or too complicated to explain.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Pricing is a hypothesis without paid-customer validation.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR044 — Teams value governance but are not willing to pay for it separately from their existing agent tools.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: No willingness-to-pay evidence.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR050 — The best features are copied or absorbed by larger platforms before the Harness establishes distribution, workflow data, or customer trust.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Distribution and customer trust are market risks, not fixable checklist bugs.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

## Your five independent sessions

Execute all five. Each can also be restarted or selected by its exact identifier without the other sessions having run. The common rules and all assigned risks above remain binding in every session.

## A5-S1 — External MCP and trusted extensibility

### Assigned scope — six original feature rows

- **F061 — First-class MCP server registry**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F062 — MCP permission scopes**. Review: PARTIAL. Evidence/remaining work: MCP server inherits bearer roles/team checks; no external-tool per-run scopes.
- **F063 — MCP server health checks**. Review: PARTIAL. Evidence/remaining work: Own MCP server has tools; no external registry discovery/health controller.
- **F064 — Custom tool builder**. Review: PARTIAL. Evidence/remaining work: backend_runner extension seam; not a user-configurable tool builder.
- **F065 — Reusable skills or playbooks**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F066 — Plugin marketplace**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.

### Implementation assignment

Implement external MCP client/registry lifecycle, scoped permissions, capability/health discovery, custom tool wrapping, versioned skills/playbooks and a curated plugin catalog/marketplace minimum. Separate server (existing) from client (new); mcp/client.py currently wraps Harness REST, not external MCP. Apply deny-by-default tool scopes and package trust using baseline primitives; do not rely on Agent1 future policy service.

### Acceptance evidence

Test malicious discovery data, oversized/recursive schemas, expired scopes, cross-team tool use, dead servers, timeouts, revoked packages and registry restart. A static plugin list is not a functioning install/update/remove marketplace; if publishing/payments unavailable, report that dimension separately.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a5/session-1/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A5-S2 — Commands, hooks, tool configuration and SDK delivery

### Assigned scope — six original feature rows

- **F067 — Namespaced slash commands or task commands**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F068 — Event hooks**. Review: PARTIAL. Evidence/remaining work: Lifecycle/audit hooks exist; no comprehensive tool-call/edit/compaction hook interface.
- **F069 — Tool allowlists and denylists**. Review: PARTIAL. Evidence/remaining work: S5 policy and egress controls exist; command allowlist largely code-defined.
- **F070 — Extension SDK**. Review: PARTIAL. Evidence/remaining work: Python/TS REST SDKs and backend_runner seam; no full extension SDK.
- **F125 — Notion or internal-wiki context integration**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F150 — Customer-facing API documentation and SDK distribution**. Review: PARTIAL. Evidence/remaining work: OpenAPI specs + Python/TS clients exist; distribution/compatibility on customer installs unverified.

### Implementation assignment

Implement namespaced commands, before/after tool/edit/lifecycle hooks, configurable restrictions, extension SDK, explicit wiki/Notion ingestion connector and documented Python/TypeScript SDK distribution workflow. Hooks must not grant capabilities or mutate frozen event semantics. Restrictions may narrow baseline S5 permissions; expanding S5 authority requires its owner integration and must fail closed meanwhile.

### Acceptance evidence

Test hook recursion/timeout/exception isolation, command namespace collisions, least privilege, malformed input and SDK install in clean temporary environments. Keep provider credentials secure and external service calls consented. Mock contracts validate transport only; an untested real integration stays unvalidated.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a5/session-2/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A5-S3 — Team channels and incident-to-agent connectors

### Assigned scope — six original feature rows

- **F121 — Slack task intake and status updates**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F122 — Microsoft Teams task intake and status updates**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F123 — Linear issue integration**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F124 — Jira issue integration**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F126 — Sentry error-to-agent workflow**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F127 — Datadog or Grafana incident-to-agent workflow**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.

### Implementation assignment

Implement Slack, Teams, Linear, Jira, Sentry and Datadog/Grafana intake/status workflows with signed inbound webhooks, tenant mapping, idempotency and approved plan creation. Run self-contained connector services against existing baseline plan/run APIs; incident text is untrusted context and never authorizes execution.

### Acceptance evidence

Test signature tampering, replay, tenant spoofing, duplicate incident delivery, rate limiting, secret-bearing payloads, upstream outages and no automatic execution before human approval. Use local fake-provider suites; do not send real messages/contact others or install external apps without explicit authorization.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a5/session-3/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A5-S4 — Tracing and measurable developer value

### Assigned scope — six original feature rows

- **F112 — OpenTelemetry export**. Review: PARTIAL. Evidence/remaining work: JSON logs/Prometheus/request IDs exist; full OTel export not located.
- **F113 — Agent quality dashboard**. Review: PARTIAL. Evidence/remaining work: Spend/run dashboards exist; quality acceptance/rework dashboard absent.
- **F114 — Developer productivity dashboard**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F115 — Cost-per-task and cost-per-accepted-change metrics**. Review: PARTIAL. Evidence/remaining work: Per-run metering exists; accepted-change denominator missing.
- **F116 — Time-to-first-useful-change metric**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F117 — Searchable organization-wide run archive**. Review: PARTIAL. Evidence/remaining work: Team-scoped run history and retention; complete searchable org archive not verified.

### Implementation assignment

Implement OTel export, quality/productivity dashboards, cost-per-task/accepted-change, time-to-first-useful-change and searchable authorized run archive. You own telemetry/review/onboarding/backend factories; expose new independently hosted dashboards under packages/telemetry or Section10, never edit Agent4 frontend. Distinguish a measured accepted outcome from a model claiming completion.

### Acceptance evidence

Use known event fixtures with failures/rework/abandonment, trace propagation tests, PII scrubbing, archive ownership/retention and transparent metric denominators. Draft pilot measurement instruments and privacy-conscious opt-in; no invented ROI, productivity uplift, willingness to pay or retention results.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a5/session-4/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A5-S5 — Evidence, alerts, scheduling and operational acceptance

### Assigned scope — six original feature rows

- **F118 — Exportable audit evidence package**. Review: PARTIAL. Evidence/remaining work: Audit GET/event history exist; no packaged change/incident export.
- **F119 — Alerts for stalled, risky, expensive, or policy-violating runs**. Review: PARTIAL. Evidence/remaining work: Budget warnings/reaper/notifications exist; no complete actionable risk alert pipeline.
- **F120 — Usage APIs and webhooks**. Review: PARTIAL. Evidence/remaining work: Spend API/notifications exist; no comprehensive finance/security usage webhook API.
- **F128 — CI provider integrations**. Review: PARTIAL. Evidence/remaining work: Project GitHub Actions gates exist; no multi-provider customer CI integration.
- **F129 — Chat notifications with approve, reject, and inspect actions**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F130 — Calendar or scheduled maintenance-task integration**. Review: PARTIAL. Evidence/remaining work: Operator cron/maintenance profiles; no integrated calendar/scheduled task product.

### Implementation assignment

Implement redacted evidence exports, actionable stall/security/spend alerts, usage/webhook API, customer CI adapters, chat approval interactions and scheduled maintenance/task triggers. Wire and test only your lane production assembly; other lanes activation needs explicit combined verification. Add upgrade/rollback/runbook/incident/support procedures and integration-check tooling that evaluates available lane artifacts without requiring them for this session.

### Acceptance evidence

Test export completeness/redaction, webhook retry/idempotency, alert dedupe, signed approval binding/expiry, DST schedule behavior, downtime misfires and plan-approval requirement for scheduled work. Prepare external billing/IdP/backup/model drill checklists and truthful release blockers; no deployment, auto-merge, unsolicited outreach or invented customer acceptance.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a5/session-5/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## Final action

Begin A5-S1 now. Complete S1 through S5 without unnecessary confirmation. If invoked later for a particular session, execute that session independently using this entire prompt as context. Finish with the 30-feature/ten-risk evidence ledger and exact release/integration limits.
