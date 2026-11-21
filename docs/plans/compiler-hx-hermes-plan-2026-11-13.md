# Improvement plan: harness compiler, Hx workflow language, Hermes

**Date:** 2026-11-13 · **Status:** approved direction, not started · **Baseline:** branch `w28-s1-delegate`, HEAD `8836dfa`
**Reads with:** `FUTURE PLAN 2.0.md` (§4.4, §4.7, §9.2, §9.3), `docs/hx-language-spec.md` (§12), `docs/hx-execution-architecture.md`, `docs/decisions/2026-10-31-hx-language-surface.md`, `docs/hermes-agentic-language-plan.md`

Assumptions: the three things to improve are (1) the harness-native verifier ("compiler"), (2) the Hx workflow language and its runner `hxr`, and (3) Hermes, the owner's own language at `/Users/user/Compiler_Project`. The aim is a language that agents can use efficiently, which Section I.D defines.

## 0. Decisions taken (2026-11-13)

| # | Question | Answer |
|---|---|---|
| 1 | What is "compiler"? | Harness-native verifier (Track A), not the Hermes VM. |
| 2 | Hermes in or out? | Owner says Hermes is theirs and there are no copyright issues. The 2026-10-31 record still says out of scope until amended in writing. **Pending: written amendment.** |
| 3 | Snippet language | Python first. |
| 4 | First limit | Cost enforcement first; resume after failure later. |

**Current state of cost (checked 2026-11-13):** already enforced by M6 when the host supplies a price table (`hxr --prices`). Without a table, the run is recorded as `declared-not-enforced`. 23 cost and budget tests pass. The remaining gaps are (a) fail closed when `budget.cost` is declared with no table, and (b) settle the reservation against actual spend (`CostAccount.settle` is a no-op). FP2 §4.4 and spec §12 still say "not enforced" and need correcting.

---

## I. Improvement plan

Each item has a gate: a measurable result with evidence in the repo at a named revision. Nothing here is "done" until its gate passes.

### I.A Compiler: harness-native verifier, made mature

Today it is a plan (Track A below) over a sandbox that is built and tested but not offered to the model as a tool.

1. **One verifier contract.** A single `run_check` tool with allowlisted commands, the Section 5 diagnostic shape, and an evidence record (command, exit code, input hash, duration). Gate: golden-fixture tests in `section5/tests/`.
2. **Simple-code execution (Python first).** Snippet runs with no egress, read-only workspace plus a temp dir, time and output caps, and fail-closed tests for network, out-of-workspace reads and `exec`. Gate: all three negative tests fail closed.
3. **Structured diagnostics with fixes.** Every diagnostic gets a stable code, a position, and an optional machine-applicable fix, so the agent can apply it without a model call. Gate: `hxc` and `run_check` emit the same shape; a fix applied from the diagnostic makes the check pass.
4. **Verification is a result, not a claim.** The runner records what ran. A model-supplied "passed" is ignored. Gate: negative test (FP2 §4.7).
5. **Cache by content hash** so unchanged inputs do not rerun. Gate: a second identical run is served from cache with its evidence cited. Defer until measured need.
6. **Measure model calls saved.** Fixed set of 10 simple tasks, calls per task before and after. Gate: count drops and pass rate holds.

### I.B Hx workflow language: faster and fuller

Goal: the same workflows run with less overhead and can recover from failure.

**Speed (measure first; no speed claims without numbers):**
1. **Baseline.** Time `hxc` and `hxr` on the existing valid fixtures, and time each phase (parse, check, authorise, run, record). Gate: a benchmark file with per-phase numbers at the baseline revision.
2. **Check once, reuse by digest.** The program digest already exists, so a checked program with the same digest skips re-parsing and re-checking. Gate: a warm run is measurably faster and produces identical evidence.
3. **Batch recording.** Write evidence events in batches with an fsync at run end and at each external effect, not one write per event. Gate: same evidence content, fewer writes, and crash-safety test still passes.
4. **Keep the hot path small.** No model or network call is added to the check path. Gate: a check of a 40-step program stays under a budget set from the baseline.

**Limits (close in this order):**
5. **Cost, fail closed.** Declared `budget.cost` with no price table is refused with a clear code, not run as `declared-not-enforced`. Gate: negative test; existing cost tests updated. *(Decision pending: confirm this breaking change.)*
6. **Cost, settle actual spend.** Reconcile the reservation with actual cost from Section 3's ledger; `settle()` does the work. Gate: a run where actual cost is lower releases the difference; a run where it is higher is recorded and stops at the next crossing.
7. **Resume after a failed step.** Record host results so a failed run resumes without repeating paid or external effects. Gate: kill-and-resume test gives identical final evidence. *(Later, after 5 and 6.)*
8. **Verification as a construct.** A `verify` block that requires executor evidence before a step can be marked done (ties to I.A.4). Gate: a workflow with a model-only "passed" fails to complete.
9. **Golden programs.** test-and-report, fix-issue skeleton (no merge), review bundle. Gate: `hxc` passes all three; `hxr` runs each with a fake host.

**Stays out:** a general Hx interpreter, `while`, and assignment. Spec §12 rules them out; changing that needs a new spec version and an owner decision.

### I.C Hermes: from basic to a mature agentic language

**Full plan:** [hermes-improvement-plan-2026-11-13.md](hermes-improvement-plan-2026-11-13.md) (phases H0–H8). Owner amendment of the 2026-10-31 record is **owner-pending**; H0 starts when the owner writes it. The summary below is kept for reference.

Hermes today: a C++17 language with a tree interpreter, bytecode VM, GC, formatter, LSP, gradual types and modules. It has ambient OS and network access (`exec`, `exec_async`, `getenv`, `list_dir`, `sleep`, `curl`-based HTTP). Those are the first things to change for agent use.

1. **Baseline.** Build, run its test suite, save pass counts and output as evidence. Gate: evidence file in `docs/briefs/evidence/`. Note the state of LSP, package tooling, type checker and debugger from tests, not from the README.
2. **Capability-gated builtins.** Move every ambient builtin behind an explicit capability in the language itself, and make the default profile refuse them. Gate: a restricted profile refuses `exec`, `curl` and `list_dir`; the legacy profile keeps current behaviour.
3. **Agent profile.** A separately selected profile for workflows: no ambient effects, typed tool results, budgets built in. Gate: a sample agent program runs under the profile and is refused if it tries an undeclared effect.
4. **Machine-readable interface.** Diagnostics, types and the formatter output available as JSON for agents, not only through LSP. Gate: a script parses every diagnostic without regex.
5. **Gradual to required types at the agent boundary.** Typed inputs and outputs are required in the agent profile. Gate: untyped boundary values are a compile error in that profile.
6. **Bridge to Hx** only after items 2 and 3 pass and the rights record is amended. Gate: Hermes workflow checked and run through `hxc`/`hxr`.

Do not link or bundle the VM into the harness until the amendment and item 2 are done. The Hermes docs say a VM is not an OS security boundary.

### I.D Language features an agent needs for efficient work

These are the features a language should have so that an agent finishes tasks with the fewest model calls and the fewest wrong steps. Status is from reading the repo, not from running it.

| # | Feature | Why it saves agent work | Status | Priority |
|---|---|---|---|---|
| 1 | **Declared effects and capabilities** | The agent knows what a step may touch without reading the code. | Hx has it (`requires`). Hermes does not. | High |
| 2 | **Typed boundary for tool and model output** | Bad data fails once, at the boundary, with a position, not deep in a run. | Hx has it (nominal schemas, `HX3001`). | High |
| 3 | **Bounded execution (steps, calls, tasks, time, cost)** | A stuck agent stops by itself and cannot run up spend. | Hx has all five; cost needs fail-closed and settlement (I.B.5–6). | High |
| 4 | **Stable, structured diagnostics with machine-applicable fixes** | The agent repairs the error without a model call. | Frozen shape exists; fixes do not. | High |
| 5 | **First-class verification** (`verify` / evidence required for "done") | Done means executor evidence, not a claim, so there is no re-asking. | Gap (I.B.8). | High |
| 6 | **Dry run / plan mode** that prints the effect graph and budget before any effect | The agent and the owner see the cost and risk before approving. | Gap. | High |
| 7 | **Checkpoint and resume** | Long tasks survive failure without repeating paid effects. | Gap (I.B.7). | Medium (after cost) |
| 8 | **Retry policy declared per effect**, with idempotency keys | Safe retries; the agent does not invent its own loop. | Gap. | Medium |
| 9 | **Recoverable vs fatal error classes** | The agent knows whether to retry or stop. | Partial (error codes exist; the class is not in the contract). | Medium |
| 10 | **Explicit human approval step** as a language construct | Human gates are visible in the source, not in prose. | Gap; authority/revocation exists in host. | Medium |
| 11 | **Deterministic replay of external results** | Debugging and audit show exactly what happened. | Hx has it (M7 failure replay). | High |
| 12 | **Provenance on every value** (stable IDs, source step) | The agent can cite where a value came from. | Partial (evidence events). | Medium |
| 13 | **Data labels / taint** (secret, restricted, untrusted-model-output) | Stops secrets and untrusted text reaching prompts or logs by construction. | Gap (FP2 §2 privacy invariant). | High |
| 14 | **Parallel fan-out and join with deterministic merge** | Independent work runs at once and merges in a fixed order. | Partial (spawned effects; merge order not specified). | Medium |
| 15 | **Small, stable, token-cheap syntax + canonical formatter** | Fewer tokens per program, and one way to write it, so diffs are clean. | Hermes has a formatter; Hx formatter not checked. | Medium |
| 16 | **Self-description**: `explain CODE`, schema dump, capability list as JSON | The agent looks up the rule instead of guessing it. | Gap. | Medium |
| 17 | **Versioned contracts with forward-only migrations** | An agent's old programs keep working across releases. | Hx has version rules (spec §13); Hermes does not. | Medium |
| 18 | **Sandboxed evaluation of expressions** with the same limits as workflows | Simple computations need no sandbox round trip. | Gap (ties to I.A.2). | Low |

**The optimum, in one line:** every step declares what it may touch and what it costs; every value is typed at the boundary; every failure is structured and says whether to retry; "done" needs executor evidence; and the whole run can be checked, planned and replayed before and after it runs.

**Order for these:** 1, 2, 3, 4, 5, 11, 13 first (high priority and mostly present). Then 6, 8, 9, 10. Then 7, 12, 14, 15, 16, 17. Leave 18 until the sandbox is proven.

---

## II. Execution tracks (reference)

The improvement items above map onto three tracks that run in parallel where they do not depend on each other.

- **Track A, verifier:** I.A.1–6.
- **Track B, Hx:** I.B.1–9. B1 is the owner's limits review.
- **Track C, Hermes:** I.C.1–6. Items C1 and C2 can start once the rights amendment is written; C3 is the restricted profile.

### Sequencing and gates

1. Confirm cost fail-closed (I.B.5) and the Hermes amendment. No code before these.
2. Track A I.A.1–2, and Track B I.B.1 (baseline) in parallel.
3. Track B I.B.2–4 (speed), then I.B.5–6 (cost).
4. Track A I.A.3–6 and Track B I.B.8–9.
5. Track C after the amendment: I.C.1–3.
6. Resume (I.B.7) after cost.

Gates, not dates. An item stays `Not started` until its evidence file exists at a named revision (FP2 §0.3).

## III. Relation to the UI plan

Track A's diagnostics and evidence records are what the run workspace (U2–U3 in `docs/plans/ui-overhaul-plan-2026-11-13.md`) should show per step, so the UI and this plan share one data shape. Do not design the UI around Hermes.

## Not verified

Nothing in this plan has been run beyond the cost and budget tests noted in section 0. The speed items have no measurements yet. Hermes build state and test counts are unknown until I.C.1 runs.
