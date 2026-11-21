# Hermes improvement plan: from basic language to a mature agentic language

**Date:** 2026-11-13 · **Status:** approved direction, not started · **Owner:** Mubashar Ali
**Repo:** `/Users/user/Compiler_Project` (outside the harness repo; this plan lives in the harness docs)
**Part of:** `docs/plans/compiler-hx-hermes-plan-2026-11-13.md` (section I.C)

Assumptions: Hermes is the owner's own language, with no copyright issues (owner, 2026-11-13). The harness-side amendment to the 2026-10-31 decision record is **owner-pending** and will be written by the owner. No Hermes code moves into the harness until that amendment exists.

## 0. Where Hermes is today

Read from `README.md`, `PROJECT_DOCUMENTATION.md`, `AGENTS.md`, and `hermes/include/hermes/interpreter.hpp`. Not yet verified by running the suite.

| Area | State | Source |
|---|---|---|
| Execution | Tree-walking interpreter and bytecode VM, two backends | README |
| Memory | Mark-and-sweep GC | README |
| Types | Gradual static type checker | README, `typechecker.hpp` |
| Tooling | `check`, `run --json`, `time`, `lint`, `fmt`, `lsp`, `install`; REPL and GUI | README, `cli_test` |
| Gaps stated by the README | LSP is diagnostics-only; `fmt` drops comments; lint is a small rule set; package install takes a single file with no manifest; no debugger; no real threads | README §9 |
| Ambient effects | `exec`, `exec_async`, `list_dir`, `getenv`, `sleep`, `sleep_async`; `lib/http.hms` drives `curl` | `interpreter.hpp` ~3081–3151; `lib/http.hms` |
| Tests | README claims 530 passing across 16 test files | README badge, `tests/` (unverified) |

**What blocks agent use most:** ambient effects (anything can run a process or reach the network), diagnostics only readable with a regex, and a formatter that loses comments, which makes model-written diffs noisy.

## 1. Improvement phases

Each phase ends at a gate with evidence committed to the Compiler repo at a named revision.

### H0. Baseline and amendment (owner-led)

- Owner writes the amendment to `docs/decisions/2026-10-31-hx-language-surface.md`, or records that Hermes stays separate. **Owner action.**
- Build the compiler from a clean tree and run every test target. Save pass and fail counts and the raw output.
- Correct the README's claims that the suite is not verified (530 count, "fuzz 400 per run") to match what was run.
- **Gate:** evidence file `docs/briefs/evidence/hermes-h0.md` with counts and revision. README claims match it.

### H1. Capability-gated builtins

- Every ambient builtin (`exec`, `exec_async`, `list_dir`, `getenv`, `sleep`, `sleep_async`, and anything `http.hms` uses) requires a named capability (for example `process.run`, `fs.read`, `env.read`, `net.http`).
- Two profiles: `legacy` (current behaviour, default for existing programs) and `agent` (no capability granted unless the host grants it).
- `lib/http.hms` becomes a capability-gated module, not a free function.
- **Gate:** tests show the `agent` profile refuses each builtin with a structured error; the `legacy` profile output is unchanged across the existing example and test programs.

### H2. Agent profile and typed boundary

- A program in the `agent` profile declares its inputs and outputs with types. Untyped boundary values are a compile error in that profile.
- Values from tools or model output are checked against a declared schema on entry; a mismatch is an error with a position, not a later crash.
- **Gate:** a sample agent program runs under `agent`; an undeclared effect and an untyped boundary value are both refused, each with a test.

### H3. Machine interface

- `hermesc check --json` and `run --json` emit one schema: `{path, line, col, code, severity, message, fix?}`. Codes are stable and listed in a table.
- `hermesc fmt` preserves comments (closes the README gap). Formatting is idempotent.
- `hermesc explain CODE` prints the rule; `hermesc schema` dumps the boundary types and capability list as JSON.
- **Gate:** a script parses every diagnostic from the test corpus without regex; `fmt` round-trips comments on a fixture set; `explain` covers every code.

### H4. Deterministic runs and replay

- Pure computation is deterministic under a fixed bytecode version.
- In the `agent` profile, every external effect (process, file read, network) is written to a run log and can be replayed from it without re-running the effect.
- **Gate:** a recorded run replays to the same output with no external calls; a changed log is detected.

### H5. Language features agents need

Chosen from the 18-item list in the overall plan (section I.D). Only these are in scope for Hermes:

1. Effects in function signatures, so a caller can see what a function may do (ties to I.D.1).
2. Structured error class on every error: `recoverable` or `fatal`, so an agent knows whether to retry (I.D.9).
3. Retry policy declared per effect, with an idempotency key (I.D.8).
4. Explicit approval statement that stops the run until the host answers (I.D.10).

Each needs a spec note and tests. Items 3 and 4 wait until H1 and H4 are green.

**Gate:** each feature has a spec section, a negative test, and a sample agent program that uses it.

### H6. Tooling for agents (after H3)

- Richer LSP: hover and go-to-definition. Agents use the JSON CLI first, so this is later, not first.
- Step-through debugger only if an agent workflow needs it. Not planned now.
- Lint rule engine: a small set of rules shared with the `agent` profile checks.
- **Gate:** LSP hover and definition tests pass; lint rules have one test each.

### H7. Performance (only after a measured need)

- Agent workloads are mostly waiting on processes and the network, so VM speed is not the bottleneck. Do not start this phase without a benchmark that shows the VM is the limit.
- Candidates from the README (register VM, computed goto, inline caches) stay on the list until the benchmark says otherwise.
- **Gate:** a benchmark with before and after numbers, committed.

### H8. Bridge to Hx (last, and only after the amendment)

- Hermes programs that pass H1–H4 can be checked and run through the Hx checker and driver (`hxc`/`hxr`) via the manifest and host bridge.
- **Gate:** one Hermes workflow passes `hxc` and runs under `hxr` with evidence. Blocked until the owner's amendment exists.

## 2. Out of scope for now

- Real parallel threads. Needs a thread-safe collector first; revisit when an agent workload demonstrably needs it.
- Traits and operator overloading. Not needed for agent workflows.
- Non-blocking async I/O with a real event loop. Revisit after H7 shows the need.

## 3. Order

1. H0 (owner amendment in parallel).
2. H1, then H2.
3. H3 and H4 in parallel.
4. H5 after H1 and H4.
5. H6 after H3.
6. H7 only if the benchmark asks for it.
7. H8 last.

Gates, not dates. An item stays `Not started` until its evidence exists at a named revision.

## Not verified

No Hermes build or test has been run for this plan. Every state claim comes from the README, the docs, and the builtin definitions listed above. H0 is the first real check.
