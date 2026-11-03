# Marathon 2.0 continuation — W21 to W29

Marathon 2.0 has run its 20 scheduled weeks; it is **not complete**. W17
(reliability and scale) is still in progress with another agent and is
uncommitted. The W20 decision record is blank, and the W20 release checklist
still has unticked engineering and owner items. This plan extends the marathon
past W20 instead of closing it. The scope comes from the 2026-10-30 readiness
audit, whose verdict was "not ready for production or a general pilot; a
limited design-partner pilot is supported once its gates close."

Order (from the audit):

1. trust and recovery;
2. one excellent everyday loop;
3. a credible benchmark;
4. differentiation;
5. delegation and ecosystem;
6. parity driven by feedback.

Each week gets its own `docs/m2-wNN-plan.md` when it starts, following the
W1–W20 format: scope, evidence, exit, open items carried forward.

## Audit inputs (verified against the week records, 2026-10-30)

* `make gate` during the audit: static checks and lint passed. Section 1 had
  1,175 passed, 63 skipped and 27 errors. The errors came from the audit
  sandbox denying local socket binding, not from a code defect. The later
  sections were not rerun.
* The W8 quota gate is not delegation: nothing starts an agent process
  (`m2-w8-plan.md`, "Quota gate ≠ delegation").
* The W7 `.vsix` and a real-window smoke test have not been run
  (`m2-w7-plan.md`).
* The W13 execution leg is not built (`m2-w13-plan.md`).
* A possible lease or heartbeat weakness was seen during a ~6-minute vision step
  (`evidence/w20/README.md`, `w14-design-heldout` r3).
* Only n=3 per live task, mostly on narrow fixtures. `--repeats` means retry
  until the first pass, not independent trials.
* Speed claims stop at fixture timings: tens of seconds per small task, and a
  dry-echo load run on a single host. There is no live-agent capacity figure
  and no comparison with another harness.

## Weeks

| Week | Theme | Scope | Exit evidence |
|---|---|---|---|
| **W21** | Trust: confinement | Close M01, the native-tool confinement review, across every supported enforcement backend. Grant no wider access because a happy-path task passes. | An M01 record per backend with the escape attempts tried; `native-tool-confinement` is back in the fixed set and passing. |
| **W22** | Trust: stop and recovery | Prove these on the deployed topology, not in unit tests: worker and child-process cancellation, partial-write reconciliation, crash cleanup, event replay, and provider-outage recovery. Investigate the lease/heartbeat weakness together with the W17 owner, without editing W17's files. | A drill per failure mode with the before and after state. The lease weakness is either reproduced and fixed, or ruled out with evidence. |
| **W23** | Everyday loop: start and run | A first-run readiness check covering provider, model, repository, worker and permissions. Deployment drift detection that names the stale component; the W20 stale-daemon `401` must be caught by this check, not by a person. Live status that distinguishes working, waiting, reconnecting, blocked and finished. A real stop that confirms the process ended. Steering that says when it takes effect. | A fresh-machine setup run. The drift check fails on a deliberately stale daemon. A stop test confirms no orphan processes. |
| **W24** | Everyday loop: review and editor | A review summary listing changed files, tests run, failures, cost, elapsed time and remaining uncertainty. VS Code `.vsix` packaging, a real-window smoke test and keyboard-first diff review. Recheck the open items in the `ui-ux-audit-2026-10-25.md` against the current code. | An installed `.vsix` used in a real window for one task, end to end. UI audit items marked closed or still open. |
| **W25** | Benchmark | Real repositories covering bug fixes, test writing, refactors, frontend and multi-step changes. Independent trials with hidden verification, keeping every failure. Metrics: time to first useful output, time to verified completion, success rate, cost, tool calls, user interventions and recovery. Cold and warm starts, plus concurrent live runs with p50/p95 queue and completion times. A separate security suite for scope escapes, secrets, prompt injection and untrusted tool output. Rerun the fixed live set on Haiku (`multi-file-refactor`, `tests-written`, `w11-grounding`), and decide the model or retirement for the six tasks pinned to the local model. | A versioned benchmark with a protocol and raw results. A comparison with another harness is quoted only under the same task, model, budget and permissions. |
| **W26** | Fix what the benchmark found | The largest quality and latency bottlenecks from W25, in ranked order. | The W25 rerun shows an improvement against its own baseline, failures included. |
| **W27** | Governance as the differentiator | A compact, editable plan showing scope, risk, verification and estimated cost, with approval bound to the version that executes. Policy denials that explain themselves, offer a safe alternative and let the user request more scope. Estimated versus actual cost, and an explicit user-chosen retry or model change, never a silent downgrade. Recovery as one-click inspect, rewind and resume. | A live task shows each of these. The approval-binding and no-silent-downgrade checks are tests. |
| **W28** | Real parallel delegation | Engine-driven spawning and cancellation of subagents behind the W8 gate. Each subagent gets an isolated workspace, quotas, visible ownership, conflict handling and a reviewable synthesis. Starts only after W22 has proved cancellation and recovery. | One delegated task with two subagents, one of them cancelled mid-run with no orphan processes, and a reviewed merge. |
| **W29** | Ecosystem, parity and readiness rerun | Build the W13 BYO-runner execution leg. A review path for third-party MCP servers. Check current Claude Code, Codex and Cursor features against their official documentation; the audit could not reach the web. Then rerun release readiness (the W20 procedure) at the new revision. | The W20 checklist refilled from the raw files and handed to an owner who did not write the code. |

## Owner gates (not engineering weeks)

These stay open whatever the weeks deliver; the W20 checklist tracks them:

* named on-call and status ownership;
* backup-freshness monitoring and alert delivery on the real deployment;
* a restore drill on the real deployment;
* the W17 SLOs adopted;
* an independent release-candidate review;
* an external security review;
* counsel on pricing;
* package publish credentials;
* Hermes rights;
* the W20 decision record.

## Rules carried over

* Do not touch W17's files until its owner commits or hands them over.
* Commit by explicit path. Do not push unless asked.
* Pre-registered thresholds are not loosened to make a gate pass.
* Every week records spend from `cost_ledger` and keeps its failures.
* Model changes rerun the vision tasks (W20 limitation).

## Integration track — added 2026-10-31

This track turns the local-project and open-source survey into staged work. It
is a proposal, not a completed audit or a claim that any candidate is safe,
owned, licensed for the intended use, or compatible. Preserve the W21–W29
trust, benchmark and release gates above; integrations do not waive them.
The Scheduler below is an academic timetabling optimizer, not the Harness run
queue. Hx M1–M5 are already complete at `332d273`; their documented limits
remain in `hx-execution-architecture.md`.

| Stage | Work and dependencies | Exit evidence |
|---|---|---|
| **W30 — candidate audit and architecture** | Inventory candidate provenance, revisions, licenses, dependencies, tests, data flows and operating costs. Inspect Scheduler, Drift Workbench, Headroom and agentic-immunity first; review Hermes rights. Compare with existing Harness services and W13/W17/W22 evidence. | An independently reviewable decision record for each candidate: adopt, pilot, defer or reject, with evidence and unresolved ownership/license/security questions. No integration on a README claim alone. |
| **W31 — Scheduler planning experiment** | After W22 recovery gates, prototype a resource-allocation adapter using synthetic tasks. Inspect `/Users/user/scheduler/solver/` and compare adapting its timetable-specific contracts with a new model using OR-Tools. The existing queue, policy, approvals and worker lifecycle remain authoritative. | Tests for feasible/infeasible problems, dependency and capacity constraints, timeouts, independent validation and explanation; measured latency; no real execution or expanded permissions. Record whether the adapter is worth shipping. |
| **W32 — governed execution and observability** | Only after W31's decision and W28 delegation gate: connect an approved scheduling proposal to existing Hx/Section 4 execution. Evaluate OpenTelemetry for run/worker/tool correlation; audit telemetry for secrets and tenant isolation. | End-to-end approved task, policy denial, cancellation, recovery and review on a verified deployment; trace redaction and cross-team isolation tests; no unapproved dispatch or automatic merge. |
| **W33 — optional tools and readiness** | Evaluate Drift Workbench as a permissioned diagnostic; Headroom against the W25 benchmark; Tree-sitter against the existing index/LSP; immunity-layer ideas against existing identity and policy; Langfuse and Temporal only against a demonstrated gap. Reassess BYO runner, VS Code and readiness. | Candidate-specific security, license and quality results with failures retained; no forced model downgrade; owner-reviewed release decision at an identified revision. Reject candidates that fail the gates or add unjustified duplicate control planes. |

### Candidate boundaries and decisions pending

* **Scheduler / OR-Tools:** an optimizer proposes assignments, never grants
  authority, relaxes forbidden paths, or substitutes for the queue. Its
  domain-specific frozen contract and Python/dependency compatibility need review.
* **Hx / Hermes:** Hx is the existing governed workflow surface. Hermes is a
  separate proprietary project; rights and distribution clearance precede any
  runtime integration. Do not treat Hx as proof of Hermes language support.
* **Drift Workbench:** optional diagnostics must use Section 5's allow-list,
  explicit network permissions and bounded, redacted evidence.
* **Headroom:** compare quality, cost and recovery with and without compression;
  do not remove review-critical context or expose secrets to another service.
* **Agentic immunity:** evaluate revocation and attestation ideas against existing
  controls; its prototype is not a replacement for Section 1/3/5 authorization.
* **Odysseus and Dify:** feature/UX study only until architecture, ownership and
  license review; avoid merging duplicate platforms. **book-to-skill:** optional
  untrusted-content import, with review and policy enforcement before use.
* **Temporal, Langfuse, Tree-sitter and OpenTelemetry:** evaluate specific
  components, licenses and deployment costs. Temporal is an alternative to be
  compared with existing durable execution, not an additional authority by default.
* **FreeLLMAPI and free-claude-code:** no automatic provider/model failover that
  violates the no-forced-downgrade rule. Other unrelated local projects remain
  outside scope unless a concrete user need and audit justify them.

### Current handoff and immediate next work

At `8531033`, Hx M1–M5 and W17 terminal-publish retry are committed; subsequent
commits record W13 accounting/notifications and W19 cross-replica admission and
NOTIFY evidence. Read their work orders before repeating earlier gap claims. W13
still needs its documented remote agent loop and other remaining gates; W20's
release decision and human ownership gates remain open. `STATUS.md` retains the
NOT READY verdict. W7 activation smoke exists; its real-window task is still owed.

The working tree on 2026-10-31 contains uncommitted W22 drill/runtime changes
and a staged resolution of the `section7/app/login/page.tsx` merge conflict.
The local auto-sign-in behavior in that resolution needs deployment review. These
changes are not validated or claimed complete by this plan; coordinate with their
owner and verify the exact resulting revision.
First reconcile W22 evidence and skipped-test reasons — DONE (2026-11-02): all
six trust/recovery gates verified on deployed topology (39/39 drills passed;
partial-write 0/3 in the first batch was a missing-`--evidence` batch-script bug,
corrected to 3/3 in `stage1-repeat-c11723d3`). W22 release decision remains
blank for an authorized owner. Local unit verification: section4 1896 passed /
59 skipped; section1 1203 passed / 63 skipped; ruff clean.

W17 gates are **blocked on W17's owner**: W17's changes (runner job attempts,
evidence-from-seq in `section1/src/harness_identity/{models.py,runner_api.py}`)
are stashed/uncommitted on `w1-engine-tools` (stash@{0}) and must not be touched
until W17's owner commits or hands them over. W17 still needs its documented
remote agent loop and other remaining gates.

Only after W17 gates complete should the Scheduler experiment (W31) start.
