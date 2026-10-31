# packages/language — Hx

Two halves, one contract:

- **`harness_hx` / `hxc`** — the reference checker for **Hx**, the Harness
  dialect of Hermes. Source in, diagnostics out: it proves a program is well
  formed and emits the policy request the Harness would have to approve. It
  never executes anything.
- **`harness_hx_host` / `hxr`** — the workflow driver. It checks, binds the
  source digest, asks the host to authorise, runs the workflow inside its
  declared budget through an injected host bridge, and records evidence. It is
  not a general interpreter: a construct outside the driver's subset is refused
  with `HXH102`, never guessed.

- Full language: [`docs/hx-language-spec.md`](../../docs/hx-language-spec.md)
- Why two halves instead of one engine: [`docs/hx-execution-architecture.md`](../../docs/hx-execution-architecture.md)
- Target workflow it was built against: [`docs/hermes-agentic-language-plan.md`](../../docs/hermes-agentic-language-plan.md)
- Wire contract it reproduces: `section5/src/harness_sandbox/output_parser.py`

## Install and run

```sh
cd packages/language
python -m venv .venv
./.venv/bin/pip install -e ../../contracts
./.venv/bin/pip install -e ".[dev]"

./.venv/bin/hxc check tests/fixtures/valid/fix_issue.hx --json
./.venv/bin/hxc check app.hx --profile core
./.venv/bin/hxc check app.hx --manifest - > policy-request.json

./.venv/bin/hxr plan app.hx                       # may this run here? (exit 3 = no)
./.venv/bin/hxr plan app.hx --grant repo.read
./.venv/bin/hxr run app.hx --grant repo.read --root . --evidence evidence.json
```

`hxr run` exits `0` ok, `1` refused/error, `2` usage, `3` denied, `4` budget,
`5` cancelled. Diagnostics go to stderr in the same wire shape `hxc` uses;
stdout carries only what was asked for (`--json`).

Or from the repo root: `make lint-language` / `make test-language`.

## What it guarantees

| Guarantee | Code paths |
|---|---|
| Diagnostics are Section 5's `{path,line,col,kind,message}`, stderr only, stdout reserved | `diagnostics.py`, `cli.py` |
| Authority is declared (`requires`), never ambient; capability table is closed and maps to Harness permissions | `capabilities.py`, `checker.py` |
| Effects must be awaited; spawned tasks must be joined; spawns are bounded by `budget.tasks` | `checker.py` |
| The host selects `core`/`agent`; source cannot | `version.py`, `parser.py`, `checker.py` |
| Host results are nominal: the program declares the schema it expects | `checker.py` (`_op_return`) |
| The manifest is a request: `grants: "none"`, `forgeable: false`, digest pinned | `manifest.py` |
| Each rule is load-bearing (seeded-fault tests disable one at a time) | `tests/test_seeded_faults.py` |

Driver guarantees (`harness_hx_host`):

| Guarantee | Code paths |
|---|---|
| Nothing runs until the program checks; refusal carries the checker's own records | `run.py` (`HXH101`) |
| The bytes that run are the bytes the manifest describes | `authority.py` (`HXH103`) |
| Grants are computed from the manifest, consulted again at every call site, and human-only permissions are never grantable | `authority.py`, `runtime.py` (`HXH401`) |
| Every host reply is validated against the declared nominal type; missing, extra or mistyped fields fail closed | `boundary.py` (`HXH301`) |
| `steps`, `tool_calls`, `tasks` and `duration` stop the run that exceeds them | `runtime.py` (`HXH501`) |
| Cancellation takes effect at the next step; a spawned call costs one tool call, not two | `runtime.py` (`HXH601`) |
| Secret-shaped arguments are redacted before any event; evidence says `review.required: true`, `autoMerge: false` | `runtime.py`, `run.py` |
| A patch is a proposal with a content-addressed id; nothing merges, approves or applies | `bridge.py` |
| A recorded run replays from its exchanges and never reaches a live host | `bridge.py` (`RecordingBridge`, `ReplayBridge`) |

## Layout

```
src/harness_hx/
  version.py       spec/toolchain versions, profiles, source suffix
  diagnostics.py   Diagnostic, Reporter, JSON/line renderers (the frozen wire)
  lexer.py         tokens, literals, durations, money, lexical diagnostics
  ast.py           syntax tree; every node keeps its position
  parser.py        recursive descent + error recovery
  capabilities.py  the two closed tables (capabilities, host operations)
  checker.py       names, types, effects, budgets, profiles; RuleSet
  manifest.py      policy-request manifest (sorted JSON)
  api.py           check_source / check_file → CheckResult
  cli.py           hxc check (exit 0/1/2)
src/harness_hx_host/
  errors.py        typed failures; closed status vocabulary; HXH codes
  boundary.py      nominal validation of everything the host returns
  authority.py     manifest → Decision; digest binding; human-only permissions
  bridge.py        the one door out (fake, section-2/5, local, record, replay)
  runtime.py       the workflow driver and its budgets
  run.py           check → bind → authorise → run → evidence
  cli.py           hxr plan / hxr run (exit 0/1/2/3/4/5)
scripts/
  capture_expected.py   regenerate goldens for tests/fixtures/*.hx
tests/
  fixtures/{valid,invalid,warnings,core}/  .hx + reviewed .expected.json
  test_*.py                                one behaviour per file
  test_host_*.py                           the driver: authority, boundary, bridge,
                                           runtime, CLI
```

## Adding a diagnostic

1. Choose the namespace: `HX1xxx` lexical, `HX2xxx` syntax, `HX3xxx`
   names/types, `HX4xxx` effects, `HX5xxx` budgets, `HX6xxx` profiles,
   `HX7xxx` warnings.
2. Emit it where the position is known (the token that caused it), not later.
3. Add or extend a fixture under `tests/fixtures/`.
4. Regenerate goldens and **read the diff**:
   `./.venv/bin/python scripts/capture_expected.py`
5. Add a unit test that names the rule, and a seeded-fault test if a `RuleSet`
   flag can switch it off.
6. Update the code table in `docs/hx-language-spec.md`.

## Non-goals

No general interpreter, VM, scheduler, module system, loops, mutation, struct
literals or user-defined generics in 0.1.0. The driver executes the workflow
construct set and nothing else; confinement belongs to `section5`, event
semantics to `section4`, and the manifest only asks.
