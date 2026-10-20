# A1 — Security, Identity, Models and Commercial Governance

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

You are **A1**, responsible for **Security, Identity, Models and Commercial Governance**. Your exclusive existing code: `section1/**, section3/**, contracts/**`. Your new code: `packages/security-policy/**`. Your reports: `docs/agent-program/a1/**`. No other path is writable under this assignment.

## Your ten original voice-session future risks

These are all assigned to you as primary owner; retain the exact numbers. A risk may have cross-cutting consequences, but your deliverable must be independently useful in your jurisdiction.

### VR011 — Prompt injection arrives through repository files, issues, pull requests, documents, or tool output.

Review assessment: **CONTROLS PRESENT**. Remaining evidence/control: Untrusted boundary framing reduces risk; no universal prompt-injection solution.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR012 — Agents expose API keys, tokens, secrets, or proprietary source code.

Review assessment: **CONTROLS PRESENT**. Remaining evidence/control: Vault/redaction controls reduce exposure; synthetic concurrent-wrapper credential race newly reproduced.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR015 — Permissions are too broad, so a “read-only” workflow can still change files or call external systems.

Review assessment: **CONTROLS PRESENT**. Remaining evidence/control: Bearer roles/path policies present; prove every SDK tool routes through enforced capability boundary.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR017 — A cross-tenant authorization bug exposes another team’s runs, events, files, or costs.

Review assessment: **CONTROLS PRESENT**. Remaining evidence/control: Authz/isolation tests pass; no guarantee against future regressions.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR018 — Personal-key sharing creates unclear consent, ownership, billing, or revocation behavior.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Team keys are not owner-consented personal-key sharing.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR019 — OAuth, webhooks, email login, or session handling fails under real production attacks.

Review assessment: **PARTIAL**. Remaining evidence/control: Mock IdP/OAuth tests; real IdP/provider drills pending.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR041 — Model and infrastructure costs exceed subscription revenue when teams run many agents.

Review assessment: **PARTIAL**. Remaining evidence/control: Budgets/ledger exist; real unit economics not measured.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR045 — Shared credentials make it impossible to attribute spend and protect the key owner from surprise bills.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: No personal/shared-key payer consent model.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR047 — Data residency, privacy, retention, or sector compliance requirements block otherwise interested customers.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Residency/retention/compliance agreements not validated.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR048 — Provider terms, model access rules, or third-party API policies restrict the BYO-key or multi-provider model.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Provider-policy review remains an operator/legal task.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

## Your five independent sessions

Execute all five. Each can also be restarted or selected by its exact identifier without the other sessions having run. The common rules and all assigned risks above remain binding in every session.

## A1-S1 — Outbound security and enforceable capabilities

### Assigned scope — six original feature rows

- **F091 — Secret scanning before prompt or tool transmission**. Review: PARTIAL. Evidence/remaining work: Secret redaction exists; outbound prompt/tool secret scanner not established.
- **F092 — Prompt-injection detection and quarantine**. Review: PARTIAL. Evidence/remaining work: Untrusted-input framing exists; no general injection detector/quarantine guarantee.
- **F093 — Sensitive-file policy**. Review: PARTIAL. Evidence/remaining work: S5 path policy/forbidden_paths exist; SDK execution enforcement gap needs proof.
- **F094 — Data-loss prevention rules**. Review: PARTIAL. Evidence/remaining work: Redaction and network policy components; no general outbound content DLP.
- **F095 — Fine-grained tool capability tokens**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F096 — Per-run network egress policy**. Review: PARTIAL. Evidence/remaining work: S5 egress controls exist; per-run live SDK egress enforcement unproven.

### Implementation assignment

Implement pre-transmission secret scanning and sensitive-data policy with redact/deny outcomes; injection suspicion quarantine as a bounded defense, never a universal detector; short-lived audience/run/action-bound capability grants; egress/DLP policy decision API. Keep secret material in Section1/3. Build a policy simulator and recorded denials using existing auth. Enforce at the boundaries you own; provide an authenticated standalone evaluator for other lanes. Do not claim S5/SDK enforcement from the existence of a policy service.

### Acceptance evidence

Exercise expired/replayed/wrong-audience grants, forged team/run identity, secret-bearing prompts, malicious tool text, private/link-local URLs and redaction. Verify absence of raw secrets in DB logs, exceptions, fixtures and models.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a1/session-1/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A1-S2 — Personal keys, payer consent and bounded spend

### Assigned scope — six original feature rows

- **F081 — Personal BYO-key profiles**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F082 — Consent-based key sharing**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F087 — Token-budget prediction before execution**. Review: PARTIAL. Evidence/remaining work: Estimated plan cost and context limits exist; not a calibrated token forecast.
- **F088 — Spend anomaly detection**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F143 — Usage-based billing**. Review: PARTIAL. Evidence/remaining work: Ledger/tiers/Stripe code exist; real usage billing reconciliation not drilled.
- **F144 — Budget alerts and automatic spend ceilings**. Review: PARTIAL. Evidence/remaining work: Hard budget gate + warning threshold/API; delivery UX/live billing limits incomplete.

### Implementation assignment

Implement individually owned BYO-key profiles, explicit recipient consent, revocation and payer attribution, budget prediction with uncertainty, anomaly signals and atomic reservations/reconciliation. Preserve team keys and existing tiers. Complete billing paths within the identity/model services; unknown provider usage must remain unsettled/unknown rather than fabricated zero. Hard ceiling must account for in-flight reservations and late usage.

### Acceptance evidence

Test simultaneous reservations, retry idempotency, owner revocation during queued/running work, recipient access denial, late usage, stale estimates, webhook replay and signed billing events. Use fake billing providers locally; identify live test-mode drill separately.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a1/session-2/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A1-S3 — Approved model routing and measurable efficiency

### Assigned scope — six original feature rows

- **F083 — Per-task model routing**. Review: PARTIAL. Evidence/remaining work: PRIMARY/REVIEWER models and explicit model params exist; no adaptive task router.
- **F084 — Automatic fallback across providers**. Review: INTENTIONALLY EXCLUDED. Evidence/remaining work: No forced model downgrade per AGENTS.md. Provider/model fallback requires explicit approved policy; do not count as mandatory debt.
- **F085 — Model quality and cost comparison for the same task**. Review: PARTIAL. Evidence/remaining work: Spend/latency history exists; no controlled same-task quality comparison.
- **F086 — Prompt or context caching controls**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F089 — Organization model allowlist with approval workflow**. Review: PARTIAL. Evidence/remaining work: Catalog/roles/provider config exist; no org allowlist approval workflow verified.
- **F090 — Local-model discovery and health monitoring**. Review: PARTIAL. Evidence/remaining work: Arbitrary compatible endpoint config; no first-class local-model discovery.

### Implementation assignment

Implement explicit per-task routing policies, admin model allowlists, opt-in provider fallback, same-task quality/cost comparisons, tenant-isolated caching controls and local compatible-endpoint discovery/health. F084 is intentionally excluded as automatic forced downgrade: maintain that prohibition and only implement policy-authorized equivalent-provider fallback with explicit attribution. Treat it as VERIFIED INTENTIONAL unless product policy explicitly permits the optional alternative.

### Acceptance evidence

Test requested-model preservation, unsupported/disallowed models, missing permissions, outage with no opt-in, consented fallback attribution, cache cross-tenant denial, TTL/invalidation and endpoint abuse. Measure real latency/cost only when available; report synthetic benchmark separately.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a1/session-3/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A1-S4 — Supply-chain trust, enterprise provisioning and lifecycle policy

### Assigned scope — six original feature rows

- **F097 — Signed agent or plugin packages**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F098 — Security event correlation**. Review: PARTIAL. Evidence/remaining work: Run/audit/request IDs exist; no security correlation engine.
- **F099 — Automated dependency vulnerability remediation**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F100 — Policy simulation mode**. Review: PARTIAL. Evidence/remaining work: Dry mode is not a full policy permission simulator.
- **F145 — SSO and SCIM provisioning**. Review: PARTIAL. Evidence/remaining work: OIDC/PKCE + SPA callback exist; SCIM not found, live IdP drill pending.
- **F147 — Custom retention and deletion policies**. Review: PARTIAL. Evidence/remaining work: Retention/deletion migrations and runbooks; no per-project policy console.

### Implementation assignment

Implement signed-package verification and revocation/allowlisting, correlated security events, dependency-remediation proposals gated by tests/manual review, safe policy simulation, SCIM provisioning/deprovisioning alongside existing OIDC, and retention/deletion policy APIs. Signatures must bind package bytes/version/issuer; do not call an unsigned catalog trusted. Use new numbered migrations only; recheck next free number before allocating.

### Acceptance evidence

Test bad/revoked signatures, package substitution, SCIM role escalation/cross-tenant updates, last-admin protection, offboarding token invalidation, retention legal-hold conflicts and deletion/restore semantics. Real IdP integration needs an actual drill, not merely mock PKCE.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a1/session-4/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A1-S5 — Self-service governance and customer evidence

### Assigned scope — six original feature rows

- **F022 — Plan approval gate**. Review: IMPLEMENTED. Evidence/remaining work: Mandatory approval state machine, service/API gate and expiry tests.
- **F141 — Self-serve workspace creation**. Review: PARTIAL. Evidence/remaining work: OTP/team/project onboarding exists; complete self-serve lifecycle not drilled.
- **F142 — Trial-to-paid conversion flow**. Review: PARTIAL. Evidence/remaining work: Billing UI/Stripe adapter exist; live checkout not drilled.
- **F146 — Organization-level admin console**. Review: PARTIAL. Evidence/remaining work: Team settings/membership/billing pages; no distinct org admin console.
- **F148 — Data residency controls**. Review: PARTIAL. Evidence/remaining work: Self-host placement enables operator choice; no enforced residency router.
- **F149 — Compliance evidence center**. Review: PARTIAL. Evidence/remaining work: Audit surfaces/runbooks; no compliance evidence-center product.

### Implementation assignment

Regression-protect mandatory approval and expiry; complete safe workspace onboarding, trial/paid transitions, organization administration, data-residency policy and a machine-verifiable compliance evidence package. Enforce residency on provider/tool choices you own; no cosmetic-only dropdown. F022 is preservation/negative regression coverage, not a rewrite. Prepare provider-policy/privacy decisions for human review without claiming counsel approval.

### Acceptance evidence

Verify approval cannot be bypassed, stale approval rejected, privilege escalation denied, unpaid/expired tier behavior honest, organization ownership enforced, residency denied on noncompliant target and evidence exports redacted. Record unresolved legal/real-provider prerequisites explicitly.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a1/session-5/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## Final action

Begin A1-S1 now. Complete S1 through S5 without unnecessary confirmation. If invoked later for a particular session, execute that session independently using this entire prompt as context. Finish with the 30-feature/ten-risk evidence ledger and exact release/integration limits.
