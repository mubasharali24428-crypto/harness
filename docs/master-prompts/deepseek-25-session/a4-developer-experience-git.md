# A4 — Developer Experience, Editors, Git Workflow and Adoption

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

You are **A4**, responsible for **Developer Experience, Editors, Git Workflow and Adoption**. Your exclusive existing code: `section2/**, section7/**`. Your new code: `integrations/editors/**, apps/desktop/**, packages/dev-cli/**`. Your reports: `docs/agent-program/a4/**`. No other path is writable under this assignment.

## Your ten original voice-session future risks

These are all assigned to you as primary owner; retain the exact numbers. A risk may have cross-cutting consequences, but your deliverable must be independently useful in your jurisdiction.

### VR001 — The core workflow remains too complex for a first-time team.

Review assessment: **PARTIAL**. Remaining evidence/control: Guided surfaces exist; run an unassisted first-run usability study.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR002 — A new team cannot reach its first useful run in five minutes.

Review assessment: **PARTIAL**. Remaining evidence/control: Time-to-first-useful-change not measured with real developers.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR003 — The CLI is missing or too awkward, so developers avoid the product.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: SDK/MCP are not a polished task CLI.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR004 — There is no strong IDE/editor integration where developers already work.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: No native IDE extensions found.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR008 — Review and pull-request workflows feel bolted on rather than native.

Review assessment: **PARTIAL**. Remaining evidence/control: PR APIs exist; live end-to-end repo/provider journey remains.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR010 — The product handles coding runs but not adjacent planning, testing, documentation, or incident workflows.

Review assessment: **PARTIAL**. Remaining evidence/control: Meetings/review exist; generalized planning/testing/incident agent workflows remain.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR032 — Developers distrust the system and bypass it with direct Claude, Cursor, Codex, or local-agent usage.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Need adoption and bypass-rate evidence from users.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR033 — Engineering managers do not want another dashboard or approval ceremony.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Need measured approval overhead and pilot feedback.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR034 — The team uses the Harness only for demos and not for recurring production work.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: No longitudinal recurring-use evidence.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

### VR049 — Usage falls after the initial novelty period because the team has no recurring trigger to return.

Review assessment: **OPEN / UNVALIDATED**. Remaining evidence/control: Retention/recurring workflow evidence needs live pilot.

For this risk supply a concrete control or validation instrument, negative/operational test, monitoring signal, residual exposure, accountable owner and next decision. Include it in every relevant session; at session 5 reconcile all ten.

## Your five independent sessions

Execute all five. Each can also be restarted or selected by its exact identifier without the other sessions having run. The common rules and all assigned risks above remain binding in every session.

## A4-S1 — Editable code surface and granular review

### Assigned scope — six original feature rows

- **F004 — Browser-based IDE**. Review: PARTIAL. Evidence/remaining work: CodeMirror manual edits and tree API exist; no complete browser IDE.
- **F007 — Inline diff review**. Review: PARTIAL. Evidence/remaining work: DiffPanel.tsx explicitly read-only; comments elsewhere are not per-hunk decisions.
- **F008 — Inline code actions**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F009 — Composer-style multi-file editing**. Review: PARTIAL. Evidence/remaining work: SDK may edit multiple files; coordinated per-file/hunk curation not present.
- **F010 — Agent chat anchored to code lines**. Review: PARTIAL. Evidence/remaining work: Comments exist; no verified symbol/range-anchored chat.
- **F040 — Automatic merge checks**. Review: PARTIAL. Evidence/remaining work: Manual approval/review/no-merge laws; external branch protection remains an operator action.

### Implementation assignment

Extend the existing browser workspace with repository browsing/editing, per-hunk accept/reject/comment, selection-based code actions, multi-file change review and file/line-anchored discussion. Preserve explicit plan approval and manual merges; merge checks are evidence gates, not auto-merge. Use baseline S2/S5 APIs only. If a required write/restore API is absent, implement a separately hosted lane-owned HTTP adapter using baseline authorized operations where possible; fail closed where impossible and publish the missing integration honestly.

### Acceptance evidence

Test stale base hashes, overlapping hunks, rejected-hunk integrity, traversal/forbidden edits, permissions, unsaved work and review invariants. Show before/after screenshots and keyboard behavior with actual UI; do not fake success when backend denies/unavailable.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a4/session-1/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A4-S2 — Isolated Git work and reviewed pull requests

### Assigned scope — six original feature rows

- **F031 — Automatic branch creation per task**. Review: PARTIAL. Evidence/remaining work: GitHub createBranch API exists; automatic isolated branch per agent not established.
- **F032 — Git worktree isolation per agent**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F033 — Automatic commit drafting**. Review: PARTIAL. Evidence/remaining work: GitHub commit primitives exist; explainable automatic commit drafting unverified.
- **F034 — Pull-request creation from a completed run**. Review: PARTIAL. Evidence/remaining work: Draft-PR API/event linkage exists; live provider and complete run-to-PR drill pending.
- **F035 — Pull-request description generation**. Review: PARTIAL. Evidence/remaining work: PR title/body supplied to API; end-to-end generated evidence-rich description unverified.
- **F036 — Review-request routing**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.

### Implementation assignment

Implement per-run branches/worktrees, explainable commit drafting, governed draft-PR creation/descriptions and ownership-based review routing. Worktrees must remain within S5-managed execution boundaries; S2 remote Git APIs cannot grant unconstrained host shell access. New S2 adapters use existing S5 API; do not import/edit Section5. Provide scoped integration proposals for enforcement missing at baseline.

### Acceptance evidence

Use disposable local Git fixtures and a fake GitHub server to verify worktree isolation, clean/dirty recovery, commit attribution, CODEOWNERS matching and idempotent draft PRs. External push/PR side effects require explicit user authorization for that test repository; never use a customer repo silently. Confirm no merge route/path exists.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a4/session-2/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A4-S3 — IDE entry points, desktop shell and issue intake

### Assigned scope — six original feature rows

- **F001 — Native VS Code extension**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F002 — JetBrains extension**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F003 — Neovim integration**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F006 — Desktop app**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F037 — GitHub issue-to-agent assignment**. Review: PARTIAL. Evidence/remaining work: GitHub webhook paths exist; issue-to-agent assignment workflow not verified.
- **F038 — GitLab issue and merge-request integration**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.

### Implementation assignment

Build thin VS Code, JetBrains and Neovim integrations plus a desktop shell that reuse real authenticated baseline APIs and expose status/approval/review workflows. Implement GitHub issue intake and GitLab issue/MR adapter using S2-owned service modules. Prefer small functional clients over copied full IDEs. Package and verify each target; source scaffolding without installable behavior remains partial.

### Acceptance evidence

Verify auth storage/redaction, repository/team selection, actual command invocation and clear unsupported states. Run extension package/build/load smoke for available hosts; use protocol tests for unavailable IDEs but mark host validation blocked. GitLab must have signed/idempotent webhooks and tenant ownership.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a4/session-3/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A4-S4 — Mobile access, remaining Git providers and first use

### Assigned scope — six original feature rows

- **F005 — Mobile task inbox**. Review: PARTIAL. Evidence/remaining work: Responsive web surfaces; no dedicated mobile task product.
- **F039 — Bitbucket and Azure DevOps integration**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F131 — Five-minute guided onboarding**. Review: PARTIAL. Evidence/remaining work: Tutorial/onboarding/checklist exist; unassisted five-minute success not measured.
- **F132 — Repository readiness check**. Review: PARTIAL. Evidence/remaining work: Onboarding checks exist; not a full repo-fit/test/dependency assessment.
- **F133 — Interactive tutorial project**. Review: PARTIAL. Evidence/remaining work: Demo fixture/repo/journey exist; no verified interactive teaching product.
- **F134 — Task templates**. Review: PARTIAL. Evidence/remaining work: Demo/sample brief exists; no user task-template library.

### Implementation assignment

Implement accessible mobile task inbox, Bitbucket/Azure DevOps repository adapters, guided first-run flow, repo readiness diagnosis, tutorial project and task templates. Ship a usable task CLI in packages/dev-cli to address VR003 independently of other lanes. Use existing provider/auth APIs or fail closed with honest configuration state. Do not imply a tutorial proves real users can reach a useful change in five minutes.

### Acceptance evidence

Test narrow viewports/touch and read-only roles, OAuth callback errors, provider incompatibility, onboarding empty/error states, fixture repo readiness checks, tutorial reset and bug/test/docs/refactor templates. Record timed unassisted pilot protocol separately; real participant data cannot be fabricated.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a4/session-4/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## A4-S5 — Workflow library, accessibility and recurring use

### Assigned scope — six original feature rows

- **F135 — Saved prompt and workflow library**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F136 — User-configurable keyboard shortcuts and command palette**. Review: PARTIAL. Evidence/remaining work: Some keyboard/focus helpers; no user-configurable shortcut/command palette product.
- **F137 — Accessibility support**. Review: PARTIAL. Evidence/remaining work: Labels/focus/responsive tests exist; full screen-reader/keyboard audit not done.
- **F138 — Internationalization and timezone-aware scheduling**. Review: NOT FOUND. Evidence/remaining work: No first-class implementation located in reviewed source/API/UI/config inventory.
- **F139 — Public status page and incident communication workflow**. Review: PARTIAL. Evidence/remaining work: Health/ready/metrics endpoints; no public incident status communications product.
- **F140 — In-product feedback capture linked to the exact run and change**. Review: PARTIAL. Evidence/remaining work: Comments/nudges exist; no product-feedback-to-exact-change flow.

### Implementation assignment

Implement versioned prompt/workflow library, configurable keyboard shortcuts/command palette, keyboard/screen-reader support, internationalization/timezone-safe scheduling UI, public status/incident view and run-linked feedback. Use baseline events/health APIs and lane-owned persistence adapters where needed. Build frontend scheduling UX with an independent fixture/adapter; do not assume Agent5 scheduler exists.

### Acceptance evidence

Run keyboard focus/escape/dialog flows, accessible names, contrast/responsive checks, DST/UTC/timezone round-trips, concurrent workflow versions and feedback-to-exact-run linkage. Status must disclose unavailable upstream checks; no hardcoded healthy badge. Collect actual adoption evidence only through an authorized pilot.

### Independence and closure

Start with baseline + this session specification. Do not require any other session/agent output. Reconstruct needed fixtures/helpers within your ownership, use existing authenticated services or independently runnable adapters, and fail closed for unavailable safety prerequisites. Produce a real working slice rather than a facade. Publish exactly six F dispositions plus relevant VR dispositions at `docs/agent-program/a4/session-5/`. Explicitly distinguish local functional completion from missing peer integration/live provider/OS/user validation. Continue autonomous safe work after recording blockers.

## Final action

Begin A4-S1 now. Complete S1 through S5 without unnecessary confirmation. If invoked later for a particular session, execute that session independently using this entire prompt as context. Finish with the 30-feature/ten-risk evidence ledger and exact release/integration limits.
