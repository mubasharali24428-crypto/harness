# Hermes as an agentic programming language

Status: proposal, 2026-10-30. This document is a development plan, not a claim that the features exist. The owner states there were no other contributors; record that clarification and resolve any remaining institutional ownership or distribution questions before release. The existing proprietary license still governs distribution. This plan supersedes conflicting technical assumptions in the older Hermes roadmap, subject to project review.

Implementation: the Harness-owned dialect derived from this plan is Hx — spec in [`hx-language-spec.md`](hx-language-spec.md), reference checker in [`packages/language`](../packages/language). Where this plan and the Hx spec differ, section 1 of the spec records what changed and why (host-selected profiles, declared capabilities, typed host boundaries, the frozen Section 5 diagnostic contract, and a manifest that requests rather than grants).

**Decided 2026-10-31:** Hx is the Harness language surface; Hermes and `.hms` are out of scope until the owner amends the record in [`decisions/2026-10-31-hx-language-surface.md`](decisions/2026-10-31-hx-language-surface.md). The phases below remain the reference plan for what maturity requires, but they are not a work order for Hermes integration, and nothing in this file may be cited as evidence that `.hms` support exists or is planned.

## Product goal

Evolve the existing Hermes language into a dependable language for defining agent workflows, backend orchestration and verifiable automation. A Hermes program should describe goals, typed inputs and outputs, agent roles, permitted operations, resource limits, verification and human review. The Harness remains the authority for identity, policy, execution, events and approvals. Hermes is an opt-in surface; users can still use the product without learning it.

First target: a repository maintenance workflow that receives a task, proposes a plan, runs an allowed test, validates its result, and produces a reviewable change. It must not merge automatically or acquire permissions through its own source code.

## Starting point and corrections

* The compiler lives in `/Users/user/Compiler_Project`, outside this repository. Its documentation describes C++17 lexer/parser, tree interpreter, bytecode compiler and VM, GC, formatter, gradual types, CLI and differential tests. Confirm actual current state and test results before committing to these capabilities.
* Harness currently adopts a diagnostic format inspired by Hermes; it does not ship a Hermes toolchain or `.hms` integration. Existing roadmap milestones H0–H7 are proposals, not completed functionality.
* Compiler project instructions explicitly describe `exec()` and HTTP via `curl`. The older roadmap's assertion that the VM has no filesystem, process or network capability is unsafe as a premise. Inventory all builtins, imports, native bindings and transitive paths for each backend before defining a restricted profile. A VM is not an OS security boundary.
* The compiler README and older roadmap disagree about some tool maturity. Baseline the actual implementation, especially LSP, package tooling, type checker and debugging, rather than relying on either description.
* Preserve frozen `harness_contracts`. Any necessary breaking change requires a version bump and updated mocks in the same commit. Use existing APIs first; do not silently change event semantics.

## Design principles

1. **Explicit authority:** source code declares requested capabilities; a separately authenticated Harness policy grants a subset. A program cannot grant itself access, change policy, override forbidden paths or bypass human review.
2. **Typed boundaries:** model output and tool results are untrusted data, decoded and validated against schemas before use. Prompts and external text are never instructions to the runtime.
3. **Replayable decisions:** pure computation is deterministic under a pinned runtime; external effects are recorded and replayed from captured results. A live model call is not deterministic and replay does not repeat it.
4. **Bounded execution:** enforce instruction, time, memory, output, concurrency, tool-call and monetary limits independently; terminate on exhaustion. No forced model downgrade.
5. **Observable and reviewable:** structured diagnostics, provenance, event correlation and evidence for each decision; redact secrets before persistence or model context.
6. **Compatibility:** preserve existing Hermes programs under a versioned legacy profile while developing a separately selected agent profile. Never silently change existing program semantics.

## Proposed language model

The following is illustrative design syntax, not valid Hermes today. Prototype syntax only after reviewing the current grammar and compatibility tests.

```text
workflow fix_issue(input: Issue) -> ReviewBundle {
  requires tool.test, repo.read, repo.propose_patch
  budget { steps: 200; tool_calls: 12; duration: 10m; cost: 2.00 USD }
  agent developer: Agent<ChangeProposal> { role: "developer" }
  let plan = await developer.plan(input)
  let proposal = await developer.propose(plan)
  let evidence = await verify.tests(proposal, suite: "unit")
  ensure evidence.passed
  return review.bundle(proposal, evidence)
}
```

* **Core:** retain functions, modules, data types, control flow and async constructs where their semantics are sound. Add a versioned specification, stable errors, source spans, module resolution and a documented compatibility policy.
* **Agent and workflow declarations:** typed inputs/outputs, roles and objectives; explicit tasks, dependencies, cancellation and checkpoints. Agent declarations are configuration for Harness, not autonomous security principals.
* **Effects and capabilities:** represent model calls, tool calls, repository reads, proposed edits, external network, secret handles and human approvals as explicit effects. Static checking detects missing declarations; runtime authorization independently checks each invocation. Capabilities are scoped, revocable and non-forgeable; declarations request, never confer authority.
* **Structured concurrency:** define parent/child lifecycle, bounded fan-out, cancellation propagation, join behavior, timeouts and deterministic ordering of recorded outcomes. Enforce existing Harness limits instead of introducing a competing agent tree.
* **Verification:** `assert`/`ensure` for program invariants, schemas for external results, named verification steps, evidence references and review gates. A successful test is evidence, not proof of correctness; an agent cannot approve its own output.
* **Failures:** typed failures for denial, timeout, budget exhaustion, invalid output, tool error and cancellation; define retry eligibility, maximum attempts, backoff and idempotency. Never automatically retry a side effect without an idempotency guarantee.
* **Secrets:** use opaque handles resolved only by authorized services; never expose values to Hermes source, logs, event payloads or model context. Capability denial is the default.
* **Packages:** start with a small audited standard library. Pin dependency versions and hashes; specify provenance and lockfiles before enabling third-party packages. No dynamic installation in restricted runs.

## Architecture and integration

```text
Hermes source -> lexer/parser -> type/effect checker -> versioned IR
                                            |-> diagnostics and policy request
IR -> confined Hermes worker -> narrow typed host bridge -> Harness services
                                    |                      |-> Section 4 orchestration
                                    |                      |-> Section 5 execution policy
                                    |                      |-> Section 1 identity/audit/event delivery
                                    +-> recorded results and evidence -> replay
```

The restricted worker runs under Section 5 confinement, with no ambient credentials, no unrestricted filesystem, shell, process or network access. A separate host bridge checks authenticated run identity, scoped policy, forbidden paths, per-operation budgets and schema for every request. It uses existing Section 4 semantics and Section 1 event persistence/replay; it must not create a parallel source of truth. Effect requests carry run ID, correlation ID, operation ID and idempotency key as supported by existing contracts. Define any adapter-specific data privately; evolve shared contracts only through their versioning process.

Pin source digest, compiler/runtime version, profile, module lockfile, policy snapshot identifier and input digest in execution evidence. Persist permitted, redacted effect requests and results with ordering and causal IDs. Replay substitutes recorded results, rejects mismatches and never invokes real external effects. Clearly mark incomplete recordings and nondeterministic inputs.

## Delivery phases and gates

Estimates are planning ranges for a small experienced team, not commitments. Reassess after the baseline; phases can overlap only after their dependencies are understood.

### 0. Baseline and scope (1–2 weeks)

* Inventory current Git state, existing uncommitted work, build configuration, compiler tests and supported platforms. Read the compiler's upgrade knowledge graph and conventions before implementation.
* Run existing CMake/CTest and sanitizer suites; record actual pass counts and failures. Map every builtin and import path for both backends, including indirect process and network access, resource-limit coverage and cancellation behavior.
* Map Harness APIs, event contracts, model routing, approvals and Section 5 execution rules. Record the owner's authorship clarification and any outstanding institutional or release questions separately.
* Deliver a capability matrix, threat model, existing-language compatibility corpus and prioritized backlog. Gate: reviewers can trace all host entry points and reproduce baseline tests; no safety or readiness claims without evidence.

### 1. Language specification and compatibility (2–4 weeks)

* Specify lexical grammar, values, scoping, functions, modules, errors, async behavior and existing backend equivalence; characterize implementation gaps with executable examples.
* Specify agent profile, effect model, type/schema rules, workflow lifecycle and migration/versioning. Start with a small vertical slice, not every proposed keyword.
* Implement version/profile selection and structured, source-located diagnostics, with clear stdout/stderr separation and negative fixtures.
* Gate: old corpus remains compatible under legacy profile; specification examples and deliberately invalid programs have stable expected results across supported backends.

### 2. Restricted runtime and capability bridge (3–6 weeks)

* Remove or disable unsafe ambient builtins and module loading in the agent profile for **both** execution paths. Deny unknown functions and imports. Run worker inside Section 5 confinement even when language restrictions are enabled.
* Build a narrow bridge using authenticated context, scoped grants, explicit policy decisions and validated arguments/results. Do not pass raw credentials or unrestricted handles into the VM.
* Enforce limits with host-level deadlines and process memory controls in addition to VM instruction accounting. Address GC roots, native calls, blocking operations and cancellation in both backends.
* Gate: adversarial tests show denied direct and indirect process, file and network access, forbidden-path denial, timeout and memory termination, no privilege escalation, and no secret leakage. Security review documents residual risks and supported platform boundaries.

### 3. Agent workflows and typed effects (4–6 weeks)

* Implement workflow and agent definitions, typed model/tool responses, explicit effect declarations and a minimal set of operations for planning, reading, proposing changes and running allow-listed tests.
* Map lifecycle, roles, events and review to existing Harness contracts and services. Expose cancellation and failure as typed outcomes; cap retries, fan-out and cost. Route models through existing selection policy without forced downgrade.
* Gate: the target repository workflow runs end to end, policy denials are visible and cannot be overridden from source, outputs are schema checked, and a human can review the proposed changes and evidence. No automatic merge.

### 4. Verification and replay (3–5 weeks)

* Build verification primitives and evidence bundles, including checks for schema, invariants, tool results and review status. Integrate Section 5 test execution and existing review workflow.
* Record and replay effect traces, including failures and cancellations, under pinned versions. Define redaction, retention, authorization and incomplete-trace behavior; reject changed source, policy or invocation mismatches explicitly.
* Compare interpreter and VM on pure programs and on a fake deterministic host. Combine golden tests, property/fuzz tests and independent expected results; periodically inject faults to verify detection.
* Gate: captured runs reproduce control flow without live effects; missing or altered trace entries fail closed. Seeded compiler and workflow faults are detected, with evidence that the tests are capable of catching them.

### 5. Developer experience (3–5 weeks)

* Extend `hermesc` with profile-aware check, run, format, lint, inspect and machine-readable diagnostics. Define exit codes and CLI compatibility.
* Build incremental editor support and a language server only after a grammar and symbol model are validated. Provide hover, definitions, references, rename and diagnostics with explicit feature limitations. Integrate highlighting and toolchain detection in Harness.
* Deliver examples, language reference, migration guide, local fake host and a debugger or trace viewer with honest replay semantics.
* Gate: a developer can author, check, debug and run the target workflow from a clean install; editor tests exercise a real `.hms` project and invalid edits.

### 6. Production hardening and release (4–8 weeks)

* Add reproducible builds, signed/versioned artifacts, dependency and license inventory, compatibility matrix, upgrade/rollback procedure, resource telemetry and operational runbooks. Do not introduce networked package execution without separate review.
* Run load, isolation, fuzz, fault-injection, recovery and supply-chain tests; evaluate supported OS/container environments and platform-specific controls. Confirm release and distribution rights and terms.
* Gate: independent security review, incident and rollback exercises, documented SLOs and limitations, passing conformance fixtures and a release owner sign-off. Publish only the components and rights explicitly selected by the owner.

## Verification matrix

| Layer | Required evidence |
| --- | --- |
| Lexer/parser | Golden valid and invalid syntax, recovery, source spans, malformed input fuzzing |
| Types/effects | Missing or excessive capabilities, scope mismatches, schema failures, legacy compatibility |
| Runtime | Both backends against fixed results, differential and mutation tests, GC/sanitizer checks |
| Security | Denied ambient operations, indirect import bypasses, forbidden paths, bridge authorization, redaction |
| Orchestration | Cancellation, retries, idempotency, fan-out, budget races, approval and no-auto-merge |
| Replay | Recorded successes/failures, ordering, version mismatch, missing records, no live effects |
| Integration | Golden `harness_contracts` fixtures; existing section tests; end-to-end confined workflow |
| Operations | Crash recovery, limits under load, upgrade/rollback, artifact provenance |

Section 1's prescribed pytest and Ruff commands remain required before a PR; run relevant compiler and affected section suites as well. Never claim the plan or a prototype is production-ready solely because tests pass.

## Decisions to settle during design

* Which existing Hermes constructs support workflow authoring cleanly, and which require syntax changes? Prototype against the current parser before freezing syntax.
* Whether both backends support all agent effects in production or one is the supported runtime while both remain test oracles. Any divergence must be explicit and tested.
* Exact schema language and effect typing, including whether dynamic values require runtime checks or are forbidden at privileged boundaries.
* What data is retained for replay, for how long, and which authorized roles can inspect it; model inputs may contain sensitive data even after redaction.
* Distribution and licensing strategy, institutional ownership evidence where applicable, and the release boundary between language tooling and proprietary integration.
* Target environments, throughput and latency goals, team capacity and release schedule after baseline measurement.

## Definition of done

Hermes has a published, versioned specification and a documented agent profile; a maintained compiler and developer toolchain; policy-controlled integration with Harness; bounded execution under confinement; typed effects, verification and human review; reproducible trace replay; compatibility and security test evidence; and operational ownership. Completion is established by the phase gates and independent review, not by the existence of syntax or a demo.
