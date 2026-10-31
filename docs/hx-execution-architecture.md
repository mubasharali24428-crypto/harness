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
| Lexer, parser with error recovery, AST with positions | implemented | `test_lexer.py`, `test_parser.py` (16) |
| Names, types, effects, budgets, profiles (55 codes `HX1xxx`–`HX7xxx`) | implemented | `test_types.py`, `test_checker_effects.py`, `test_profiles.py`, `test_budget.py` (39) |
| Closed capability and host-operation tables | implemented | `test_contract_vocabulary.py` (9) |
| Policy-request manifest, sorted JSON, digest-pinned | implemented | `test_manifest.py` (14) |
| Section 5 wire compatibility (`{path,line,col,kind,message}`) | implemented | `test_wire_contract.py`, `test_message_stability.py` (14) |
| Golden fixtures a human reviews | implemented | `test_fixtures.py` (60) |
| Rules are load-bearing (seeded faults) | implemented | `test_seeded_faults.py` (7) |
| `hxc check` CLI with profiles and manifest output | implemented | `test_cli.py` (14) |
| `use`, the module loader, and per-module qualified names | implemented (M8) | `test_host_modules.py` (28), spec §5.3 |
| `for … accumulate` and `x with { … }` — a new value without assignment | implemented (M8) | `test_host_modules.py`, `fixtures/valid/update_and_accumulate.hx` |
| The checker's code table and the source agree, both directions | implemented (M8) | `test_code_table.py` (3) — with a shrink-only "no fixture" ratchet |
| Loops in a pure `fn`, mutation, `while`, runtime loading, generics | refused by design | spec §12; `test_language_growth.py` pins the absence |

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
| Secrets found by content, not by key name, on every surface | implemented (M4) | `test_host_model_relay.py` (10) — prompt, log, event, evidence |
| `model.invoke` routed with the declared role; downgrade recorded, never performed | implemented (M4) | `test_host_model_relay.py` |
| The driver IS Section 4's `WorkflowRunner`; its events reach Section 1 | implemented (M2) | `test_host_contract_runner.py` (10); `section4/tests/unit/test_workflow_*.py` (13) |
| `for` over a list, one `budget.steps` per pass | implemented (M5) | `test_language_growth.py` (15) |
| `cost` budget enforced against a host price table (`HXH501`/`HXH502`/`HXH503`) | implemented (M6) | `test_host_cost.py` (15) — priced before the crossing, unpriced refused, exact decimals, parallel reservations |
| Mid-run revocation of a grant, with the revision in the log (`HXH401` `revoked`, `HXH404`) | implemented (M6) | `test_host_revocation.py` (12) — next crossing only, cannot widen the request, handed-in authority verified |
| The driver's code set is closed and each code has one status | implemented (M6) | `test_host_codes.py` (8) — the §13 "closed-set assertion" the spec requires for an added code |
| A recorded failure replays as itself: same code, status, line, message, contract digest | implemented (M7) | `test_host_failure_replay.py` (13) — including the control that removes the failure and expects a refusal |
| Recordings are versioned (`hx.recording/v2`) and an unsettled exchange is refused (`HXH104`) | implemented (M7) | `test_host_failure_replay.py`, `test_host_cli.py` (`--record`/`--replay`, v1 refusal) |
| Evidence states whether the trace is complete and where the answers came from | implemented (M7) | `recording` block: `origin` (live / recording / replay), settled, failures, unanswered |
| The run executes the file set the check was given; a swapped helper is refused (`HXH103`) | implemented (M8) | `test_host_modules.py`, `test_cli_modules.py` — `--program-digest` refuses and `--digest` alone does not |
| `program_sha256` identifies the program, not the checkout | implemented (M8) | `test_cli_modules.py` — the same bytes in two directories give one digest |
| `hxc`/`hxr` resolve `use` the same way, from a root the host chooses | implemented (M8) | `test_cli_modules.py` (9) — default, `--modules DIR`, `--modules none` |

### 4.3 Host adapters

| Adapter | Status | Notes |
|---|---|---|
| `FakeHostBridge` | implemented | deterministic; used by every runtime test |
| `ReadOnlyRepoBridge` (local) | implemented | one root, secret names/suffixes refused, containment enforced, tests/models refused with `HXH403` |
| `SandboxHostBridge` (Section 2 reads + Section 5 `exec_structured`) | implemented (M3) | live against the shipped `LocalSandboxRunner` — Seatbelt on macOS, Landlock on Linux — for one real `repo.read` and one real `verify.tests`; unit-tested against stub sandboxes elsewhere |
| `RecordingBridge` / `ReplayBridge` | implemented | replay refuses any exchange it did not record |
| Model relay (`model.invoke` through Harness routing) | implemented (M4) | `ModelRelay` takes a `ModelRouter` and the agents' declared settings; without a router it fails closed (`HXH403`) rather than answering as itself |

### 4.4 Harness integration

| Integration | Status |
|---|---|
| Sections import only `harness_contracts` | **preserved** — no section imports the driver; it lives in `packages/language` |
| Section 4 engine calls the driver | implemented (M2) — `workflow_runs.py` takes a `WorkflowRunner` and publishes the run's events |
| Events persisted via Section 1 `agent_events` | implemented (M2) — Section 4 publishes, Section 1 persists; ordering is asserted against the contract, not assumed |
| Section 5 confinement used for `verify.tests` | implemented (M3) — live `exec_structured`; a refused path is refused by the same policy check as before |
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

`packages/language`: **367 passed**, `ruff check` clean.

| File | Tests | What it proves |
|---|---|---|
| `test_host_runtime.py` | 24 | end-to-end run; correlation + idempotency; replay without a host; replay refuses unrecorded calls; start-level and per-call denial; `ensure` failure line; schema mismatch/missing/extra field; missing input; each budget; one tool call per spawn; cancellation; redaction; evidence shape; digest mismatch; check refusal; multi-workflow refusal |
| `test_host_boundary.py` | 10 | Bool ≠ Int; optional/`Any`; nominal identity; missing/extra/nested-index failures; unknown schema |
| `test_host_bridge.py` | 13 | call ordering; content-addressed proposals; settled exit code → evidence; streaming-only refusal; unlisted suite; repository protocol; missing adapter; root escape; secret paths; no deadlock inside a running loop |
| `test_host_authority.py` | 9 | deny-by-default; grants; permission mapping; human-only invariant; the driver's permission names are the frozen contract's; corrupt manifest; pure op; digest binding; decision JSON |
| `test_host_cli.py` | 15 | plan/deny/grant/JSON; run + evidence file; denial; missing file; check errors; wrong digest; host refusal; inputs must be an object; price file read and refused-when-unreadable; `--record` then `--replay` after the underlying file is deleted; a v1 recording refused by name |
| `test_host_contract_runner.py` | 10 | the driver is a `WorkflowRunner`; a replay reproduces the digest without reaching the live host; a replay missing an exchange fails honestly |
| `test_host_model_relay.py` | 10 | the secret is absent from the captured prompt, the log, the events and the evidence; an echoed secret is scrubbed on the way back; no router fails closed; a downgrade is recorded once and not retried; a provider error is scrubbed on all three surfaces |
| `test_language_growth.py` | 15 | enums, record construction and `for` each have code + fixture + golden; 500 files under `budget { steps: 5 }` ends as `budget`/`HXH501` with its performed iterations still in the log; no `while`, and nothing loaded while a run goes on |
| `test_host_cost.py` | 15 | the next crossing is priced before it happens; an unpriced operation is refused, not free; the ceiling is spendable to the cent and one cent over stops; no table says `declared-not-enforced`; a bad table crosses nothing; money adds exact where a float would drift; two parallel reservations cannot both break the ceiling; the CLI reads a price file and refuses an unreadable one |
| `test_host_revocation.py` | 12 | a revoked capability denies the next crossing and not a completed one; never-granted says so instead; the revision sits in the log between the crossings; restore cannot widen the request; evidence holds the starting grant and the live one; handed-in authority for another program, or carrying a human-only permission, is refused (`HXH404`) |
| `test_host_codes.py` | 8 | the driver's code set equals the spec's table, every code has exactly one status, statuses come from the closed vocabulary, every code can be rebuilt from a recording, and `HXH000` never surfaces |
| `test_host_failure_replay.py` | 13 | a recorded failure replays with the same code, status, line and message; the contract digest matches for a failing run; the live host is never reached; removing the failure makes the replay refuse instead of inventing; a tampered argument is not a match; a spawned failure keeps its slot; an unsettled call is refused (`HXH104`); v1 is not re-read; a foreign failure code is refused; the file carries no secret; unplayed exchanges are counted |
| `test_host_modules.py` | 28 | `use` resolves by name only; the reader sees `notes.Summary` and nothing else; the same helper runs identically in its own file; a look-alike local schema is refused; a module with an entry point, a nested `use`, a misnamed file and a self-use are each refused by code and named in the right file; the manifest pins every file; a swapped helper is refused by name; `with` leaves the base untouched; `accumulate` records the value each pass started with and still burns a too-small budget; and what did not arrive — no assignment, no `while`, no `import` |
| `test_cli_modules.py` | 9 | `hxc` and `hxr` agree on where `use` is read from; `--modules none` blames the file that asked; the manifest lists every file; the program digest is unchanged by moving the same program to another directory; `--program-digest` refuses a swapped helper, and `--digest` on the entry alone lets it run |
| `test_code_table.py` | 3 | §9.4 and the source agree on every code in both directions, each M8 code has a named fixture, and the "code with no fixture" list may only shrink |
| checker suites | 173 | lexer, parser, types, effects, budgets, profiles, manifest, wire, goldens (incl. the demo programs and the M8 fixtures), seeded faults, growth, the closed driver-code set |

Determinism: every driver test uses an injected clock, an in-memory bridge and no
network, container or model — with exactly two exceptions, both deliberate: §3's
CLI transcript reads files in a temp directory, and
`section5/tests/unit/test_hx_live_host_ops.py` runs the real confined runner. The
live test is skipped, loudly and with the runner's own reason, on a host with no
OS boundary; it never weakens into a fake.

---

## 6. Limitations (security and operational)

1. **The driver is not a sandbox.** It runs in-process with the caller's
   privileges. It bounds *host calls*, not CPU: a pathological `fn` body is
   bounded by `budget.steps` and Python's recursion limit, not by an OS
   boundary. Real confinement stays Section 5's job.
2. **Trust sits in the bridge.** `SandboxHostBridge` is honest about refusing
   what it lacks an adapter for, but it is only as safe as the Section 5 handle
   and allow-listed commands the host gives it. That wiring has been run live
   for two operations (M3); everything else is as safe as the host's policy,
   which this document does not own.
3. **Events are emitted, then published.** The driver calls `emit` and returns
   the evidence; it does not write `agent_events` itself. Section 4 publishes and
   Section 1 persists (M2) — the driver still owns no storage, which is why a run
   has to be given a runner rather than reaching into a database.
4. **Redaction is by shape, not by semantics.** Keys containing `secret`,
   `token`, `password`, `credential`, `private`, `api_key` are masked, and free
   text is scanned for the shapes secrets actually take (PEM blocks, `sk-`,
   `ghp_`, `AKIA`, `AIza`, `xoxb-`, JWTs, `://user:pass@`). A secret in a shape
   nobody has seen yet still gets through: the detector recognises prefixes, not
   entropy, so undetected shapes are a listed gap rather than a claim of
   completeness.
5. **Replay substitutes recorded answers, it does not undo effects.** Since M7 a
   failure replays as the failure it was and an unsettled exchange is refused
   (`HXH104`), so a replay can no longer hide the original outcome behind its own.
   What it still cannot do is reverse a side effect the first run performed, or
   re-derive an answer the host never gave — a run that died mid-call leaves a gap,
   and the recording marks it incomplete rather than filling it in.
6. **No retries, no idempotency beyond keys.** The driver issues each call once
   and records a key; a host that wants exactly-once must implement it against
   that key. This is deliberate — a silent retry of a side effect is the thing
   the evidence bundle exists to prevent.
7. **Cost is enforced against a price list the host supplies — and that list is
   an estimate, not a bill.** Prices are per crossing in USD (`pricing.py`); with
   no table the run is recorded as `declared-not-enforced`. The driver cannot know
   what a provider actually charged, so a deployment that needs billed-amount
   accuracy must settle against Section 3's cost ledger and treat the table as the
   reservation it is. What is guaranteed is arithmetic, ordering and the refusal
   to cross unpriced — not that the number matches an invoice.
8. **Revocation is per capability, and it stops effects rather than work.** A
   revoked grant denies the *next* crossing; already-completed crossings stay, and
   pure computation runs on until the program returns, its budget ends or `cancel`
   is set. There is no preemptive kill of a call in flight: that belongs to the
   host's own cancellation (Section 5's stop path), not to the language driver.
9. **Single workflow per run.** A program with several workflows must be asked
   for one by name; no composition, no calls between workflows.
10. **The two grammars drift risk is real.** Hx and Hermes share names, not a
    parser. The mitigation is scope: Hx adds constructs only with a code, a
    fixture and a golden, and never claims to accept Hermes programs.
11. **`use` is only as trustworthy as the resolver the host hands it** (M8). The
    language names modules and never paths, so no source text can reach outside what
    the host chose — but a resolver pointed at a directory anyone in the deployment
    can write to makes "the checked program is the executed program" a claim about
    that directory. What the driver guarantees is that the bytes verified after the
    check are the bytes that were checked (`assert_checked_source`, every file
    individually) and that an approved `program_sha256` that does not match is
    `HXH103`; where the bytes came from stays the host's policy.
12. **Section 4 cannot pin a multi-file program yet** (M8, and honest about it).
    `run_source` takes `expected_program_digest`, but the frozen `WorkflowRequest`
    has no field carrying it, so an engine-driven run checks and verifies its own
    file set without a caller-supplied pin. Closing this needs a contract change
    (new field + TS mirror + mocks in one commit), not a language change.

---

## 7. Next milestones, each with a gate

| # | Milestone | Gate (all must hold before the next starts) |
|---|---|---|
| **M1** ✅ | Vertical slice: check → authorise → one allow-listed host op → typed validation → evidence; denial, cancellation, budget; replay | `packages/language` suite green; `hxr` exit codes as documented; §3 reproduced |
| **M2** ✅ | Section 4 executes a workflow through `run_source`, and events reach Section 1's `agent_events` | Sections 1/4 suites still pass (1222 + 1933); event ordering + correlation asserted against the contract; a replay produces identical evidence; no section imports anything but `harness_contracts` (`make guard-isolation`) |
| **M3** ✅ | Live Section 5: `SandboxHostBridge` over a real confined `exec_structured`, allow-listed commands from policy | one real read + one real test suite run through the shipped runner; confinement tests unchanged; a refused path is refused for the same reason as before |
| **M4** ✅ | Model relay: `model.invoke` routed by the Harness, roles from the manifest, no forced downgrade | no secret value in any prompt, log, event or evidence (the test asserts on captured prompts); downgrade attempt is recorded, not performed |
| **M5** ✅ | Language growth: `enum` values in source; record construction; loops with a `budget.steps` termination story | each addition ships a code, a fixture, a golden and a test that the closed set changed deliberately (`test_language_growth.py`) |
| **M6** ✅ | The limits bind: `budget.cost` enforced against a host price table; a grant revocable mid-run with the revision recorded | a run stops *before* the crossing it cannot pay for, and an unpriced operation is refused rather than free; no table is announced in the evidence as `declared-not-enforced`; revocation bites the next crossing and cannot widen the request; the driver's code set is pinned closed. 295 language tests, `ruff` clean, `spec_drift` clean, sections 4/5 unaffected — see §9 |
| **M7** ✅ | Failure replay and complete recordings: a recorded failing run reproduces its failure, and an incomplete recording fails closed | replay of a failed run raises the recorded failure, not `HXH403`; a missing or altered record is refused; the recording format is versioned — measured at `3637e3f`+M7: 315 language tests, see §9 |
| **M8** ✅ | Multi-file programs and safe iteration: `use` with a digest per file, `for … accumulate`, and record update (`with`) — deliberately no assignment, which would remove the termination argument | code + fixture + golden per construct; swapping one module file fails closed (`HXH103`) while `--digest` on the entry alone does not; the approved digest survives moving the program to another directory; the termination story still holds with no `while`; spec `0.4.0` — 367 language tests, `ruff` clean, see §9 |

Out of scope until M3 is green: any claim that a run is *confined*. Until M2 is
green: any claim that a run is *audited*. Both are green now; the claims they
gate are limited by §6, not by this table — a run is audited as far as Section 1
persists it and confined as far as the `LocalSandboxRunner` policy goes.

---

## 8. Where the code lives

| Concern | Path |
|---|---|
| Checker, manifest, `hxc` | `packages/language/src/harness_hx/` |
| Driver, bridges, `hxr` | `packages/language/src/harness_hx_host/` |
| Secret detector (prefixes, PEM, JWT, URL credentials) | `packages/language/src/harness_hx_host/secrets.py` |
| Model relay (roles, scrubbing both ways, downgrade record) | `packages/language/src/harness_hx_host/model_relay.py` |
| Price table and cost reservation (M6) | `packages/language/src/harness_hx_host/pricing.py` |
| Recording format, in-flight marker, replay of failures (M7) | `packages/language/src/harness_hx_host/bridge.py` (`BridgeExchange`, `recording_to_json`, `ReplayBridge`) |
| Revocable authority (M6) | `packages/language/src/harness_hx_host/authority.py` (`RevisableDecision`, `assert_checked_source`) |
| Module resolver and the per-reader rename (M8) | `packages/language/src/harness_hx/loader.py` (`load_modules`, `module_program`, `directory_resolver`) |
| `WorkflowRunner` adapter for Section 4 | `packages/language/src/harness_hx_host/contract_runner.py` |
| Section 4 run + publish | `section4/src/harness_orchestrator/workflow_runs.py` |
| Live Section 5 seam | `section5/tests/unit/test_hx_live_host_ops.py` |
| Target workflow | `packages/language/tests/fixtures/valid/fix_issue.hx` |
| Demo programs (§3) | `packages/language/tests/fixtures/valid/{read_one,needs_tests,stops_at_budget}.hx` |
| Language specification | `docs/hx-language-spec.md` (§12.1 is the driver) |
| Original proposal | `docs/hermes-agentic-language-plan.md` |
| Package README | `packages/language/README.md` |
| Hermes conventions and batch status | `/Users/user/Compiler_Project/hermes/UPGRADE_KNOWLEDGE_GRAPH.md` |

---

## 9. The M2–M5 record

What each milestone added, and the check that made it true rather than planned.

**M2 — Section 4 runs a workflow.** `workflow_runs.py` takes a `WorkflowRunner`
and publishes the events the driver emits; `contract_runner.py` makes `hxr`
that runner. It is wired to the frozen `WorkflowStatus` / `WorkflowOperation` /
`WorkflowRequest` / `WorkflowResult` surface (`contracts/workflow.py`), which is
why the contracts package is `1.0.0rc7` — an additive change with the TS mirror
updated in the same commit, not a silent one.

> Gate: section 1 **1222 passed, 63 skipped**; section 4 **1933 passed, 59
> skipped**; `test_a_successful_run_publishes_in_causal_order` and
> `test_the_transcript_is_durable_and_causally_ordered` assert order against the
> contract; `test_a_replay_reproduces_the_digest_without_touching_the_host`
> asserts identity; `test_the_section_imports_only_contracts_and_itself` plus
> `make guard-isolation` (275 src files, no cross-section edge) assert the import
> rule.

**M3 — Section 5, live.** The same `SandboxHostBridge` that unit tests stubbed
now runs against the shipped `LocalSandboxRunner`: one real `repo.read` (content
through streaming `exec`) and one real `verify.tests` (a verdict through settled
`exec_structured`), with the path checked before the command exists and a
forbidden path refused by the same policy as before.

> Gate: `section5/tests/unit/test_hx_live_host_ops.py`, **4 passed** on macOS
> (Seatbelt). In CI it runs under the `section5` matrix job; where the kernel
> offers no boundary the test **skips with the runner's own reason** instead of
> proving something weaker than the product claims.

**M4 — Model relay.** `ModelRelay` turns the manifest's `agent` declarations
into `ModelRouter.call_model` calls (role from source, overridable by the host,
never the reverse), fails closed with `HXH403` when there is no router, and
records a downgrade (`performed: false`) instead of retrying it. One detector
(`secrets.py`) runs on the outbound prompt, the inbound reply, every log line,
every event payload and the evidence bundle — the last two because the old
key-name redaction let a secret through in free text under `input` and in
`outcome.output`.

> Gate: `test_host_model_relay.py`, **10 passed**. The assertions are on the
> *captured* prompt and on `caplog`, not on intent; the report carries
> `promptSha256` and `promptBytes`, never the messages. What is scrubbed is
> reported as a count by kind, never as the value.

**M5 — The language grew by three constructs.** Enum members (`Level.high`,
`HX3013` for an undeclared one), record construction (`Summary(text: "…")`,
with `HX3003`/`HX3004` and a runtime refusal for a wrong field), and `for x in
list` (`HX3014` non-list, `HX4008` in a pure `fn`, `HX5006` without
`budget.steps`). Each came with code + valid fixture + refusal fixture + golden.

> Gate: `test_language_growth.py`, **15 passed**, including the termination
> story demonstrated rather than asserted: 500 files under `budget { steps: 5 }`
> ends as `status == "budget"`, `HXH501`, `"budget.steps (5)"`, with 1–5
> `loop.iteration` events still in the log. The same file pins what did **not**
> arrive: no `while` (it would need a termination proof this language cannot
> offer), no assignment, no modules.

Suite totals after M5: `packages/language` **261 passed**, `ruff` clean.

**M6 — the two limits that were notes in the evidence became checks.** Cost and
revocation were listed as open in every review since M2, and both were the same
kind of gap: the driver recorded a ceiling it never read, and recorded an
authority it never re-asked after the start.

- *Cost* is now reserved before the crossing, against a **price table the host
  supplies** (`pricing.py`: `Pricing` + `CostAccount`). A program cannot name its
  own prices — that would be the ceiling raising itself — so the table arrives as
  `run_source(prices=...)`, `hxr run --prices FILE` or `ContractRunner(prices=...)`
  and is fingerprinted in the evidence. Three decisions carry the milestone: an
  operation the table does not price is **refused, not free** (`HXH502`); a table
  that cannot be built stops the run before anything crosses (`HXH503`), and the
  CLI treats an unreadable price file as a usage failure rather than as "no prices";
  and a run with a ceiling but no table says `declared-not-enforced` in its own
  evidence, because the dangerous failure is an unbounded run that reads as a
  bounded one. Amounts are exact decimals: a `0.30` ceiling must not reject a run
  that spent `0.10` then `0.20`, and the reservation is atomic, so two spawned
  crossings cannot both pass a ceiling that only one of them can pay for.
- *Revocation* is `RevisableDecision` in `authority.py`. The runtime already asked
  `allows()` at every call site, so revocation needed no new check — only an
  authority whose answer can change. It can narrow, never widen: `restore` returns
  `False` for a capability the program did not request, so a run cannot grow its
  ceiling by waiting. Revisions are logged as `grant.revoked` / `grant.restored`
  **between** the crossings they separate, each carrying the host's reason, and a
  denial says whether the capability was revoked or never granted — those are
  different facts and a reviewer should not have to infer which.
- Because handing authority in from outside is now possible, it is **checked, not
  trusted**: a decision minted for a different request, or carrying a capability
  that maps to a human-only permission, is refused with `HXH404`. The human-only
  invariant is asserted against an injected bug, not just against today's table.
- The code set is now pinned: `test_host_codes.py` reads the error classes and
  fails if a code exists that §9.5 does not name, or a code's status moved. §13
  has required that assertion for every added code since M5; it had no test.

Spec and toolchain moved `0.1.0 → 0.2.0` (a minor change under §13: codes added,
no existing code's meaning changed, no program that checked before fails now).
One checker message embedded the version string, so it was reworded and its
golden regenerated — the only golden the regeneration touched.

> Gate: `test_host_cost.py` **15 passed**, `test_host_revocation.py` **12**,
> `test_host_codes.py` **6**, plus the new `cost_ceiling.hx` fixture and its
> golden. Suite total **295 passed**, `make lint-language` clean,
> `scripts/spec_drift.py` clean, `contracts/ts/generate.py --check` clean,
> `make guard-isolation` clean (275 src files, 10 sections), section 4 hx/workflow
> tests **22 passed**, section 5 live host-op tests **4 passed**. Nothing outside
> `packages/language` changed behaviour: with no table and no `authority`,
> `run_source` does exactly what it did before M6 — which is why the pre-existing
> 261 tests still pass untouched.

Suite totals after M6: `packages/language` **295 passed**, `ruff` clean.

**M7 — a recording now tells the truth about failures.** Replay had one honest
limitation and one honest gap: successes replayed, failures did not exist. A run
that died at crossing three replayed as `HXH403` — "nothing recorded" — so the
replay's own complaint replaced the original failure, and a reviewer comparing the
two could not tell a broken host from a missing record.

- `BridgeExchange` carries an `outcome` (`ok` / `failure` / `in-flight`) and a
  code-named failure. Codes travel between builds; classes do not, so a recording
  naming a failure this build does not emit is refused rather than reinterpreted.
- `RecordingBridge` writes the request **before** the host answers and settles it
  by replacing the entry (the dataclass stays frozen: a recorded fact is not
  editable after the fact). A host that disappears mid-call therefore leaves an
  `in-flight` exchange, which is the only honest thing a trace can say about a
  crossing whose result nobody knows.
- `ReplayBridge` re-raises a recorded failure through `errors.from_record`, so the
  replayed run reports the same code, status, message, line and column — and, over
  the contract adapter, the same digest. M2's replay claim was proven for
  successes; it now holds for failures too.
- Removing a failure from a recording does **not** make the replay succeed: it
  refuses with `HXH403`, because the record no longer answers that call. That
  negative control is the difference between "the replay reproduces the record" and
  "the replay does whatever it likes when the record is thin".
- Evidence gains a `recording` block — `origin` (live / recording / replay),
  settled, failures, and the unanswered crossings. "It replayed" now has to mean
  "it replayed up to here", in the artifact rather than in prose.
- The format is declared `hx.recording/v2` and a v1 or unlabelled file is refused
  (`HXH104`, status `refused`): v1's silence meant "no failure happened", and under
  v2 the same silence can mean "no answer arrived". Reading one as the other is
  exactly the quiet reinterpretation a version field exists to block.
- `hxr run --record FILE` / `--replay FILE` make it operable, not just testable.
  The strongest test is the simple one: record a run, **delete the file it read**,
  replay, and the run still answers correctly — proof the reply came from the
  recording and not from a host it could still reach. A replay whose recording
  cannot be read exits `2` instead of quietly running live.
- A recording leaves the process, so it obeys the same law as the evidence: the
  serialised file is scrubbed, and a test proves the token is not in it.

Spec and toolchain moved `0.2.0 -> 0.3.0` (another §13 minor change: one code
added, no existing code's meaning changed, no previously-checking program now
fails). One checker message embedded the version string, so it was reworded once —
that single golden is the only fixture regenerated.

> Gate: `test_host_failure_replay.py` **13 passed**, `test_host_cli.py` **15**
> (five new: record/replay round trip, recorded failure survives the file
> reappearing, v1 refused by name, unreadable recording is a usage failure,
> `--record` and `--replay` are alternatives), `test_host_codes.py` **8** (two new:
> `HXH104` is a refusal, and every code in the table can be rebuilt from a
> recording). Suite **315 passed**, `make lint-language` clean,
> `scripts/spec_drift.py` clean, TS mirror up to date, `make guard-isolation`
> clean, section 4 hx/workflow subset **22 passed**, section 5 live host ops
> **4 passed**. The 261 pre-M6 tests and all 295 post-M6 tests still pass
> untouched — the replay change is additive to the exchange shape, and an all-
> successful recording behaves exactly as it did before.

Suite totals after M7: `packages/language` **315 passed**, `ruff` clean.

**What was still open after M7**, so nobody reads this table as more than it says:
a price is the host's estimate, not a settled bill (§6.7); revocation stops effects,
not computation (§6.8); replay substitutes recorded answers but cannot undo an effect
the first run performed (§6.5); the detector knows prefixes, not entropy; `for` has no
`break`; and there were no modules, mutation or generics — which is what M8 went at.
Each is in §6, §4.2 or spec §12 as a named limit, not a footnote here.

### The M8 record (multi-file programs, and two ways to compute a value)

M8 answered the oldest "planned" row in §4.1 without buying a hole in the boundary
the M1–M7 work had dug.

- **`use` names a module; the host supplies the bytes.** `loader.py` resolves a name
  to source through a mapping, a directory or a callable, and nothing else. A program
  cannot write a path, cannot ask for a module mid-run, and cannot get an `import`
  back by wanting one: `HX4004` still fires, with a message that now says what
  replaced single-file programs instead of a runtime loader.
- **The rename is the safety mechanism.** A used module's declarations are presented
  to the reader as `notes.Summary`, `notes.Level`, `notes.shout` — in the checker's
  tables *and* in the bodies — so one identifier means one thing across the file
  boundary. The consequence tests check rather than assert in prose: a look-alike
  `Summary` declared in the reading file is refused where the helper is called, and a
  value the helper built carries the module's type name at the boundary in both
  directions (`notes.Summary` to a reader, `Summary` in its own file).
- **No-shadowing became load-bearing, so it became a rule.** `HX3025`: a binding,
  parameter, loop variable or accumulator may not reuse a declared name or a used
  module name. Without it the rename above is just a convention that a `let notes = 1`
  can undo.
- **One level deep, types and pure `fn` only, 16 files, 256 KiB.** A module that
  declares a workflow or agent is `HX3019`, uses another module is `HX3017`, is named
  differently than it is used (`HX3016`) or uses itself (`HX3017`) — refused, not
  flattened.
- **The manifest pins each file, and the pin survives a clone.** `sources[]` carries
  the module name, path, digest and size per file; `program_sha256` digests the
  `(module, sha256)` pairs only. Paths were in that digest in the first draft of this
  milestone and are out of it now, because a digest that moves when the same program
  is checked out elsewhere is not an approval an engineer can hand to a runner — the
  test moves the identical files to a second directory and demands one digest.
- **`assert_checked_source` verifies every file, not just the entry.** That closes the
  gap a reviewer would otherwise have to remember: an unchanged `main.hx` with a
  swapped `notes.hx` used to be a program that ran something nobody checked. Now the
  run is `HXH103` with every path it compared, and `--program-digest` (from the
  approved manifest) is the caller's pin. `--digest` alone still pins one file, so the
  test suite says that out loud instead of leaving it as a surprise: with only
  `--digest`, a swapped helper **runs**.
- **`for … accumulate` and `x with { … }` are the two new ways to get a value, and
  neither is assignment.** The accumulator is a binding the loop creates — one
  `budget.steps` per pass as before, the value each pass *started* with recorded in the
  `loop.iteration` event, the name visible only after the loop — and `with` builds a
  new record with the base untouched. The same five-part program under `steps: 3` and
  `steps: 20` ends `budget`/`HXH501` and `ok` respectively, which is M5's termination
  story unchanged. There is still no `while`, no `n = 2` (the parser has no production
  for it: `HX2001`) and no `break`.
- **Diagnostics now say which file they mean, and the goldens record it.** A record
  about a `use` clause that could not be honoured belongs to the file that wrote the
  clause; a record about a module's own contents belongs to the module. `path` is
  written into a golden only when it differs from the fixture's file, so of the 57
  goldens exactly 2 gained one and the rest did not churn.
- **A gap this milestone found and did not paper over.** `+` joins Strings and
  nothing else, and a `List` has no members — so an `accumulate` loop can build a
  report from parts but cannot sum a column. Adding arithmetic is an owner decision
  about operators, not something to slip in beside a new keyword; the limit is in
  spec §12 and the demo programs work around it visibly rather than by magic.
- **Out of scope, deliberately:** `.hms`, Hermes, and any namespace story deeper than
  one level (see the decision record, `docs/decisions/2026-10-31-hx-language-surface.md`).

Spec and toolchain moved `0.3.0 -> 0.4.0` (§13 minor: codes added, no existing code's
meaning moved, no previously-checking program now fails to check — the one message
that changed is `HX4004`'s, and its single golden was regenerated and read).

> Gate: `test_host_modules.py` **28 passed**, `test_cli_modules.py` **9**,
> `test_code_table.py` **3**, `test_fixtures.py` **60** (11 invalid + 1 valid M8
> fixtures with goldens, incl. the "what did not arrive" controls),
> `test_message_stability.py` **7** (cross-file positions and the file-naming rule
> generalised to multi-file programs). Suite **367 passed**, `make lint-language`
> clean, `scripts/spec_drift.py` clean, TS mirror up to date, `make guard-isolation`
> clean (276 src files, 10 sections), section 4 hx/workflow subset **22 passed** —
> `pytest tests/unit -q -k "hx or workflow or runner"`, named because a bare count is
> not reproducible — section 5 live host ops **4 passed**
> (`tests/unit/test_hx_live_host_ops.py`). The 315 pre-M8 tests pass untouched:
> `use`, `accumulate`, `with` and
> the manifest's `sources[]` are additive, a program with no `use` keeps its old
> single-entry behaviour, and the module path is opt-in for every caller.

Suite totals after M8: `packages/language` **367 passed**, `ruff` clean.

**What is still open after M8**: a price is the host's estimate, not a settled bill
(§6.7); revocation stops effects, not computation (§6.8); replay cannot undo an effect
(§6.5); a module is only as trustworthy as the resolver the host hands it (§6.11);
Section 4 cannot pin a multi-file program until `WorkflowRequest` carries a digest
(§6.12); `for` has no `break`; there is no numeric arithmetic; and the formatter,
capability parity and the language server remain — M10a, M9, M10b.
