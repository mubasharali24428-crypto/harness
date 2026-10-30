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
