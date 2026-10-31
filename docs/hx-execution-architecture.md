# Hx execution: architecture decision, verified baseline, capability matrix

**Status:** decided and implemented for one vertical slice (2026-10-30).
**Repos:** `harness` (this one) and `/Users/user/Compiler_Project` (Hermes).
**Contracts:** nothing in `contracts/` changed. No migration, no version bump.

This document answers four questions with evidence rather than intent: what
Hermes and Hx actually are today, which architecture follows from that, what
was built and run, and what is still missing.

---

## 1. Decision

**Hybrid — Hermes stays the general-purpose language; Hx is the agentic surface;
the boundary is a manifest and a bridge, not a grammar bridge.**

- **Hermes is untouched.** It remains a C++17, header-only language with two
  differential-tested backends, a mark-and-sweep collector, namespaced modules,
  a package manager, a formatter, an LSP, and *ambient* operating-system access
  (`exec`, `exec_async`, `getenv`, `list_dir`, `sleep`, plus `lib/http.hms`
  driving `curl`). Nothing in this milestone edits it.
- **Hx is the agentic surface.** Effects are declared (`requires`), the
  capability table is closed, the host selects the profile, budgets are declared
  in source and enforced by the host, and the manifest is a *request*, not a
  grant.
- **The seam is two artifacts.** A `policy-request manifest` (digest-pinned,
  `grants: "none"`, `forgeable: false`) and a `HostBridge` (the single injected
  door out). Authority, limits, evidence and review belong to the host; the
  program owns none of them.

Hx is therefore a **separate language with a shared lineage** — not a dialect
that `hermesc` accepts, and not a front end that lowers to Hermes bytecode:

- *not a dialect*: `hermesc` cannot parse or check an `.hx` file, and Hx source
  never reaches either Hermes backend;
- *not a front end*: no Hx AST is lowered to Hermes bytecode, because the VM's
  ambient natives and GC-managed values are precisely what Hx exists to deny
  the model. Lowering to that VM would import the security model Hx removes.

### The three options, against evidence

| Option | What the code says | Verdict |
|---|---|---|
| **Extend Hermes directly** (add capabilities/effects to Hermes, run agent programs on its VM) | `install_builtins()` already gives every program `exec`, `exec_async`, `getenv`, `list_dir`, `sleep`; `lib/http.hms` shells out to `curl`; the tree-walker and the VM must agree on every semantic (the project's own convention #8/#9). A capability model here would have to *remove* ambient access from both backends without breaking 542 existing tests, and every new effect would need two implementations kept in lockstep. | Rejected. The sandbox would have to be built anyway, and built twice, inside a language with no notion of a principal. |
| **Hx alone** (checker only; someone else executes) | `hxc` proves a program is well formed and emits a manifest. It cannot demonstrate that a declared effect is actually refused when unauthorised, or that a budget stops a run — those are runtime properties. | Rejected as an *end state*: a checker cannot be evidence of execution. |
| **Hybrid (chosen)** | Hermes' language work (types, modules, async I/O, agent stdlib) is real and stays valuable where a general language is wanted. Hx adds exactly the layer Hermes lacks — declared authority, host-enforced limits, reviewable evidence — and executes only the workflow construct set. | **Chosen.** No second engine for general code; one small driver for the subset that must be auditable. |

The two-grammars risk is real and is answered by scope, not by hope: Hx supports
straight-line workflows with `if`/`else`, `let`, `ensure`, `await`, `spawn`,
`fn` and host calls. It has no loops, no mutation, no module system, no struct
literals, no generics. Each of those is a *deliberate* refusal recorded in
`docs/hx-language-spec.md` §12, and each addition has to pass the same gate as
everything else: a code, a fixture, a golden, a test.

---

## 2. Verified baseline (what was actually run)

Documentation was treated as a claim, not proof. Every row below was executed.

| Repo | Command | Result (2026-10-30) |
|---|---|---|
| Compiler_Project `@340361e` (`agent-stdlib-async-io`) | `build/hermes_tests` | **542/542 test cases, 2162/2162 assertions, 0 failed** |
| Compiler_Project | `ctest --test-dir build` | **2/2 passed** (46.7 s) |
| harness `packages/language` | `pytest tests -q` | **219 passed** (153 checker + 66 driver) |
| harness `packages/language` | `ruff check src tests scripts` | clean |
| harness `packages/language` | `hxc check` / `hxr plan` / `hxr run` | exit codes 0/1/2/3/4/5 as shown in §3 |

Not run, and why: `section1`–`section10` suites. This milestone adds a new
package and edits no section; re-running them would report on code that did not
change. The gate for wiring the driver into a section is listed in §9, and that
gate includes those suites.

Uncommitted work was preserved in both repos: `Compiler_Project` is on
`agent-stdlib-async-io` with untracked example files, and this repo carries
pre-existing modifications in `section4`, `section7`, `clients/` and `docs/`
that this work did not touch.

### Inventory of host access in Hermes (the evidence behind the decision)

| Access | Where | Bound by |
|---|---|---|
| `exec(cmd, stdin?)`, `exec_async(...)` | `interpreter.hpp` `install_builtins()` | nothing — any binary, any argument |
| `getenv(name)` | same | nothing |
| `list_dir(path)` | same | nothing |
| `sleep(ms)`, `sleep_async(ms)` | same | nothing |
| `json_parse` / `json_stringify` / `replace` | same | pure; strict JSON, 512-level cap |
| HTTP | `lib/http.hms` → `curl -q --config -` | network reachability; secrets kept off `argv` |

Conclusion drawn from the table, not from opinion: **the Hermes runtime is not a
security boundary.** Its ambient access is a feature for a general-purpose
language and a vulnerability for an agent. Hx does not borrow it. Every effect a
workflow can name is one of four entries in a closed table, and each maps to
exactly one method on an injected bridge.

### What Hx reuses, and what it refuses

| Reused (design debt avoided) | Refused (deliberately) |
|---|---|
| One implementation per semantic (the Hermes convention) | Ambient access of any kind |
| Closed tables, checked at import time | Two backends for one language |
| Diagnostics first: every failure is a record with a position and a stable code | Implicit nullability, implicit `Any` at host boundaries |
| Golden fixtures that a human reads in the diff | Loops, mutation, modules (until they have a termination/story) |
| Names a reader already knows: `module`, `fn`, `schema`, `let`, `if`, `ensure` | Approval verbs (`approve`, `administer`, `billing`, `export`) |

---

## 3. The milestone: an agent workflow, end to end

One program, `fix_issue.hx`, declares four capabilities, two agents, a budget and
a review bundle. The driver takes it from bytes to a reviewed outcome.

```
source ──▶ check ──▶ manifest ──▶ digest bind ──▶ authorise ──▶ run ──▶ evidence
             │            │                          │            │
        HXH101        (frozen)                   HXH401       bridge calls,
        nothing                                  per call      typed, budgeted,
        runs                                     site          recorded
```

What was demonstrated, with the real commands and their real exit codes. Run from
`packages/language`; every program named below is a fixture in the repo, so the
transcript is reproducible as written:

| # | Command | Observed |
|---|---|---|
| 1 | `hxr plan tests/fixtures/valid/read_one.hx` | `requested: repo.read` / `granted: nothing` / `denied: repo.read — requested but not granted by this host`; **exit 3** |
| 2 | `hxr plan tests/fixtures/valid/read_one.hx --grant repo.read` | `granted: repo.read`; **exit 0** |
| 3 | `hxr run tests/fixtures/valid/read_one.hx --grant repo.read --root . --evidence evidence.json` | `status: ok`, output `{"path": "README.md", "text": "# packages/language — Hx…"}`, evidence bundle written; **exit 0** |
| 4 | `hxr run tests/fixtures/valid/read_one.hx --root .` | `[HXH401] the host did not grant every requested capability`; `status: denied`; **exit 3** |
| 5 | `hxr run tests/fixtures/valid/needs_tests.hx --grant tool.test --root .` | `[HXH403] verify.tests is not available in the local read-only bridge; it needs Section 5's confined executor`; **exit 1** |
| 6 | `hxr run tests/fixtures/valid/stops_at_budget.hx --grant repo.read --root .` | `[HXH501] budget.tool_calls (1) exhausted`, attributed to source line 14; `status: budget`; **exit 4** |

Step 3's evidence (`hx.evidence/v1`) contains: source path, `sha256` and byte
count; spec and toolchain versions; profile; workflow; the manifest; the policy
decision (`authority: harness`, `forgeable: false`, granted/denied per
capability); the inputs' digest but never their values; every crossing as
`call.start` / `call.finished` with `runId`, `correlationId`, `operationId` and a
32-hex `idempotencyKey`; usage against declared limits; the outcome; and
`review: {required: true, approved: false, autoMerge: false}`.

The same workflow with a host that can run tests (`FakeHostBridge`) completes all
five crossings — `repo.read`, `model.invoke` ×2, `repo.propose_patch`,
`verify.tests` — and returns the `ReviewBundle` whose patch is a **proposal**
(`patch-<sha256 prefix>`, diff text) and whose evidence is the settled exit code
of the suite. Nothing is merged, approved or applied.

---

## 4. Capability matrix

`implemented` = code exists and is covered by a test that passed today.
`partial` = the code exists but a required condition is unmet (usually: never run
against a live section). `planned` = designed, not built. `unverified` = built,
but no test proves the property.

### 4.1 Hx — language and checker (`harness_hx`)

| Capability | Status | Evidence |
|---|---|---|
| Lexer, parser with error recovery, AST with positions | implemented | `test_lexer.py`, `test_parser.py` (15) |
| Names, types, effects, budgets, profiles (39 codes `HX1xxx`–`HX7xxx`) | implemented | `test_types.py`, `test_checker_effects.py`, `test_profiles.py`, `test_budget.py` (39) |
| Closed capability and host-operation tables | implemented | `test_contract_vocabulary.py` (9) |
| Policy-request manifest, sorted JSON, digest-pinned | implemented | `test_manifest.py` (14) |
| Section 5 wire compatibility (`{path,line,col,kind,message}`) | implemented | `test_wire_contract.py`, `test_message_stability.py` (14) |
| Golden fixtures a human reviews | implemented | `test_fixtures.py` (40) |
| Rules are load-bearing (seeded faults) | implemented | `test_seeded_faults.py` (7) |
| `hxc check` CLI with profiles and manifest output | implemented | `test_cli.py` (14) |
| Loops, mutation, modules, struct literals, generics | planned (refused by design) | spec §12 |

### 4.2 Hx — workflow driver (`harness_hx_host`)

| Capability | Status | Evidence |
|---|---|---|
| Check gate: errors mean nothing runs (`HXH101`) | implemented | `test_host_runtime.py` |
| Digest binding before run (`HXH103`) | implemented | `test_host_authority.py`, `test_host_runtime.py` |
| Manifest → decision, closed denials, human-only permissions never granted (`HXH401`) | implemented | `test_host_authority.py` (9) |
| Decision consulted at each call site, not once at the start | implemented | `test_host_runtime.py` |
| Nominal, closed validation of every host reply (`HXH301`) | implemented | `test_host_boundary.py` (10) |
| Budgets `steps`, `tool_calls`, `tasks`, `duration` enforced while running (`HXH501`) | implemented | `test_host_runtime.py` |
| Cancellation at the next step (`HXH601`) | implemented | `test_host_runtime.py` |
| `spawn` on a bounded pool; one tool call per spawned call | implemented | `test_host_runtime.py` (regression test added) |
| Secret-shaped arguments redacted before any event | implemented | `test_host_runtime.py` |
| Evidence bundle (`hx.evidence/v1`) with correlation and idempotency | implemented | `test_host_runtime.py` |
| Recorded run replays with no live host | implemented | `RecordingBridge` / `ReplayBridge` tests |
| `hxr plan` / `hxr run`, exit codes 0–5, stdout machine-readable | implemented | `test_host_cli.py` (10) |
| `cost` budget enforced | **partial** — parsed and reported, never enforced (no price model in the driver) | `runtime.py` `Budget` |
| Mid-run revocation of a grant | **partial** — the per-call check consults the same immutable `Decision`; revocation means a new decision | `runtime.py` `_require` |

### 4.3 Host adapters

| Adapter | Status | Notes |
|---|---|---|
| `FakeHostBridge` | implemented | deterministic; used by every runtime test |
| `ReadOnlyRepoBridge` (local) | implemented | one root, secret names/suffixes refused, containment enforced, tests/models refused with `HXH403` |
| `SandboxHostBridge` (Section 2 reads + Section 5 `exec_structured`) | **unverified** | unit-tested against stub sandboxes and a stub repository; never run against a live Section 5 container |
| `RecordingBridge` / `ReplayBridge` | implemented | replay refuses any exchange it did not record |
| Model relay (`model.invoke` through Harness routing) | **planned** — adapters take a host-supplied `agents` mapping today | no router call exists in this package |

### 4.4 Harness integration

| Integration | Status |
|---|---|
| Sections import only `harness_contracts` | **preserved** — no section imports the driver; it lives in `packages/language` |
| Section 4 engine calls the driver | planned (M2) |
| Events persisted via Section 1 `agent_events` | planned (M2); today events go to the caller's `emit` callback and the evidence bundle |
| Section 5 confinement used for `verify.tests` | partial (adapter exists; live run is M3) |
| Human approval / review surface | out of scope by design — the evidence says `approved: false` and stops |

### 4.5 Hermes (unchanged by this milestone)

| Capability | Status (per `hermes/UPGRADE_KNOWLEDGE_GRAPH.md` and the code) |
|---|---|
| Two backends (tree-walker + bytecode VM), differential tests | implemented; 542 cases green |
| Mark-and-sweep GC, closures, generators, exceptions | implemented |
| Classes/structs/enums, `match` with guards and array destructuring (#90) | implemented |
| `break`/`continue` (#89), dedicated integer type (#91) | implemented |
| Gradual static type checker, `hermesc check` (#88) | implemented, deliberately flow-insensitive (documented false-positive shape) |
| Namespaced modules (#92), package manager (#56) | implemented |
| Async I/O event loop (#95), agent stdlib `json_*`/`exec*`/`getenv`/`list_dir` + `lib/process.hms`, `lib/http.hms` (#96) | implemented |
| Threads (#55) | pending (needs a thread-safe collector) |
| Register allocation (#67), generational GC (#69), threaded dispatch (#93), inline caching (#94) | pending |
| Formatter, LSP, GUI | implemented |
| Native codegen (JIT/LLVM) | unscoped |
| Capability model, principal, budget enforcement, evidence | **absent by design** — this is what Hx adds |

---

## 5. Tests, and what each one is for

`packages/language`: **219 passed**, `ruff check` clean.

| File | Tests | What it proves |
|---|---|---|
| `test_host_runtime.py` | 24 | end-to-end run; correlation + idempotency; replay without a host; replay refuses unrecorded calls; start-level and per-call denial; `ensure` failure line; schema mismatch/missing/extra field; missing input; each budget; one tool call per spawn; cancellation; redaction; evidence shape; digest mismatch; check refusal; multi-workflow refusal |
| `test_host_boundary.py` | 10 | Bool ≠ Int; optional/`Any`; nominal identity; missing/extra/nested-index failures; unknown schema |
| `test_host_bridge.py` | 13 | call ordering; content-addressed proposals; settled exit code → evidence; streaming-only refusal; unlisted suite; repository protocol; missing adapter; root escape; secret paths; no deadlock inside a running loop |
| `test_host_authority.py` | 9 | deny-by-default; grants; permission mapping; human-only invariant; the driver's permission names are the frozen contract's; corrupt manifest; pure op; digest binding; decision JSON |
| `test_host_cli.py` | 10 | plan/deny/grant/JSON; run + evidence file; denial; missing file; check errors; wrong digest; host refusal; inputs must be an object |
| checker suites | 153 | lexer, parser, types, effects, budgets, profiles, manifest, wire, goldens (incl. the three demo programs), seeded faults |

Determinism: every driver test uses an injected clock, an in-memory bridge and no
network, container or model. The one real-host interaction in this document is
§3's CLI transcript, which reads files inside a temp directory.

---

## 6. Limitations (security and operational)

1. **The driver is not a sandbox.** It runs in-process with the caller's
   privileges. It bounds *host calls*, not CPU: a pathological `fn` body is
   bounded by `budget.steps` and Python's recursion limit, not by an OS
   boundary. Real confinement stays Section 5's job.
2. **Trust sits in the bridge.** `SandboxHostBridge` is honest about refusing
   what it lacks an adapter for, but it is only as safe as the Section 5 handle
   and allow-listed commands the host gives it. That wiring has not been run
   live.
3. **Events are emitted, not persisted.** The driver calls `emit` and returns the
   evidence; it does not write `agent_events`. Section 1 owns persistence and
   Section 4 owns semantics, so this is a wiring gap (M2), not a licence to
   invent a second event store.
4. **Redaction is name-based.** Keys containing `secret`, `token`, `password`,
   `credential`, `private`, `api_key` are masked. A secret pasted into a
   free-text field is not detected; that needs content scanning, which is not in
   this milestone.
5. **Replay covers successes only.** A failed exchange is not recorded, so a
   replay of a failing run re-raises `HXH403` rather than the original failure.
6. **No retries, no idempotency beyond keys.** The driver issues each call once
   and records a key; a host that wants exactly-once must implement it against
   that key. This is deliberate — a silent retry of a side effect is the thing
   the evidence bundle exists to prevent.
7. **`cost` is declared, not enforced.** There is no price model in the driver;
   the number appears in evidence so a reviewer can check it.
8. **Revocation is coarse.** A grant set is fixed for the run; revoking mid-run
   means passing a different `Decision`, which `run_source` does not expose as an
   API.
9. **Single workflow per run.** A program with several workflows must be asked
   for one by name; no composition, no calls between workflows.
10. **The two grammars drift risk is real.** Hx and Hermes share names, not a
    parser. The mitigation is scope: Hx adds constructs only with a code, a
    fixture and a golden, and never claims to accept Hermes programs.

---

## 7. Next milestones, each with a gate

| # | Milestone | Gate (all must hold before the next starts) |
|---|---|---|
| **M1** ✅ | Vertical slice: check → authorise → one allow-listed host op → typed validation → evidence; denial, cancellation, budget; replay | `packages/language` suite green; `hxr` exit codes as documented; §3 reproduced |
| **M2** | Section 4 executes a workflow through `run_source`, and events reach Section 1's `agent_events` | Sections 1/4 suites still pass; event ordering + correlation asserted against the contract; a replay produces identical evidence; no section imports anything but `harness_contracts` |
| **M3** | Live Section 5: `SandboxHostBridge` over a real confined `exec_structured`, allow-listed commands from policy | one real read + one real test suite run in CI (docker); confinement tests unchanged; a refused path is refused for the same reason as before |
| **M4** | Model relay: `model.invoke` routed by the Harness, roles from the manifest, no forced downgrade | no secret value in any prompt, log or event (test asserts on captured prompts); downgrade attempt is recorded, not performed |
| **M5** | Language growth, in this order: `enum` values in source; record construction; loops with a `budget.steps` termination story | each addition ships a code, a fixture, a golden and a test that the closed set changed deliberately |

Out of scope until M3 is green: any claim that a run is *confined*. Until M2 is
green: any claim that a run is *audited*.

---

## 8. Where the code lives

| Concern | Path |
|---|---|
| Checker, manifest, `hxc` | `packages/language/src/harness_hx/` |
| Driver, bridges, `hxr` | `packages/language/src/harness_hx_host/` |
| Target workflow | `packages/language/tests/fixtures/valid/fix_issue.hx` |
| Demo programs (§3) | `packages/language/tests/fixtures/valid/{read_one,needs_tests,stops_at_budget}.hx` |
| Language specification | `docs/hx-language-spec.md` (§12.1 is the driver) |
| Original proposal | `docs/hermes-agentic-language-plan.md` |
| Package README | `packages/language/README.md` |
| Hermes conventions and batch status | `/Users/user/Compiler_Project/hermes/UPGRADE_KNOWLEDGE_GRAPH.md` |
