# Hx — the Harness language specification (v0.2.0)

> **Status:** prototype, version `0.2.0`. The reference implementation lives in
> [`packages/language`](../packages/language): `harness_hx` (console script
> `hxc`, the checker) and `harness_hx_host` (console script `hxr`, the workflow
> driver). This document describes what they accept, reject and execute — not an
> aspiration. Where a rule is not implemented, it is listed under
> [Not in 0.2](#not-in-02) rather than implied.

Hx is the Harness-owned dialect of Hermes. The proposal in
[`hermes-agentic-language-plan.md`](hermes-agentic-language-plan.md) and the
H0–H7 ladder in [`roadmap-2026-10-25.md`](roadmap-2026-10-25.md) define the
product claim; Hx is the language those claims require once the unsafe parts are
removed. It keeps Hermes' shape — a real grammar, typed values, effects written
in the source — and replaces the parts that were never true: ambient host
access, implicit authority, unbounded execution.

`hxc` checks; `hxr` runs one workflow of the subset in §12.1 through an
injected host bridge, inside the declared budget, and records evidence. Neither
contains an interpreter or VM for arbitrary Hx, and neither contains a sandbox:
the engine (`section4`) and the confined worker (`section5`) already own those.
The driver is deliberately the smallest thing that can prove a capability was
requested, authorised, used once, checked and recorded.

---

## 1. What changed from the Hermes proposal

| Proposal | Hx | Why |
|---|---|---|
| A Hermes compiler with a VM that "has no fs/network" | A checker plus a workflow driver; confinement stays in `section5`, orchestration in `section4` | Absence of features in a new VM is a promise; the existing confined worker is a tested boundary. A second general engine would need differential tests before either could be trusted, so the driver executes only the workflow subset. |
| Ambient capabilities ("the agent can read files") | `requires` lists a closed vocabulary; every effect names its capability | Authority read off the source is reviewable; authority read off a runtime is not. |
| The program decides its privileges | The **host** selects the profile and resolves policy; source `profile` is rejected (`HX6002`) | A program that grants itself authority is the failure mode the language exists to prevent. |
| Unbounded loops/agents | `budget { steps; tool_calls; tasks; duration; cost }`, all bounded | Termination is a policy question the host must be able to answer before running. |
| Outputs are "whatever the tool returned" | Host results are **nominal**: the program must declare the schema it expects (`HX3001`) | Untrusted data entering typed code should fail at the boundary, once, with a position. |
| A Hermes-branded diagnostic format | Section 5's frozen `{path,line,col,kind,message}` on stderr | One wire contract for all tool output; a second format is a second parser to keep honest. |

The name is deliberately different. The proposal's compiler rights and
versioning are not settled (see roadmap H0), and a dialect that owns its spec,
its effect table and its diagnostics does not depend on that settlement. This is
now a recorded decision, not a stylistic choice: Hx is the Harness language
surface and Hermes/`.hms` is out of scope until the owner amends
[the decision record](decisions/2026-10-31-hx-language-surface.md).

---

## 2. Design laws

1. **Authority is declared, never ambient.** A capability a workflow does not
   list is an error at the call site (`HX4001`), not a runtime refusal.
2. **The host chooses the profile.** `core` and `agent` are selected by the
   caller of `hxc`; the source cannot (`HX6002`).
3. **Effects are awaited.** An effect call that is not awaited, or passed
   straight into another call, is rejected (`HX4005`). Spawning is the only way
   to overlap work, and a spawned task must eventually be awaited (`HX4007`).
4. **The vocabulary is closed.** Capabilities, host operations, agent
   operations, budget keys and ambient names are fixed tables in
   `harness_hx/capabilities.py`. A new name requires a code change, a fixture
   and a golden diff — it cannot arrive through a typo.
5. **`Any` is a checker escape hatch, not a second type system.** It silences a
   comparison; it never appears in a manifest permission mapping.
6. **Diagnostics are positions first.** Every record is one line, one code, one
   message, at the token that caused it. Recovery keeps going so one run reports
   every problem a reviewer has to fix.
7. **The manifest is a request.** `grants: "none"`, `forgeable: false`,
   `approval: "required-for-review"`. A host that reads it must still
   authenticate, resolve team policy and check every call.

---

## 3. Lexical structure

### 3.1 Source units

A program is one UTF-8 file with the suffix `.hx`. There are no includes,
imports or modules: `import` is an *ambient* name and is rejected by name
(`HX4004`) with a message saying programs are single-file.

### 3.2 Comments

```hx
// line comment
/* block comment, may span lines */
```

An unterminated block comment is `HX1003`; an unterminated string is `HX1002`.

### 3.3 Identifiers and keywords

Identifiers match `[A-Za-z_][A-Za-z0-9_]*`. These words are reserved and may not
be used as identifiers:

```
module schema enum fn agent workflow requires budget let ensure return
if else await spawn true false and or not
```

`profile` is **not** a keyword: a source line beginning `profile <name>` is
recognised only to reject it (`HX6002`), with a message naming the host.

### 3.4 Literals

| Form | Examples | Notes |
|---|---|---|
| Integer | `0`, `42`, `1000000` | Non-negative; a leading `-` is not part of the literal. |
| Float | `2.00`, `0.5` | Used for `budget.cost` amounts. |
| String | `"README.md"`, `"a\"b\n"` | Escapes: `\"`, `\\`, `\n`, `\t`, `\r`. Anything else is `HX1005`, reported at the backslash. |
| Boolean | `true`, `false` | |
| Duration | `250ms`, `90s`, `10m`, `24h` | No space between number and unit; units are `ms`, `s`, `m`, `h`. |
| Money | `2.00 USD`, `0 USD` | Only inside `budget`; the amount is a decimal and the currency is a three-letter uppercase word. Hx accepts **only** `USD` (`HX5004`), and the driver enforces the amount against the host's price table (§8). |
| List | `[1, 2, 3]`, `[]` | Element types must agree (`HX3002`). |

### 3.5 Punctuation and operators

```
->  ==  !=  <=  >=  {  }  (  )  [  ]  ,  :  ;  .  <  >  =  +  ?  -  *  /
```

`-`, `*` and `/` are reserved punctuation but have no expression meaning in
0.2.0; `?` marks an optional type. An unexpected character is `HX1001`, and the
lexer resumes at the next token so later declarations still get checked.

---

## 4. Types

```
Type      = Name [ "<" Type { "," Type } ">" ] [ "?" ] ;
```

| Type | Meaning | Notes |
|---|---|---|
| `Int`, `Float`, `String`, `Bool` | primitives | `Int`/`Float` may be ordered; `+` is String-only. |
| `Duration`, `Money` | budget units | Declarable, but no arithmetic is defined for them. |
| `Any` | checker escape hatch | Compatible with everything, in both directions. |
| `List<T>` | ordered collection | Exactly one type argument (`HX3008`). |
| `Map<K, V>` | keyed collection | Exactly two type arguments (`HX3008`). |
| `Agent<T>` | a declared agent that returns `T` | Only in an `agent` declaration; anywhere else `HX3001`. |
| `T?` | `Optional<T>` | A present `T` may stand where `T?` is expected; the reverse is `HX3002`. |
| schema name | nominal record | Fields are checked on member access (`HX3004`); a record is built by naming every field (`Summary(text: "…")`, `HX3003`). |
| enum name | nominal enumeration | A member is written `Level.high` and is a value of `Level`; an undeclared member is `HX3013`. |

**Nominal, not structural.** Two schemas with identical fields are different
types. There is no subtyping and no coercion: `Any` is the only universal
match. `Task<T>` exists internally (the result of `spawn`) but cannot be written
as a source type; `Task` is therefore `HX3001`.

**Typed boundary.** Every value returning from a host operation names a schema
the program must declare. `repo.read` returns `RepoFile`, `verify.tests`
returns `Evidence`, `repo.propose_patch` returns `Patch`,
`review.bundle` returns `ReviewBundle`, `repo.list` returns `List<RepoFile>`.
A missing declaration is `HX3001` at the call, not a surprise at runtime.

---

## 5. Grammar

```ebnf
Program      = [ Module ] { Declaration } EOF ;
Module       = "module" Ident ;

Declaration  = Schema | Enum | Fn | Agent | Workflow ;

Schema       = "schema" Ident "{" { Field } "}" ;
Field        = Ident ":" Type [ "=" Expr ] [ "," | ";" ] ;
Enum         = "enum" Ident "{" { Ident } "}" ;

Fn           = "fn" Ident Params "->" Type Block ;

Agent        = "agent" Ident ":" Type "{" { AgentSetting } "}" ;
AgentSetting = Ident ":" Expr [ "," | ";" ] ;      (* role | objective | model *)

Workflow     = "workflow" Ident Params "->" Type
               "{" [ Requires ] [ Budget ] { Agent } { Statement } "}" ;
Requires     = "requires" Capability { "," Capability } ;
Capability   = Ident "." Ident ;
Budget       = "budget" "{" { BudgetEntry [ ";" | "," ] } "}" ;
BudgetEntry  = Ident ":" BudgetValue ;
BudgetValue  = Int | Float [ Currency ] | Duration | String | "true" | "false" ;

Params       = "(" [ Ident ":" Type { "," Ident ":" Type } ] ")" ;

Block        = "{" { Statement } "}" ;
Statement    = Let | Ensure | Return | If | For | Expr [ ";" ] ;
Let          = "let" Ident [ ":" Type ] "=" Expr ;
Ensure       = "ensure" Expr ;
Return       = "return" [ Expr ] ;
If           = "if" Expr Block [ "else" Block ] ;
For          = "for" Ident "in" Expr Block ;

Expr         = Or ;
Or           = And { "or" And } ;
And          = Not { "and" Not } ;
Not          = [ "not" ] Comparison ;
Comparison   = Addition { ( "==" | "!=" | "<" | ">" | "<=" | ">=" ) Addition } ;
Addition     = Unary { "+" Unary } ;
Unary        = ( "await" | "spawn" ) Postfix | Postfix ;
Postfix      = Primary { "." Ident | CallArgs } ;
CallArgs     = "(" [ Arg { "," Arg } ] ")" ;
Arg          = Expr | Ident ":" Expr ;
Primary      = Int | Float | String | Duration | "true" | "false"
             | Ident | "(" Expr ")" | "[" [ Expr { "," Expr } ] "]" ;
```

Precedence, loosest first: `or`, `and`, `not`, comparisons, `+`, `await`/`spawn`,
member/call. There is no `if` **expression** in 0.2.0; `if` returns by
`return`ing from both branches.

### 5.1 Declaration order inside a workflow

`requires`, then `budget`, then `agent` declarations, then the body. A section
after the body is `HX2004`; a repeated section is `HX2005`. The order is
enforced because a reviewer should be able to answer "what may this workflow do
and for how long" by reading the top of it.

### 5.2 Statements

- `let` binds a name for the rest of the declaration. An annotation is
  checked against the value (`HX3002`); without one, the value's type is used.
  Scope is per declaration in 0.2.0 — a binding made inside an `if` branch stays
  visible after it. There is no block scoping and no shadowing, which keeps the
  rule simple enough to state and to test; a future version that adds it must
  say so in the manifest's `spec_version`.
- `ensure` asserts a `Bool` at that point (`HX3002` otherwise). It is the only
  assertion form — a workflow does not decide pass/fail, it states evidence.
- `return` ends a declaration. A `fn` or `workflow` returning anything other
  than `Any` must end with a `return`, or with an `if`/`else` whose both
  branches do (`HX3007`).
- `for name in list` iterates a `List<T>` in order (`HX3014` otherwise), binding
  `name` for the body and spending one `budget.steps` per pass (`HX5006` when the
  workflow declares no steps). The list is the structural bound and the budget
  is the spend bound, so a loop cannot outlive either: a list longer than the
  remaining steps ends the run as `budget` (`HXH501`) at a recorded step, with
  the iterations it did perform still in the event log. There is no `while` and
  no mutation — the loop variable is a binding, not a cell — and a `for` inside a
  pure `fn` is `HX4008`, because a pure function has no budget to point at.
- An expression statement is evaluated for its effect; its value is discarded.

---

## 6. Profiles

| Construct | `core` | `agent` |
|---|---|---|
| `module`, `schema`, `enum`, `fn`, `let`, `ensure`, `return`, `if` | yes | yes |
| `workflow`, `agent`, host operations, `await`, `spawn` | no — `HX6001` | yes |

`core` is the deterministic subset: no effects, no host operations, no
concurrency. It is what a pure helper library should be checkable under, and
what a future "no-model" review path can run.

The profile is an **argument to the host**, not a line in the file:

```sh
hxc check app.hx --profile core
```

A source that tries to choose gets `HX6002` and the checker continues under the
host's profile, because the rest of the file still deserves checking.

---

## 7. Effects, capabilities and the ambient wall

### 7.1 Declaring

```hx
workflow fix_issue(input: Issue) -> ReviewBundle {
  requires repo.read, repo.propose_patch, tool.test, model.invoke
  budget { steps: 200; tool_calls: 12; tasks: 4; duration: 10m; cost: 2.00 USD }
  ...
}
```

`requires` accepts only the closed set below. Unknown names are `HX4002` with
the known list in the message; a repeat is a warning (`HX7005`); a declared
capability never used is a warning (`HX7001`).

### 7.2 The capability table

| Capability | Harness permission | Used by | Meaning |
|---|---|---|---|
| `repo.read` | `read` | `repo.read`, `repo.list` | Read repository files and listings inside allowed paths. |
| `repo.propose_patch` | `comment` | `repo.propose_patch` | Propose a patch for human review. Never a write, never a merge. |
| `tool.test` | `read` | `verify.tests` | Run an allow-listed test command in the confined worker. |
| `model.invoke` | `read` | `agent.plan`, `agent.propose`, `agent.review` | Call a declared agent. Routed by the Harness model policy; never downgraded silently. |

The permission column exists so the manifest speaks
`harness_contracts.policy_engine.PERMISSIONS` instead of a parallel vocabulary.
It is a *ceiling description*, not a grant. `approve`, `administer`, `billing`
and `export` are absent by construction and pinned by
`tests/test_contract_vocabulary.py`; a program may never hold them.

### 7.3 Host operations

| Operation | Return | Effect? |
|---|---|---|
| `repo.read(path: String)` | `RepoFile` | yes |
| `repo.list(prefix: String)` | `List<RepoFile>` | yes |
| `repo.propose_patch(changes)` | `Patch` | yes |
| `verify.tests(target, suite: String)` | `Evidence` | yes |
| `review.bundle(changes, evidence)` | `ReviewBundle` | **no** — pure, requests nothing |

Agent operations are `plan`, `propose`, `review`; each takes one `input` and
returns the agent's payload type. Any other member is `HX3011` with the known
list.

### 7.4 The rules at the call site

| Rule | Code |
|---|---|
| Effect used without its capability in `requires` | `HX4001` |
| Capability name not in the table | `HX4002` |
| Host operation or agent call inside a pure `fn` | `HX4003` |
| Ambient name (`exec`, `open`, `http`, …) | `HX4004` |
| Effect not awaited | `HX4005` |
| `policy.grant(...)`, `capabilities.add(...)` and friends | `HX4006` |
| Task spawned and never awaited | `HX4007` |

`HX4004` fires **by name**, wherever the name appears — call, member base or
declaration — so the message says "no profile provides it" instead of "unknown
identifier" six lines later. The forbidden ambient set is:

```
exec eval system shell sh bash process os fs open curl http https
socket net fetch load require import
```

The forbidden authority set is `policy policies grant grants capability
capabilities`. Both are frozen tables with a test that fails if a name is added
without a fixture.

### 7.5 Awaiting and spawning

```hx
let file = await repo.read("README.md")        // sequential
let a = spawn repo.read("README.md")           // concurrent, returns Task<RepoFile>
let first = await a                            // join
```

`await` accepts an effect call or a `Task<T>`; anything else is `HX3002`.
`spawn` only accepts an effect, only inside a workflow, and counts against
`budget.tasks` (`HX5005` when exceeded; `HX7003` when no limit is declared).
A task whose name is never awaited is `HX4007` — a workflow that leaves work
running has no defined result.

### 7.6 A grant can be revoked while the run is in flight

Authority is the host's, and a host's circumstances change: a budget freeze, an
incident, a reviewer who decides the next effect is not wanted. Because the
driver asks the decision at **every call site**, a grant set that can change is
all revocation needs — no new check, no new syntax, and nothing the program can
see or influence.

| Property | What it means | Refusal |
|---|---|---|
| A revocation bites the next crossing | Completed crossings stay as they were; the run keeps the work it was still allowed to finish. | `HXH401`, `revoked: true` |
| The request set is fixed | `revoke` narrows authority; `restore` can only return a capability this program requested. A run cannot grow its own ceiling by waiting. | `restore` returns `False` |
| The log tells the story | `grant.revoked` / `grant.restored` events are recorded between the crossings they separate, with the host's reason. | — |
| Handed-in authority is checked, not trusted | A decision from another program, a capability outside the closed table, or one mapping to a human-only permission is refused before anything runs. | `HXH404` |
| Revoking is not cancelling | A revoked capability stops effects, not computation: pure steps run on until the program's own return, its budget, or `cancel` ends them. | — |

The host-facing shape is `as_revisable(authorize(...))` passed as
`run_source(authority=...)`. A plain `Decision` still works and still means "fixed
for this run"; the evidence distinguishes them (`policy.live`,
`policy.initiallyGranted`, `policy.revisions`).

---

## 8. Budgets

```hx
budget { steps: 200; tool_calls: 12; tasks: 4; duration: 10m; cost: 2.00 USD }
```

| Key | Type | Bound | Invalid code |
|---|---|---|---|
| `steps` | integer | `1 .. 1_000_000` | `HX5004` (type), `HX5003` (range) |
| `tool_calls` | integer | `1 .. 1_000_000` | same |
| `tasks` | integer | `1 .. 1_000_000` | same |
| `duration` | duration | `1ms .. 24h` | `HX5004` (not a duration), `HX5003` (out of range) |
| `cost` | money | `0 < amount <= 1000.00 USD` | `HX5004` (no currency / not USD), `HX5003` (range) |

Unknown keys are `HX5001` (message lists the five known keys); a duplicate is
`HX5002` even when the first value was itself invalid. A workflow with no
budget is a **warning** (`HX7002`) and does not fail the check: host defaults
apply, and the reviewer still sees the omission. Budgets are recorded verbatim
in the manifest.

**What a budget does to a run.** `steps` is spent per statement and per loop
pass; `tool_calls` per effect; `tasks` per spawn; `duration` is wall clock
checked at every step and every join. All four stop the run with `HXH501`.
`cost` is enforced against a **price table the host supplies** —
`run_source(prices=...)`, `hxr run --prices FILE`, or `ContractRunner(prices=...)`
— because a program that could name its own prices could raise its own ceiling:

- the next crossing is priced **before** it happens, so a run that cannot pay
  never produces the effect it could not afford (`HXH501`, `limit: cost`);
- an operation the table prices neither by name nor by capability is refused
  (`HXH502`) rather than run free — a ceiling with a hole in it is not a ceiling;
- a table the host cannot build stops the run before anything crosses
  (`HXH503`), and `hxr` treats an unreadable price file as a usage failure;
- a run that declares `cost` with **no** table is recorded as
  `declared-not-enforced` in its evidence. That phrase is deliberate: an
  unbounded run must never be readable as a bounded one.

Prices are per crossing in USD, keyed by operation name (`developer.plan`) with a
fallback to the capability (`model.invoke`), so a host can price one call or one
kind of effect. Amounts are exact decimals, not floats: a `0.30` ceiling must not
reject a run that spent `0.10` then `0.20`. What a price *is* — a provider tariff,
an internal charge-back, a placeholder — is the host's meaning; the driver only
guarantees the arithmetic and that nothing crosses unpriced.

---

## 9. Diagnostics

### 9.1 Wire contract (owned by Section 5)

Every record is the frozen shape produced by
`section5/src/harness_sandbox/output_parser.py`:

```json
{"path":"app.hx","line":8,"col":11,"kind":"error","message":"[HX4001] capability 'tool.test' is required here but is not declared in 'requires'"}
```

- Keys appear in the order `path`, `line`, `col`, `kind`, `message`.
- Absent positions are **omitted**, never `null`.
- Records go to **stderr** only, as one JSON array or as NDJSON. Stdout is
  reserved for a requested manifest and nothing else.
- `kind` is `error`, `warning` or `note`. Hx currently emits errors and
  warnings; `note` exists in the contract for future use.

The stable code rides inside `message` as a leading `[HXnnnn]` rather than
becoming a sixth key, so a consumer that knows only the five frozen names keeps
working and adding a code can never break a reader.

### 9.2 Human format

```
app.hx:8:11: error: [HX4001] capability 'tool.test' is required here but is not declared in 'requires'
```

A record without a position prints without one. `Reporter` deduplicates by
`(line, col, kind, code, message)` and sorts by position, so two passes that
notice the same problem produce one line.

### 9.3 Exit codes

| Code | Meaning |
|---|---|
| `0` | No errors. Warnings are allowed — a warning is information, not a refusal. |
| `1` | At least one error; diagnostics went to stderr. |
| `2` | Usage or I/O failure: unreadable file, unknown profile, `--manifest` with more than one program. |

`hxr` keeps `0`/`1`/`2` and adds three: `3` denied, `4` budget exhausted, `5`
cancelled. Every status has exactly one code (§9.5), so a script can branch on
either.

### 9.4 Code table

| Prefix | Area | Codes |
|---|---|---|
| `HX1xxx` | Lexical | `HX1001` unexpected character · `HX1002` unterminated string · `HX1003` unterminated block comment · `HX1005` invalid escape |
| `HX2xxx` | Syntax | `HX2001` expected token/expression · `HX2002` unclosed brace (reported at the opener) · `HX2003` expected a declaration · `HX2004` section/agent after the body · `HX2005` duplicate section |
| `HX3xxx` | Names and types | `HX3001` unknown type / undeclared boundary schema / `Agent` misuse · `HX3002` type mismatch · `HX3003` arity or argument name · `HX3004` unknown schema field · `HX3005` unknown identifier, function or operation · `HX3006` duplicate declaration/field/member/setting/parameter · `HX3007` missing final return · `HX3008` wrong type-argument arity · `HX3009` unknown agent setting · `HX3010` agent without `role` · `HX3011` not callable / unreadable member / unknown agent operation · `HX3012` operator type error · `HX3013` unknown enum member · `HX3014` `for` over a non-List |
| `HX4xxx` | Effects and capabilities | `HX4001` undeclared capability · `HX4002` unknown capability · `HX4003` effect in a pure `fn` · `HX4004` ambient operation · `HX4005` effect not awaited · `HX4006` authority from source · `HX4007` task never awaited · `HX4008` loop in a pure `fn` |
| `HX5xxx` | Budgets | `HX5001` unknown key · `HX5002` duplicate key · `HX5003` out of range · `HX5004` wrong type/units/currency · `HX5005` spawns exceed `budget.tasks` · `HX5006` `for` without `budget.steps` |
| `HX6xxx` | Profiles | `HX6001` construct requires the `agent` profile · `HX6002` source may not select the profile |
| `HX7xxx` | Warnings | `HX7001` capability declared but unused · `HX7002` no budget · `HX7003` spawn without `budget.tasks` · `HX7005` duplicate capability |

`HX1004` and `HX7004` are unassigned; numbers are never reused.

### 9.5 The driver's codes (`HXH…`)

The checker's codes describe a *program*; the driver's codes describe a *run*.
They share the wire shape and the `[CODE] message` prefix, and each code maps to
exactly one run status, so a caller never has to invent the mapping.

| Code | Status | Meaning |
|---|---|---|
| `HXH101` | `refused` | The program has check errors; nothing ran. The checker's own records are the diagnostics. |
| `HXH102` | `refused` | Well formed but not runnable here: no workflow, several workflows and none named, a non-`agent` profile, a construct outside the driver's subset, no bridge. |
| `HXH103` | `refused` | The supplied or manifest digest does not describe the bytes about to run. |
| `HXH201` | `error` | The program itself failed: undefined name, wrong arity, `ensure` false, unreadable field. Carries the program's line. |
| `HXH301` | `error` | A host reply did not match the type the program declared (missing, extra, or mistyped field). The host's bug, not the program's. |
| `HXH401` | `denied` | A capability the run requested is not granted, or the principal lacks its permission, or it maps to a human-only permission. |
| `HXH403` | `error` | The host has no adapter for this operation (no sandbox, no model relay, no recorded answer). Refused rather than faked. |
| `HXH404` | `denied` | Authority handed to the runner is not this program's: a different request set, a capability outside the closed table, or one that maps to a human-only permission. |
| `HXH501` | `budget` | `steps`, `tool_calls`, `tasks`, `duration` or `cost` exhausted; the message names the limit. |
| `HXH502` | `budget` | The run declares `budget.cost` and the host's table has no price for this crossing. Refused rather than run free. |
| `HXH503` | `refused` | The host supplied a price table that cannot be built (empty, negative, non-numeric, blank key). Nothing crosses. |
| `HXH601` | `cancelled` | The run was cancelled; observed at the next step. |

`HXH000` is the base class's placeholder and never appears in output.

---

## 10. The policy-request manifest

`hxc check app.hx --manifest -` prints the request to stdout; `--manifest PATH`
writes it to a file. It is built from the tree after checking, so a host should
consume it only when the exit code is `0` (or after applying its own policy to
the diagnostics in a warnings-only run).

```json
{
  "agents": [],
  "language": "hx",
  "module": "maintenance",
  "policy_request": {
    "approval": "required-for-review",
    "authority": "harness",
    "forgeable": false,
    "grants": "none",
    "permission_ceiling": {
      "model.invoke": "read",
      "repo.propose_patch": "comment",
      "repo.read": "read",
      "tool.test": "read"
    },
    "requested_capabilities": ["model.invoke", "repo.propose_patch", "repo.read", "tool.test"]
  },
  "profile": "agent",
  "source": {"bytes": 1012, "path": "fix_issue.hx", "sha256": "1f69…"},
  "spec_version": "0.2.0",
  "toolchain": {"name": "hxc", "version": "0.2.0"},
  "workflows": [
    {
      "budget": {"cost": "2.00 USD", "duration": "10m", "steps": 200, "tasks": 4, "tool_calls": 12},
      "name": "fix_issue",
      "requests": [
        {"capability": "model.invoke", "operation": "developer.plan"},
        {"capability": "repo.propose_patch", "operation": "repo.propose_patch"},
        {"capability": "repo.read", "operation": "repo.read"},
        {"capability": "tool.test", "operation": "verify.tests"}
      ],
      "requires": ["model.invoke", "repo.propose_patch", "repo.read", "tool.test"],
      "returns": "ReviewBundle"
    }
  ]
}
```

Notes a host must not skip:

- `source.sha256` is the digest of the exact UTF-8 bytes checked; the request
  describes *that* source, not a path.
- `requests` pairs each capability with one operation that used it, and omits
  pure operations (`review.bundle`) because they request nothing.
- `budget` is `null` when the workflow declared none — the host's defaults, not
  a default invented here.
- Agents declared at top level appear in `agents`; agents declared inside a
  workflow appear through the `model.invoke` requests they generate.
- No key means "granted". `grants` is the string `"none"`, and
  `forgeable` is `false` as data, not as a comment.

---

## 11. Conformance

The reference implementation ships the tests that define conformance:

| Layer | Where | What it pins |
|---|---|---|
| Golden fixtures | `tests/fixtures/{valid,invalid,warnings,core}` + `.expected.json` | Every fixture produces exactly the reviewed records, at the reviewed positions. |
| Regeneration | `scripts/capture_expected.py` | Goldens are regenerated deliberately and the diff is read; they are not updated by a passing test. |
| Wire contract | `tests/test_wire_contract.py` | Key order, omitted keys, stderr rule, human line. |
| Effect rules | `tests/test_checker_effects.py` | Declared, awaited, pure, ambient, authority, spawn. |
| Types | `tests/test_types.py` | Nominality, arity, optionals, operators, returns. |
| Budgets | `tests/test_budget.py` | Every key, every bound, warnings that do not fail. |
| Profiles | `tests/test_profiles.py` | Host selects; source cannot. |
| Seeded faults | `tests/test_seeded_faults.py` | Each `RuleSet` flag is load-bearing: disable it, the code vanishes, the rest stays. |
| Contract bridge | `tests/test_contract_vocabulary.py` | Capability permissions are subset of `PERMISSIONS`; human-only permissions absent. |
| Messages | `tests/test_message_stability.py` | One line, one code, family matches the fixture, positions in range. |
| Manifest | `tests/test_manifest.py` | Versions, digest, typed budgets, request-not-grant shape. |
| CLI | `tests/test_cli.py` | Exit codes, stderr/stdout separation, usage failures. |
| Driver: authority | `tests/test_host_authority.py` | Deny-by-default, grants, permissions, human-only invariant, contract vocabulary, digest binding. |
| Driver: boundary | `tests/test_host_boundary.py` | Nominal, closed validation; Bool ≠ Int; missing/extra/nested paths. |
| Driver: bridge | `tests/test_host_bridge.py` | One door; settled exit codes; refusals by name; root escape; secret paths; recording and replay. |
| Driver: runtime | `tests/test_host_runtime.py` | End-to-end run, denial, each budget, cancellation, redaction, evidence, replay without a live host. |
| Driver: CLI | `tests/test_host_cli.py` | `hxr plan` / `hxr run` exit codes 0–5, stdout/stderr split, evidence file. |
| Driver: contract runner | `tests/test_host_contract_runner.py` | `hxr` is a `WorkflowRunner` for Section 4; a replay reproduces the digest without reaching the live host. |
| Driver: model relay | `tests/test_host_model_relay.py` | A secret never reaches the outbound prompt, the log, the event stream or the evidence bundle; a downgrade is recorded, never performed. |
| Driver: cost enforcement | `tests/test_host_cost.py` | The next crossing is priced before it happens; an unpriced operation is refused rather than free; no table means `declared-not-enforced`, said out loud in the evidence; money adds exact, and two parallel crossings cannot both pass a ceiling one of them breaks. |
| Driver: revocation | `tests/test_host_revocation.py` | A revoked capability denies the next crossing and not a completed one; the revision is in the log with its reason; handed-in authority is verified, and can never widen the request. |
| Driver: code set | `tests/test_host_codes.py` | Every emitted code is in the §9.5 table, every table code is still emitted, and no code changed status. |
| Growth | `tests/test_language_growth.py` | Each addition (enum values, record construction, loops) has code + fixture + golden, and what did not come in with them: no `while`, no modules. |
| Live host | `section5/tests/unit/test_hx_live_host_ops.py` | One real `repo.read` and one real `verify.tests` through the shipped `LocalSandboxRunner`; skips loudly, never weakly, without an OS boundary. |

To add a diagnostic: pick the namespace, add the code where it is emitted, add a
fixture that triggers it, regenerate its golden, and read the diff. A code that
exists without a fixture is a claim nobody checked.

`RuleSet` exists for "validate the validator": a checker that never fails is
indistinguishable from one that always passes. Tests disable one rule at a time
and assert the corresponding code disappears while the others remain.

---

## 12. Not in 0.2

Deliberately absent, with the reason:

- **A general interpreter.** There is no VM and no scheduler for arbitrary Hx.
  `hxc` only checks; `hxr` executes the workflow subset in 12.1 and refuses
  anything else with `HXH102`. There is no ambient access of any kind: every
  effect crosses the host bridge.
- **Module system.** Single file. `import` is rejected by name, not unknown.
- **Mutation, `while`, and assignment.** `for` iterates a list and spends one
  `budget.steps` per pass, so the data bounds the passes and the budget bounds
  the spend. There is no `while` — it would need a termination proof this
  language cannot offer — and no assignment or mutable binding.
- **Construction out of thin air.** A record can be built (`Summary(text: "…")`)
  from values the program already holds, and only for a type the program itself
  declared. There is no anonymous struct literal and no construction on the wire.
- **User-defined generics, traits, pattern matching.** None.
- **Money/duration arithmetic.** Parseable and bounded, not computable.
- **Approval verbs.** `approve`, `administer`, `billing`, `export` are not
  capabilities and no manifest key grants them.
- **Model selection.** `agent { model: "..." }` is recorded as a string; the
  Harness routing policy decides, and a forced downgrade is out of scope by
  product rule.
- **Live replay.** A recorded run replays from its recorded exchanges
  (`RecordingBridge` / `ReplayBridge`) and never reaches a host; that is
  evidence replay, not effect replay — nothing is re-issued and nothing is
  undone.

### 12.1 The workflow driver (`hxr`)

`packages/language` ships two commands: `hxc` checks, `hxr` plans or runs one
workflow against a host bridge. The driver is deliberately smaller than the
language it accepts:

| Construct | Driver |
|---|---|
| `let`, `ensure`, `return`, `if`/`else`, expression statements | executes |
| `await` on a host operation or an agent operation | executes through the bridge |
| `spawn` … `await` of the task | executes on a bounded thread pool |
| `fn` declarations and calls | executes |
| `for x in list` … body | executes, spending one `budget.steps` per pass; the list ends it or the budget does (`HXH501`) |
| enum member references (`Level.high`) | executes |
| record construction (`Summary(text: "…")`) | executes; a field of the wrong type is refused on the spot (`HXH201`) |
| anything else | `HXH102`, refused — never guessed |

The order is the product claim, and it is the order of `run_source`:

1. **Check.** Errors mean nothing runs (`HXH101`, status `refused`).
2. **Bind.** The manifest's `source.sha256` must describe the bytes about to
   run (`HXH103`).
3. **Authorise.** The host grants a subset of the manifest's request; an
   unrequested grant is not a grant, an ungranted request denies the run
   (`HXH401`), and the decision is asked again at every call site.
4. **Run.** The workflow executes inside its declared `budget`; `steps`
   (including one per loop pass), `tool_calls`, `tasks` and `duration` stop the
   run they name (`HXH501`), cancellation takes effect at the next step
   (`HXH601`), and a host reply that does not match its declared type fails at
   the boundary (`HXH301`).
5. **Record.** `hx.evidence/v1`: source digest and size, manifest, policy
   decision, input digest (never values), every crossing with run id,
   correlation id and idempotency key, usage against declared limits, outcome,
   and `review: {required: true, approved: false, autoMerge: false}`.

Statuses are closed: `ok`, `refused`, `denied`, `budget`, `cancelled`, `error`.
The driver never merges, applies an edit, approves, or retries a side effect.

## 13. Versioning

`spec_version` and the toolchain version are separate fields in every manifest.
While the major version is `0`:

- **Adding** a code, a capability, a budget key or a host operation is a minor
  change. It must include: the table entry, a fixture, a golden, and a test
  that the closed-set assertion was deliberately updated.
- **Changing** an existing code's meaning or a message that a test asserts is a
  spec change: bump `spec_version` and update goldens in the same commit.
- **Removing** a capability or tightening a bound is breaking; it needs a
  migration note for programs already checked under the old version.
- `contracts/` is frozen separately: if a permission name changes there, this
  package's bridge test fails before anything else does.
