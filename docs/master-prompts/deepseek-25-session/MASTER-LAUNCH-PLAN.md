# Master launch plan — five DeepSeek V4.1 Flash agents × five sessions

## How to run

Open FIVE separate agents, all using DeepSeek V4.1 Flash. Give each an isolated worktree/checkout and paste its entire corresponding file below. Each file already repeats the common governance, ownership, baseline, transport agreement, testing, independence and reporting instructions: do not paste all five into every agent. No other model or subagent is required.

Sessions are five independently restartable work packages per agent. They do not create five automatic context windows; if the host stops, resume the relevant named session with the same full prompt. Run the five agents concurrently; each progresses through its own five sessions. Every session has six original features, totaling 150 feature assignments exactly once. Each agent owns ten original future risks, totaling 50 exactly once. Implemented rows are regression/protection assignments; F084 preserves the no-forced-downgrade rule.

Full-stack integration necessarily follows parallel implementation. This plan makes each work package independently runnable/testable against the agreed baseline; it cannot make a new cross-service feature production-integrated without combined validation. NEEDS INTEGRATION and NEEDS EXTERNAL VALIDATION are honest statuses, not completion. No promised fixed fraction of commercial/legal risks or all 150 product surfaces can be guaranteed by a session count.

## Five prompts

- [A1: Security, Identity, Models and Commercial Governance](a1-security-identity-models.md) — five sessions, 30 features, ten future risks.
- [A2: Execution, Context, Memory and Multi-Agent Runtime](a2-execution-context-agents.md) — five sessions, 30 features, ten future risks.
- [A3: Browser and Computer Use, Sandboxes and Verification](a3-browser-sandbox-verification.md) — five sessions, 30 features, ten future risks.
- [A4: Developer Experience, Editors, Git Workflow and Adoption](a4-developer-experience-git.md) — five sessions, 30 features, ten future risks.
- [A5: MCP, Integrations, Observability, Operations and Pilot Evidence](a5-mcp-integrations-operations.md) — five sessions, 30 features, ten future risks.

## Code ownership

| Agent | Exclusive existing code ownership | Exclusive new code namespaces |
|---|---|---|
| A1 | `section1/**, section3/**, contracts/**` | `packages/security-policy/**` |
| A2 | `section4/**, section6/**, section11/**` | `packages/agent-runtime/**` |
| A3 | `section5/**` | `workers/browser/**, workers/desktop/**, packages/verification/**, scripts/agent-program/a3/**` |
| A4 | `section2/**, section7/**` | `integrations/editors/**, apps/desktop/**, packages/dev-cli/**` |
| A5 | `mcp/**, clients/**, section8/**, section10/**, deploy/**, .github/**, Makefile, .env.example, .gitignore, scripts/sdk-gen/**, scripts/loadtest/**, scripts/e2e/**` | `packages/extensions/**, integrations/connectors/**, packages/telemetry/**, scripts/agent-program/a5/**` |

Unlisted shared files are read-only. Agent1 owns contracts/migrations, Agent5 owns global deployment/CI, Agent4 owns the existing frontend. The prompt pack is immutable input; each agent writes its own reports under docs/agent-program/aN. Existing AGENTS.md user changes must be preserved.

## Twenty-five work packages

| Session | Owner | Deliverable | Original feature IDs |
|---|---|---|---|
| A1-S1 | A1 | Outbound security and enforceable capabilities | F091, F092, F093, F094, F095, F096 |
| A1-S2 | A1 | Personal keys, payer consent and bounded spend | F081, F082, F087, F088, F143, F144 |
| A1-S3 | A1 | Approved model routing and measurable efficiency | F083, F084, F085, F086, F089, F090 |
| A1-S4 | A1 | Supply-chain trust, enterprise provisioning and lifecycle policy | F097, F098, F099, F100, F145, F147 |
| A1-S5 | A1 | Self-service governance and customer evidence | F022, F141, F142, F146, F148, F149 |
| A2-S1 | A2 | Repository intelligence and grounded planning | F011, F012, F013, F014, F015, F016 |
| A2-S2 | A2 | Execution context, memory, modes and faithful replay | F017, F018, F019, F020, F021, F111 |
| A2-S3 | A2 | Killable isolated execution, continuation and recovery | F023, F025, F026, F027, F028, F029 |
| A2-S4 | A2 | Task decomposition and independent specialist agents | F030, F071, F072, F073, F074, F075 |
| A2-S5 | A2 | Per-agent policies, synthesis and fair scheduling | F076, F077, F078, F079, F080, F024 |
| A3-S1 | A3 | Precise browser and guarded computer interaction | F051, F052, F053, F054, F055, F056 |
| A3-S2 | A3 | Browser diagnostics, preview, replay and visual QA | F057, F058, F059, F060, F046, F047 |
| A3-S3 | A3 | Fast isolated environments, snapshots and quotas | F101, F102, F103, F104, F105, F106 |
| A3-S4 | A3 | Install approvals, worker durability and reproducible incidents | F107, F108, F109, F110, F044, F045 |
| A3-S5 | A3 | Agent verification loops and evidence strength | F041, F042, F043, F048, F049, F050 |
| A4-S1 | A4 | Editable code surface and granular review | F004, F007, F008, F009, F010, F040 |
| A4-S2 | A4 | Isolated Git work and reviewed pull requests | F031, F032, F033, F034, F035, F036 |
| A4-S3 | A4 | IDE entry points, desktop shell and issue intake | F001, F002, F003, F006, F037, F038 |
| A4-S4 | A4 | Mobile access, remaining Git providers and first use | F005, F039, F131, F132, F133, F134 |
| A4-S5 | A4 | Workflow library, accessibility and recurring use | F135, F136, F137, F138, F139, F140 |
| A5-S1 | A5 | External MCP and trusted extensibility | F061, F062, F063, F064, F065, F066 |
| A5-S2 | A5 | Commands, hooks, tool configuration and SDK delivery | F067, F068, F069, F070, F125, F150 |
| A5-S3 | A5 | Team channels and incident-to-agent connectors | F121, F122, F123, F124, F126, F127 |
| A5-S4 | A5 | Tracing and measurable developer value | F112, F113, F114, F115, F116, F117 |
| A5-S5 | A5 | Evidence, alerts, scheduling and operational acceptance | F118, F119, F120, F128, F129, F130 |

## Integration acceptance after the 25 packages

This is an acceptance checklist for the combined result, not an undisclosed 26th implementation session or a requirement to wait during independent work. Do not merge or deploy automatically.

1. Collect all five file manifests and F/VR ledgers, verify ownership and preserve unrelated work. Review unavailable or unsupported sub-capabilities before calling any feature done.
2. Reconcile Agent1 contract/migration changes, generated mirrors and golden fixtures with each independent HTTP adapter. Confirm request/response schemas and error/status mappings agree. Re-run all consumers against real counterparts.
3. Review any non-owned integration proposals; they need implementation and tests before relevant F rows can close. A transport agreement and mocks do not close this work.
4. Assemble deployment in a disposable environment and prove principal propagation, isolation, S5 enforcement of SDK/browser/desktop/MCP actions, credential separation, approval expiry and cancellation of child process trees.
5. Run make gate plus Section11, MCP, production frontend build, real-PG migration/idempotency/restore probes, live browser journeys, extension install smoke, tool replay/security checks and independent concurrent-run tests.
6. Re-run latency/accuracy/cost benchmarks with actual deployed workers/model configuration. Record synthetic versus real fixtures, cold/warm conditions and any skipped environment-dependent checks.
7. Execute authorized external-provider drills, review legal/retention/support decisions, and run a controlled non-production developer pilot. Commercial acceptance/ROI/retention remain unproven until measured.
8. Publish a truthful combined verdict with all 150 F rows and 50 VR rows, pending integrations/external validation and release blockers. Keep new unsafe/unverified autonomous paths disabled.

## Assignment files

- feature-assignment.csv preserves all 150 original feature numbers, exact titles, assessment and sole owner/session.
- future-risk-assignment.csv preserves all 50 original voice-risk numbers and sole primary owner.
- These VR risks are DIFFERENT from the repository's separate pilot R-01…R-50 register. The 177-ID/174-row remediation matrix is background evidence, not the same 150-feature inventory.

This pack prepares instructions only. It does not start agents, alter the application, commit, merge, deploy or contact external parties.
