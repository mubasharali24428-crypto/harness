# Harness review — October 17, 2026

Reviewed commit: `31bf407`. Repository initially clean. 289 commits total; 22 dated October 17 in America/Los_Angeles. Only these review artifacts were added; no application code, contracts, secrets, commits, pushes, or deployment settings changed.

## Verdict

Strong tested governance/control platform; incomplete general-purpose autonomous agent. Suitable to prepare a tightly scoped non-production pilot after resolving the engine execution/isolation findings below. Not enough evidence for an unrestricted production-ready or lightning-fast claim. Broad repository review and targeted reproduction, not exhaustive penetration testing or a live external-provider certification.

The retrieved voice-session notes explicitly say the ten complaints are a synthesis, NOT a universal statistically ranked top ten. Their 150 competitive capability entries also contain obvious false negatives (for example mandatory plan approval). Do not interpret 150 as 150 verified bugs.

## Four distinct inventories

1. Voice-session complaints: **2 strongly addressed, 6 partially addressed, 2 outcomes unproven** (8/10 addressed to some degree).
2. Voice-session 150 capability wishlist: **{'NOT FOUND': 54, 'PARTIAL': 91, 'IMPLEMENTED': 4, 'INTENTIONALLY EXCLUDED': 1}**. A partial feature counts as remaining work. These are conservative code-review assessments, not 150 independently executed acceptance tests. “NOT FOUND” means no first-class implementation located in the reviewed inventory. Features are not equally valuable. Most partial items need substantial integration or product work.
3. Voice-session 50 future risks: **{'PARTIAL': 17, 'OPEN / UNVALIDATED': 23, 'CONTROLS PRESENT': 10}**. Controls present means mitigated, never permanently eliminated. These risks are not the repository's R-01…R-50.
4. Repository remediation matrix: claims **177 original IDs / 174 deduplicated rows**, mixing FIXED, ACCEPTED, PARTIAL, AS-REG and LEAD. This review does not certify its arithmetic or turn those statuses into 174 fixes. The final report's two named remaining code items (MAC2-F4, OPS-20) are implemented, but newly reproduced findings below show that “zero open code issues” is not a global verdict. Approximately 69 rows carry partial/accepted/as-registered/lead language; that is a row search, not a deduplicated defect count.

## What today's work accomplished

22 commits include S1 cross-section authz/secret/KDF hardening, prompt-boundary framing, audit read/lifecycle wiring, frontend crypto build repair, redeem/ticket limits, replay terminal sealing, Landlock runtime probing, compose E2E CI gate, migration 0034 FK cascade correction, stale real-PG probe repairs, MCP <2 pin and SSO login/callback screens. The earlier voice claim that most work was uncommitted is stale: the checkout started clean at the reviewed HEAD.

## Fresh verification

- `make gate` PASS with local socket access: static gates, all eight backend lint suites, contract TS mirror, route/isolation guards, backend tests, TypeScript and frontend tests.
- Backend counts: S1 639 passed/10 skipped; S2 36; S3 95; S4 526/7 skipped; S5 181/1 skipped; S6 118; S8 88; S10 69. Total **1,752 passed / 18 skipped**.
- Frontend **507 passed**, TypeScript clean.
- Section11 separately **49 passed / 1 skipped**. Historical 50-pass reports do not apply to this environment without the optional prerequisite.
- MCP own venv missing: `mcp/.venv/bin/python` unavailable. Historical 18-pass report not freshly revalidated here.
- First sandboxed gate got 628 S1 passes plus 11 mock-IdP socket PermissionErrors; retry with socket access passed. These were environment errors, not demonstrated product failures.
- Warnings remain, including >10,000 in S4; passing is not warning-free.
- Did not execute real Stripe/IdP/object-store/model drills, live browser UI journey, full frontend production build, or real-PG migration/probe suite in this review. Previous report evidence is historical, not fresh evidence.
- `make gate` does not include Section11, MCP, live-PG, full browser E2E or production frontend build. Its green result has those limits.

## New and residual code findings

**A1 — HIGH: concurrent-wrapper process environment race.** `section4/src/harness_orchestrator/engine.py:223-242` saves, overwrites and restores DEEPSEEK_API_KEY/BASE_URL around a call. `engine_runtime.py:322-325` runs synchronous SDK calls in parallel threads. A deterministic fake-SDK two-thread test with distinct synthetic keys returned `{'B': True, 'A': False}` for correct credential observation; both runs reported completed. No real credential or provider used. The default deployed service currently shares one configured wrapper; this reproduction establishes the wrapper's failure with distinct concurrent credentials, not proof of an observed production cross-tenant leak. Before per-user keys or parallel wrappers, pass credentials explicitly or isolate each SDK process/environment. A global lock is a temporary serialization measure, not a speed architecture.

**A2 — HIGH: live SDK confinement and workspace isolation not established end to end.** Compose assigns one `DSH_WORKSPACE=/tmp/harness-dsh/workspace` (compose:44). Wrapper passes that cwd to the SDK (engine.py:214), while EngineRuntime executes it in the orchestrator thread pool. No S5 command/path-policy adapter is visible in that path. S5 tests do not prove SDK tools traverse S5. This is a verified missing enforcement link in reviewed wiring, not a demonstrated sandbox escape. Require a real-tool denial test for forbidden paths/network plus isolated project/run workspaces before autonomous concurrent work.

**A3 — HIGH for autonomous side effects: timeout/cancel does not stop underlying SDK work.** EngineRuntime explicitly documents that its deadline stops the run, not the thread; shutdown uses wait=False. State cleanup/result quarantine cannot undo or terminate external/file side effects. Execute runs in killable, confined processes and verify child termination, budget reconciliation and resume behavior.

**A4 — MEDIUM: rules/retrieval-to-execution gap.** service.py:652 stores rules_prompt_block and :670 stores context_bundle; planner receives retrieval data. service.py:960-962 invokes runtime with only `goal=plan.goal`. Engine wrapper receives neither assembled block. Persisted context and a retrieval-aware plan are not proof the execution model sees them. Add a bounded, framed execution-context contract and tests capturing the exact model/SDK input.

**A5 — MEDIUM: per-hunk review and restore remain incomplete.** DiffPanel is read-only; CheckpointManager snapshots/list/diff exist but no full restore endpoint. Earlier 'no checkpoints' claims are too broad, and 'safe undo complete' would also overstate it.

**A6 — evidence debt:** STATUS headline is still August; final report initially says uncommitted/no migrations although later wave added committed work/0034. The matrix carries unresolved AS-REG/LEAD entries. Reconcile current release facts before using these reports in customer claims.

## Ten complaint areas

| # | Complaint | Current assessment | Remaining |
|---|---|---|---|
| 1 | Almost-right code | Outcome unproven | Real task correctness/accepted-change benchmark; review gate cannot guarantee model correctness. |
| 2 | Debugging slower than manual | Outcome unproven | Measure net developer time including review and rework against baseline. |
| 3 | Context loss | Partial | Rules/context execution injection, durable useful memory, quality checks. |
| 4 | Poor repo understanding | Partial | Index freshness, dependency/call graph, retrieval evaluation and execution grounding. |
| 5 | Unreliable edits | Partial | Isolated live workspaces, killable tools, per-hunk review, restore and bounded verification loops. |
| 6 | Security/privacy | Partial | Resolve A1–A3 and prove live tool confinement/provider boundaries. |
| 7 | Cost unpredictability | Partial | Personal/shared-key payer model, in-flight reservations and live billing validation. |
| 8 | Team collaboration | Strong foundation | Teams/roles/shared runs/meeting/review/event state exist; not full agent swarms or IDE collaboration. |
| 9 | Tool fragmentation | Partial | External MCP client/registry, IDE integration, tool scopes/consistent observations. |
| 10 | Accountability | Strong foundation | Approval, events, audit/review/manual merges; capture every future browser/tool action too. |

## Agentic capability and speed plan

Keep the Python orchestration/control plane. Add a **TypeScript/Node browser worker using Playwright**, with direct CDP only where Playwright lacks a necessary primitive. Python is also a valid Playwright client; TypeScript is a fit here because the repo already uses TS/Playwright, not because its language magically makes the model faster. Do not rewrite the platform in Rust/Go without a profiler identifying a hot path that warrants it.

Use a contract-only tool gateway: observe → choose typed action → authorize against approved scope/budget → execute in a per-run worker → verify postcondition → record event/artifact → continue or stop. Define tool input/output, cancellation and observation freshness contracts, bump contracts and regenerate TS mirrors/mocks in the same change. S5 remains execution enforcement authority; browser networking must not bypass its policy through a separate service.

Browser tool set: open/navigate, bounded DOM/accessibility snapshot, locate, click, fill, scroll, screenshot, console/network summary, verify/assert, close. Browser contexts isolated by team/project/run; session persistence scoped and encrypted, no cross-team cookie reuse. Add per-call timeouts, loop/step/cost limits, consent for external writes, URL/redirect/SSRF enforcement (including DNS changes), download/upload restrictions and secret scrubbing.

Accuracy: prefer API/DOM/accessible-name targets. Observe fresh target immediately before action, require actionability, verify URL/DOM/result afterward. Use screenshot/vision coordinates for canvas/GUI targets, recording viewport size, CSS pixels, devicePixelRatio, zoom, scroll and screenshot crop origin. Convert image coordinates into current viewport CSS coordinates and reject stale observations. Vision clicks cannot promise universal pixel-perfect semantic correctness; ambiguous targets should trigger re-observation or human intervention. CDP coordinates are CSS pixels, not arbitrary screenshot pixels.

Speed: warm browser pool; reuse only authorized isolated contexts; compact relevant DOM instead of full screenshots each step; cache unchanged observations/index entries; batch read-only independent observations; use event/actionability waits instead of fixed sleeps; stream results; bound concurrency and reuse connection pools. Parallelize independent work after fixing shared-state risks. Preserve the user's chosen model; no forced downgrade.

Benchmark cold/warm separately: tool-dispatch overhead, navigation, screenshot, locate/click latency, model time-to-first-token, model decision latency, total completion time, success rate, wrong-target rate, recovery rate, tokens/cost per accepted task. Suggested local-fixture engineering targets (NOT measured claims): p95 warm locate/click ≤250ms excluding model/network/navigation, p95 screenshot ≤500ms, zero cross-tenant cookie/context exposure, 100% blocked forbidden actions. Use multiple viewport/DPR/zoom/frame/scroll/canvas scenarios and report distributions; no universal web latency promise.

Order: (1) fix/prove A1–A3; (2) execution context and repeatable accepted-change eval; (3) typed tool gateway + browser worker; (4) browser correctness/security/replay benchmarks; (5) controlled developer pilot; (6) subagent delegation and external MCP only after capabilities and cancellation are enforceable. Reuse the existing queue/review/governance rather than building a second scheduler.

Live release work still needed: external Stripe/OIDC/object-store/model drills; real repo-to-draft-PR journey; production topology/isolation checks; reviewed privacy/terms/support/retention decisions; current remote CI/branch-protection verification. No push, branch protection change or outreach performed.

Primary technical references verified on the web: [Playwright locators](https://playwright.dev/docs/locators), [actionability](https://playwright.dev/docs/actionability), [locator screenshots/scale](https://playwright.dev/docs/api/class-locator), [CDP Input](https://chromedevtools.github.io/devtools-protocol/tot/Input/). Agent-reach Exa failed DNS; built-in web search provided these official sources. Agent-reach update command unavailable; no update status claimed.

## Full 150-item feature reconciliation

Statuses evaluate the requested whole capability; helpers/tests alone are PARTIAL. 'INTENTIONALLY EXCLUDED' honors the existing no-forced-downgrade rule.

| Original # | Capability | Assessment | Evidence / remaining work |
|---|---|---|---|
| 1 | Native VS Code extension | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 2 | JetBrains extension | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 3 | Neovim integration | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 4 | Browser-based IDE | PARTIAL | CodeMirror manual edits and tree API exist; no complete browser IDE. |
| 5 | Mobile task inbox | PARTIAL | Responsive web surfaces; no dedicated mobile task product. |
| 6 | Desktop app | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 7 | Inline diff review | PARTIAL | DiffPanel.tsx explicitly read-only; comments elsewhere are not per-hunk decisions. |
| 8 | Inline code actions | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 9 | Composer-style multi-file editing | PARTIAL | SDK may edit multiple files; coordinated per-file/hunk curation not present. |
| 10 | Agent chat anchored to code lines | PARTIAL | Comments exist; no verified symbol/range-anchored chat. |
| 11 | Incremental semantic codebase index | PARTIAL | Section11 embeddings/chunking/search; freshness and live deployment not proven. |
| 12 | Git-history-aware retrieval | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 13 | Cross-repository search | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 14 | Dependency graph visualization | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 15 | Symbol and call-graph navigation | PARTIAL | AST-aware chunking is not a full symbol/call graph. |
| 16 | Automatic context ranking | PARTIAL | Retrieval scoring/budgeting exists; execution receives only plan.goal. |
| 17 | Context-quality diagnostics | PARTIAL | Context paths/token drops stored on runs; no complete quality diagnostic surface. |
| 18 | Persistent project memory | PARTIAL | Project rules, briefs and events persist; rules not passed into live execution. |
| 19 | Hierarchical instruction files | PARTIAL | Project rules exist; no org/team/directory hierarchy. |
| 20 | Automatic documentation ingestion | PARTIAL | Repo context ingestion exists; no automatic wiki/ticket/ADR connectors verified. |
| 21 | Explicit Ask/Plan/Agent modes | PARTIAL | Plan gate and dry/live modes; no full read-only Ask/Plan/Agent permission model. |
| 22 | Plan approval gate | IMPLEMENTED | Mandatory approval state machine, service/API gate and expiry tests. |
| 23 | Long-running background agents | PARTIAL | Durable queue/heartbeat exist; thread cancellation and restart side-effect recovery incomplete. |
| 24 | Parallel agent jobs | IMPLEMENTED | Durable queue supports independent concurrent runs with bounded executor; credential/workspace caveats below. |
| 25 | Agent handoff between local and cloud environments | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 26 | Follow-up messages to active runs | PARTIAL | Human edit/nudge APIs exist; not a general active-run instruction channel. |
| 27 | Pause, resume, and cancel controls | PARTIAL | Pause/resume/stop states tested; running SDK thread cannot be killed. |
| 28 | Automatic retry policy | PARTIAL | Provider retry/breaker components exist; no complete policy across all tool failures. |
| 29 | Failure recovery strategies | PARTIAL | Manual recovery and breaker exist; no autonomous bounded strategy switching. |
| 30 | Task decomposition planner | PARTIAL | PlanService makes steps/citations; not a delegating task-graph planner. |
| 31 | Automatic branch creation per task | PARTIAL | GitHub createBranch API exists; automatic isolated branch per agent not established. |
| 32 | Git worktree isolation per agent | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 33 | Automatic commit drafting | PARTIAL | GitHub commit primitives exist; explainable automatic commit drafting unverified. |
| 34 | Pull-request creation from a completed run | PARTIAL | Draft-PR API/event linkage exists; live provider and complete run-to-PR drill pending. |
| 35 | Pull-request description generation | PARTIAL | PR title/body supplied to API; end-to-end generated evidence-rich description unverified. |
| 36 | Review-request routing | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 37 | GitHub issue-to-agent assignment | PARTIAL | GitHub webhook paths exist; issue-to-agent assignment workflow not verified. |
| 38 | GitLab issue and merge-request integration | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 39 | Bitbucket and Azure DevOps integration | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 40 | Automatic merge checks | PARTIAL | Manual approval/review/no-merge laws; external branch protection remains an operator action. |
| 41 | Automatic test discovery | PARTIAL | Plan includes tests and sandbox output parsing; no dependency-aware test discovery. |
| 42 | Test-generation workflow | PARTIAL | Model can generate tests; no first-class rationale/coverage workflow. |
| 43 | Test repair loop | PARTIAL | SDK may retry work; no first-class bounded test repair controller. |
| 44 | Failure reproduction harness | PARTIAL | Tests/runbooks/load profiles exist; no reusable customer bug-capture artifact. |
| 45 | Automatic bug reproduction from an issue or stack trace | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 46 | Browser end-to-end testing through Playwright or equivalent | PARTIAL | Playwright and compose journeys test this platform; agent cannot use browser tools. |
| 47 | Visual regression testing | PARTIAL | No verified shipped visual baseline comparison workflow; test screenshots are insufficient. |
| 48 | API contract testing | IMPLEMENTED | Frozen contracts, golden fixtures, generated clients/specs, and CI mirror checks. |
| 49 | Mutation-testing or test-strength analysis | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 50 | Verification scorecard | PARTIAL | Pilot final-report scorecard exists; no automated per-change verification product. |
| 51 | Browser control | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 52 | Desktop mouse and keyboard control | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 53 | Remote desktop takeover | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 54 | Visual screen understanding | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 55 | Browser session persistence | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 56 | Human-in-the-loop browser approval | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 57 | Browser network and console diagnostics | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 58 | Local application preview with agent interaction | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 59 | Mobile browser or PWA testing | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 60 | Computer-use action replay | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 61 | First-class MCP server registry | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 62 | MCP permission scopes | PARTIAL | MCP server inherits bearer roles/team checks; no external-tool per-run scopes. |
| 63 | MCP server health checks | PARTIAL | Own MCP server has tools; no external registry discovery/health controller. |
| 64 | Custom tool builder | PARTIAL | backend_runner extension seam; not a user-configurable tool builder. |
| 65 | Reusable skills or playbooks | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 66 | Plugin marketplace | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 67 | Namespaced slash commands or task commands | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 68 | Event hooks | PARTIAL | Lifecycle/audit hooks exist; no comprehensive tool-call/edit/compaction hook interface. |
| 69 | Tool allowlists and denylists | PARTIAL | S5 policy and egress controls exist; command allowlist largely code-defined. |
| 70 | Extension SDK | PARTIAL | Python/TS REST SDKs and backend_runner seam; no full extension SDK. |
| 71 | Specialized subagents | PARTIAL | Reviewer and meeting AI roles exist; no user-configurable specialist subagents. |
| 72 | Agent-to-agent delegation | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 73 | Shared task board | PARTIAL | Shared runs/review surfaces; no shared agent/human task board. |
| 74 | Agent team or swarm mode | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 75 | Independent context windows per subagent | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 76 | Per-subagent model selection | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 77 | Per-subagent tool permissions | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 78 | Subagent result synthesis | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 79 | Human assignment and escalation queue | PARTIAL | BLOCKED state and human controls exist; no assignment/escalation work queue. |
| 80 | Agent workload, queue, and concurrency controls | IMPLEMENTED | run_queue.py, worker.py and EngineRuntime enforce queue/concurrency policies. |
| 81 | Personal BYO-key profiles | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 82 | Consent-based key sharing | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 83 | Per-task model routing | PARTIAL | PRIMARY/REVIEWER models and explicit model params exist; no adaptive task router. |
| 84 | Automatic fallback across providers | INTENTIONALLY EXCLUDED | No forced model downgrade per AGENTS.md. Provider/model fallback requires explicit approved policy; do not count as mandatory debt. |
| 85 | Model quality and cost comparison for the same task | PARTIAL | Spend/latency history exists; no controlled same-task quality comparison. |
| 86 | Prompt or context caching controls | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 87 | Token-budget prediction before execution | PARTIAL | Estimated plan cost and context limits exist; not a calibrated token forecast. |
| 88 | Spend anomaly detection | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 89 | Organization model allowlist with approval workflow | PARTIAL | Catalog/roles/provider config exist; no org allowlist approval workflow verified. |
| 90 | Local-model discovery and health monitoring | PARTIAL | Arbitrary compatible endpoint config; no first-class local-model discovery. |
| 91 | Secret scanning before prompt or tool transmission | PARTIAL | Secret redaction exists; outbound prompt/tool secret scanner not established. |
| 92 | Prompt-injection detection and quarantine | PARTIAL | Untrusted-input framing exists; no general injection detector/quarantine guarantee. |
| 93 | Sensitive-file policy | PARTIAL | S5 path policy/forbidden_paths exist; SDK execution enforcement gap needs proof. |
| 94 | Data-loss prevention rules | PARTIAL | Redaction and network policy components; no general outbound content DLP. |
| 95 | Fine-grained tool capability tokens | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 96 | Per-run network egress policy | PARTIAL | S5 egress controls exist; per-run live SDK egress enforcement unproven. |
| 97 | Signed agent or plugin packages | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 98 | Security event correlation | PARTIAL | Run/audit/request IDs exist; no security correlation engine. |
| 99 | Automated dependency vulnerability remediation | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 100 | Policy simulation mode | PARTIAL | Dry mode is not a full policy permission simulator. |
| 101 | One-click ephemeral development environments | PARTIAL | Per-task sandbox workspaces exist; no one-click complete dev environment. |
| 102 | Prebuilt repository environments | PARTIAL | Backend image builds dependencies; no repository-specific warm environment cache. |
| 103 | Multiple sandbox backends | PARTIAL | LocalSandboxRunner and Docker runner code; production backend parity not established. |
| 104 | Environment snapshots and restore points | PARTIAL | Git checkpoints record/list/diff edits; no full environment restore API. |
| 105 | Resource quotas | PARTIAL | Compose CPU/memory/pids and run deadlines; disk quotas/end-to-end SDK kill incomplete. |
| 106 | Internet access modes | PARTIAL | S5 allow_network and egress; no verified agent-wide three-mode Internet policy. |
| 107 | Dependency-install approval workflow | PARTIAL | Package installs blocked; consent-grant installation workflow not implemented. |
| 108 | Multi-host worker pool | PARTIAL | DB claims support workers; multi-host operated scheduling/health not proven. |
| 109 | Offline or air-gapped deployment mode | PARTIAL | Self-host/local models supported; complete offline provisioning guide not verified. |
| 110 | Disaster-recovery drill and restore verification | PARTIAL | Local PITR drill/runbooks; real object-store drill and product surface pending. |
| 111 | Run replay | PARTIAL | Events/rehydration/demo replay; not every prompt/tool/file observation captured. |
| 112 | OpenTelemetry export | PARTIAL | JSON logs/Prometheus/request IDs exist; full OTel export not located. |
| 113 | Agent quality dashboard | PARTIAL | Spend/run dashboards exist; quality acceptance/rework dashboard absent. |
| 114 | Developer productivity dashboard | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 115 | Cost-per-task and cost-per-accepted-change metrics | PARTIAL | Per-run metering exists; accepted-change denominator missing. |
| 116 | Time-to-first-useful-change metric | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 117 | Searchable organization-wide run archive | PARTIAL | Team-scoped run history and retention; complete searchable org archive not verified. |
| 118 | Exportable audit evidence package | PARTIAL | Audit GET/event history exist; no packaged change/incident export. |
| 119 | Alerts for stalled, risky, expensive, or policy-violating runs | PARTIAL | Budget warnings/reaper/notifications exist; no complete actionable risk alert pipeline. |
| 120 | Usage APIs and webhooks | PARTIAL | Spend API/notifications exist; no comprehensive finance/security usage webhook API. |
| 121 | Slack task intake and status updates | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 122 | Microsoft Teams task intake and status updates | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 123 | Linear issue integration | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 124 | Jira issue integration | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 125 | Notion or internal-wiki context integration | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 126 | Sentry error-to-agent workflow | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 127 | Datadog or Grafana incident-to-agent workflow | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 128 | CI provider integrations | PARTIAL | Project GitHub Actions gates exist; no multi-provider customer CI integration. |
| 129 | Chat notifications with approve, reject, and inspect actions | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 130 | Calendar or scheduled maintenance-task integration | PARTIAL | Operator cron/maintenance profiles; no integrated calendar/scheduled task product. |
| 131 | Five-minute guided onboarding | PARTIAL | Tutorial/onboarding/checklist exist; unassisted five-minute success not measured. |
| 132 | Repository readiness check | PARTIAL | Onboarding checks exist; not a full repo-fit/test/dependency assessment. |
| 133 | Interactive tutorial project | PARTIAL | Demo fixture/repo/journey exist; no verified interactive teaching product. |
| 134 | Task templates | PARTIAL | Demo/sample brief exists; no user task-template library. |
| 135 | Saved prompt and workflow library | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 136 | User-configurable keyboard shortcuts and command palette | PARTIAL | Some keyboard/focus helpers; no user-configurable shortcut/command palette product. |
| 137 | Accessibility support | PARTIAL | Labels/focus/responsive tests exist; full screen-reader/keyboard audit not done. |
| 138 | Internationalization and timezone-aware scheduling | NOT FOUND | No first-class implementation located in reviewed source/API/UI/config inventory. |
| 139 | Public status page and incident communication workflow | PARTIAL | Health/ready/metrics endpoints; no public incident status communications product. |
| 140 | In-product feedback capture linked to the exact run and change | PARTIAL | Comments/nudges exist; no product-feedback-to-exact-change flow. |
| 141 | Self-serve workspace creation | PARTIAL | OTP/team/project onboarding exists; complete self-serve lifecycle not drilled. |
| 142 | Trial-to-paid conversion flow | PARTIAL | Billing UI/Stripe adapter exist; live checkout not drilled. |
| 143 | Usage-based billing | PARTIAL | Ledger/tiers/Stripe code exist; real usage billing reconciliation not drilled. |
| 144 | Budget alerts and automatic spend ceilings | PARTIAL | Hard budget gate + warning threshold/API; delivery UX/live billing limits incomplete. |
| 145 | SSO and SCIM provisioning | PARTIAL | OIDC/PKCE + SPA callback exist; SCIM not found, live IdP drill pending. |
| 146 | Organization-level admin console | PARTIAL | Team settings/membership/billing pages; no distinct org admin console. |
| 147 | Custom retention and deletion policies | PARTIAL | Retention/deletion migrations and runbooks; no per-project policy console. |
| 148 | Data residency controls | PARTIAL | Self-host placement enables operator choice; no enforced residency router. |
| 149 | Compliance evidence center | PARTIAL | Audit surfaces/runbooks; no compliance evidence-center product. |
| 150 | Customer-facing API documentation and SDK distribution | PARTIAL | OpenAPI specs + Python/TS clients exist; distribution/compatibility on customer installs unverified. |

## Original voice-session 50 future risks

These numbers refer to the voice notes, not the separate R-01…R-50 pilot-security register. None of these is permanently eliminated.

| # | Future risk | Assessment | Evidence / remaining validation |
|---|---|---|---|
| 1 | The core workflow remains too complex for a first-time team. | PARTIAL | Guided surfaces exist; run an unassisted first-run usability study. |
| 2 | A new team cannot reach its first useful run in five minutes. | PARTIAL | Time-to-first-useful-change not measured with real developers. |
| 3 | The CLI is missing or too awkward, so developers avoid the product. | OPEN / UNVALIDATED | SDK/MCP are not a polished task CLI. |
| 4 | There is no strong IDE/editor integration where developers already work. | OPEN / UNVALIDATED | No native IDE extensions found. |
| 5 | Repository indexing and context selection miss important files or decisions. | PARTIAL | Retrieved context does not reach execution as an explicit bundle. |
| 6 | Long sessions lose context or produce inconsistent handoffs. | PARTIAL | Events persist; cross-session decision/context quality unmeasured. |
| 7 | Resume, retry, rollback, and checkpoint behavior is confusing or unreliable. | PARTIAL | State tests pass; SDK threads cannot be killed and checkpoints lack full restore. |
| 8 | Review and pull-request workflows feel bolted on rather than native. | PARTIAL | PR APIs exist; live end-to-end repo/provider journey remains. |
| 9 | Agents lack safe branch/worktree isolation for concurrent work. | PARTIAL | Compose shares DSH_WORKSPACE; no per-run worktree in live wrapper. |
| 10 | The product handles coding runs but not adjacent planning, testing, documentation, or incident workflows. | PARTIAL | Meetings/review exist; generalized planning/testing/incident agent workflows remain. |
| 11 | Prompt injection arrives through repository files, issues, pull requests, documents, or tool output. | CONTROLS PRESENT | Untrusted boundary framing reduces risk; no universal prompt-injection solution. |
| 12 | Agents expose API keys, tokens, secrets, or proprietary source code. | CONTROLS PRESENT | Vault/redaction controls reduce exposure; synthetic concurrent-wrapper credential race newly reproduced. |
| 13 | A sandbox escape or unsafe network path turns an agent run into host compromise. | PARTIAL | S5 confinement tested; live SDK-to-S5 enforcement not demonstrated. |
| 14 | Malicious MCP servers, skills, plugins, or dependencies enter through the agent supply chain. | OPEN / UNVALIDATED | No external MCP/package trust registry; future exposure needs scope/signatures. |
| 15 | Permissions are too broad, so a “read-only” workflow can still change files or call external systems. | CONTROLS PRESENT | Bearer roles/path policies present; prove every SDK tool routes through enforced capability boundary. |
| 16 | A compromised or malicious model provider returns instructions that bypass the intended workflow. | PARTIAL | Approval gates present; live tool boundary remains unverified. |
| 17 | A cross-tenant authorization bug exposes another team’s runs, events, files, or costs. | CONTROLS PRESENT | Authz/isolation tests pass; no guarantee against future regressions. |
| 18 | Personal-key sharing creates unclear consent, ownership, billing, or revocation behavior. | OPEN / UNVALIDATED | Team keys are not owner-consented personal-key sharing. |
| 19 | OAuth, webhooks, email login, or session handling fails under real production attacks. | PARTIAL | Mock IdP/OAuth tests; real IdP/provider drills pending. |
| 20 | The product cannot produce sufficient security evidence for a customer’s review, procurement, or insurance requirements. | OPEN / UNVALIDATED | No customer procurement/evidence package validated. |
| 21 | Provider outages, rate limits, or model quotas stop customer runs. | CONTROLS PRESENT | Timeout/circuit breaker/provider failures tested; provider outage behavior still ongoing risk. |
| 22 | Model updates silently change behavior and invalidate plans, tests, or review results. | PARTIAL | SDK pin/contracts exist; model quality/version benchmark missing. |
| 23 | Long-running runs fail after consuming tokens without a usable resume point. | PARTIAL | Session IDs/queue exist; kill/restart mid-tool recovery not established. |
| 24 | Queue, concurrency, or cancellation bugs cause starvation, duplicate work, or runaway spend. | CONTROLS PRESENT | Queue/heartbeat/replay tests pass; non-killable threads remain residual. |
| 25 | Multi-replica deployment breaks sessions, WebSockets, OAuth state, rate limits, or event ordering. | PARTIAL | PG delivery/claims tested; S5 registry and live workspace isolation limit horizontal operation. |
| 26 | Event logs, indexes, artifacts, or databases grow until storage and query costs become serious. | CONTROLS PRESENT | Retention/partition/migrations exist; disk artifact policy/scale still require operations. |
| 27 | Backups exist but a real restore has never been proven under pressure. | PARTIAL | Local drill exists; real object-store restore drill pending. |
| 28 | Missing or noisy traces make customer incidents impossible to diagnose. | CONTROLS PRESENT | Structured logs/request IDs/metrics present; distributed tracing incomplete. |
| 29 | Different model, prompt, contract, or dependency versions make results irreproducible. | CONTROLS PRESENT | Frozen contracts, SDK pin, specs and mirror drift checks present. |
| 30 | A vulnerable dependency, container, image, or build tool creates an emergency release. | CONTROLS PRESENT | Secret/dependency scanning configured; vulnerability remediation remains ongoing. |
| 31 | Buyers cannot see a measurable return on investment beyond “the agent feels useful.” | OPEN / UNVALIDATED | No measured accepted-change productivity ROI from real pilot. |
| 32 | Developers distrust the system and bypass it with direct Claude, Cursor, Codex, or local-agent usage. | OPEN / UNVALIDATED | Need adoption and bypass-rate evidence from users. |
| 33 | Engineering managers do not want another dashboard or approval ceremony. | OPEN / UNVALIDATED | Need measured approval overhead and pilot feedback. |
| 34 | The team uses the Harness only for demos and not for recurring production work. | OPEN / UNVALIDATED | No longitudinal recurring-use evidence. |
| 35 | GitHub, GitLab, IDEs, and agent providers change APIs faster than integrations can keep up. | OPEN / UNVALIDATED | No customer integration compatibility matrix. |
| 36 | GitHub, Anthropic, OpenAI, Google, or Microsoft bundle similar governance into tools customers already pay for. | OPEN / UNVALIDATED | Competitive risk cannot close through code changes. |
| 37 | The category is too crowded and the phrase “AI engineering harness” is not understood by buyers. | OPEN / UNVALIDATED | Positioning needs buyer validation. |
| 38 | Enterprise security and procurement reviews take longer than the product’s sales runway. | OPEN / UNVALIDATED | Sales/procurement cycle unmeasured. |
| 39 | Self-hosted installation, upgrades, backups, and provider setup are too difficult for ordinary teams. | PARTIAL | Bootstrap/runbooks exist; independent install/upgrade trial pending. |
| 40 | Documentation, examples, support, and community are too thin to create independent adoption. | OPEN / UNVALIDATED | Runbooks exist; customer support intake/SLA/community not validated. |
| 41 | Model and infrastructure costs exceed subscription revenue when teams run many agents. | PARTIAL | Budgets/ledger exist; real unit economics not measured. |
| 42 | Pricing is guessed, underpriced, or too complicated to explain. | OPEN / UNVALIDATED | Pricing is a hypothesis without paid-customer validation. |
| 43 | Every customer requires custom deployment and support, turning SaaS into unprofitable consulting. | OPEN / UNVALIDATED | Repeatable delivery/support margin unmeasured. |
| 44 | Teams value governance but are not willing to pay for it separately from their existing agent tools. | OPEN / UNVALIDATED | No willingness-to-pay evidence. |
| 45 | Shared credentials make it impossible to attribute spend and protect the key owner from surprise bills. | OPEN / UNVALIDATED | No personal/shared-key payer consent model. |
| 46 | A customer blames the Harness for an agent-generated defect, data loss, outage, or security incident. | OPEN / UNVALIDATED | Terms/privacy and incident/liability process need decisions/counsel. |
| 47 | Data residency, privacy, retention, or sector compliance requirements block otherwise interested customers. | OPEN / UNVALIDATED | Residency/retention/compliance agreements not validated. |
| 48 | Provider terms, model access rules, or third-party API policies restrict the BYO-key or multi-provider model. | OPEN / UNVALIDATED | Provider-policy review remains an operator/legal task. |
| 49 | Usage falls after the initial novelty period because the team has no recurring trigger to return. | OPEN / UNVALIDATED | Retention/recurring workflow evidence needs live pilot. |
| 50 | The best features are copied or absorbed by larger platforms before the Harness establishes distribution, workflow data, or customer trust. | OPEN / UNVALIDATED | Distribution and customer trust are market risks, not fixable checklist bugs. |

## Original sources

Voice session: `/Users/user/Documents/Codex/2026-10-16/realtime-voice-chat-4/outputs/session-notes-2026-10-16_23-03-52.md`; competitive register in the same folder: `harness-feature-gap-register-2026-10-16.md`. Repository sources: `docs/pilot-remediation/{RISK_REGISTER,RECONCILIATION_MATRIX,FINAL_REPORT}.md`, `docs/EXPANSION_PLAN.md`, `docs/clientela/part3-roadmap.md`, source/tests and current Makefile.
