# Decision: Hx is the Harness language surface; Hermes and `.hms` are out of scope

**Date:** 2026-10-31
**Status:** ACCEPTED — in force until amended in writing by the owner
**Decision owner:** Mubashar Ali (repo commit identity; the operator directing this
session). The owner gave this direction explicitly on 2026-10-31.
**Recorded by:** the coding agent implementing the Hx milestones. The agent did not
make this decision and has no authority to change it.
**Related:** [`hx-language-spec.md`](../hx-language-spec.md) §1,
[`hx-execution-architecture.md`](../hx-execution-architecture.md),
[`hermes-agentic-language-plan.md`](../hermes-agentic-language-plan.md),
[`roadmap-3.0.md`](../archive/roadmap-3.0.md) (candidate boundaries),
[`reviews/blocked-item-workorders-2026-10-31.md`](../reviews/blocked-item-workorders-2026-10-31.md)

---

## The question this settles

Two candidate "languages" have been live in this project at once:

* **Hermes** — a separate, proprietary compiler project (present on this machine at
  `/Users/user/Compiler_Project`, **outside this repository**): C++17 lexer/parser,
  a tree interpreter, a bytecode compiler and VM, GC, formatter, gradual types. Its
  own project instructions describe `exec()` and HTTP via `curl` as available
  builtins. Its rights and distribution position are unresolved, and its roadmap
  milestones H0–H7 are proposals, not shipped functionality.
* **Hx** — the Harness-owned dialect derived from the Hermes *plan*: `hxc` (checker)
  and `hxr` (workflow driver) in [`packages/language`](../../packages/language),
  versioned `harness-hx`, moving by the spec's §13 minor-change rule — `0.1.0` when
  this record was written, `0.2.0` at M6, `0.3.0` at M7, `0.4.0` at M8.

Leaving that ambiguity unresolved is not free: the two share vocabulary but not a
parser, so every future feature request risks being read as "make Hermes work here".
This record removes the ambiguity.

## Decision

1. **Hx is the language surface for agent workflows in the Harness.** Authoring,
   checking, execution, evidence and diagnostics are Hx's (`docs/hx-language-spec.md`
   is the normative document). The Harness sections remain the authority: identity,
   policy, execution and confinement in Section 5, orchestration and events in
   Section 4, persistence and replay in Section 1, model routing in Section 3.
2. **`.hms` is out of scope.** No `.hms` file handling, no `.hms` language support,
   no `.hms` editor integration, and no claim that Hx accepts, compiles or is
   compatible with Hermes programs. Verified on 2026-10-31: every `.hms` mention in
   this repository is documentation
   (`CHANGELOG.md`, `docs/hermes-agentic-language-plan.md`, `docs/hx-*.md`,
   `docs/m2-w6-plan.md`, `docs/m2-w7-plan.md`, `docs/roadmap-2026-10-25.md`,
   `docs/tool-diagnostics-protocol.md`, `docs/week-7-plan.md`,
   `docs/reviews/*`); **no tracked source file references it**.
3. **No Hermes-branded wire formats.** Diagnostics stay on Section 5's frozen
   `{path, line, col, kind, message}` contract. A second diagnostic format would be
   a second parser to keep honest.
4. **W7's LSP evidence does not extend to Hermes.** The recorded LSP proof is
   Python; it establishes nothing about `.hms`. Any doc that says otherwise is
   superseded by this record.
5. **Revisiting is a written amendment, not a drift.** See triggers below. Until one
   fires and the owner amends this file, engineering work proceeds as if Hermes
   integration were not planned — because it is not.

## Options considered and rejected

| Option | Why rejected now |
|---|---|
| **Adopt the Hermes toolchain as the Hx runtime** | It is a second general engine whose safety is a promise, not a tested boundary ("a VM is not an OS security boundary"). Its own docs list `exec()` and `curl`; a restricted profile requires inventorying every builtin, import and native path on **both** backends plus differential tests first. That is the plan's phase 0, 1–2 weeks, in a repository that is not this one. |
| **Ship Hx semantics *on top of* Hermes syntax (`.hms` compatible)** | Compatibility with programs nobody has audited, under rights that are not cleared, for a surface the Harness cannot approve. It also forfeits the reason Hx exists: a closed capability vocabulary and host-selected profile. |
| **Keep the ambiguity ("Hermes-inspired")** | Cheapest today, and it already produced confusion: milestone claims and `.hms` claims get conflated, and a Python LSP proof gets cited as Hermes support. |

## Consequences the reader should know

- **Hx owns its own spec and version.** Adding a construct requires code + fixture +
  golden (the M5 discipline). Language growth is a spec change, and the spec version
  is where a reader can see it.
- **`import` is a forbidden name in Hx today** (`FORBIDDEN_AMBIENT` in
  [`capabilities.py`](../../packages/language/src/harness_hx/capabilities.py)
  includes `import`, `exec`, `open`, `curl`, `os`, `net`, …). So when multi-file
  programs arrive (milestone M8), they get a **new** keyword and a digest binding
  across every file — not `import`, and not module loading at runtime.
  M8 landed exactly that way on 2026-10-31: `use`, host-supplied bytes, `HX3015`–
  `HX3019`, and one digest per file in the manifest. `import` is still refused by
  name, now with a message that names what replaced it.
- **Vocabulary stays closed.** Four capabilities (`model.invoke`, `repo.read`,
  `repo.propose_patch`, `tool.test`), five host operations (those plus the pure
  `review.bundle`), three agent operations (`plan`, `propose`, `review`). Growing
  them is a spec amendment, not a bridge method.
- **Nothing may be published on Hermes's behalf by implication.** If Hermes is later
  integrated, that happens under its own license and a distribution decision — not
  because Hx docs mention it.
- **Out-of-scope does not mean unknown.** The Hermes plan
  (`docs/hermes-agentic-language-plan.md`) remains the design record that Hx descends
  from, and where it and the Hx spec differ, the spec's §1 wins.

## Revisit triggers

Any of these puts the decision back on the owner's table. None of them reopens it by
themselves — the agent must surface it and stop:

1. **Rights and distribution are cleared in writing** for the Hermes compiler, with
   the license, attribution and institutional-ownership questions answered.
2. **A real customer requires `.hms`** to run existing Hermes programs — i.e. demand
   for compatibility rather than for the capability.
3. **A second language surface is proposed** for agent workflows (any project, any
   name), which would make "one surface" false.
4. **The Hermes toolchain gets a baseline that would make it usable here**: its test
   suites run green and reproducible, every builtin/import/native path is inventoried
   on both backends, and differential interpreter-vs-VM results exist. Until then the
   phase-0 numbers are unread.
5. **Hx needs a construct its own grammar cannot express** and the fix is a general
   language feature rather than a declared host operation.

## What this decision does not claim

- It does **not** say Hx is mature, production-ready, or a complete language. When
  this record was written the open limits were cost enforcement, mid-run revocation,
  failure replay, no modules, no mutation and no formatter/LSP; M6 shipped the first
  two, M7 the third and M8 the modules — deliberately *not* mutation, which M8 replaced
  with `for … accumulate` and `with` (spec §12). What is still open: the formatter
  (M10a), capability parity (M9) and the language server (M10b), the numeric-arithmetic
  gap M8 documented, and the fact that Section 4 cannot pin a multi-file program until
  `WorkflowRequest` carries a program digest
  ([`hx-execution-architecture.md`](../hx-execution-architecture.md) §6). None of that
  is maturity; the M6–M10 milestones and the human gates below are what decide it.
- It does **not** resolve Hermes rights. Those remain a human/legal gate, unchanged by
  this file; this record only says the Harness will not depend on that resolution.
- It is **not** an independent review. The Hx milestones are gated by their own
  evidence, and a reviewer who did not write them still has to sign.
