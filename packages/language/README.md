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
./.venv/bin/hxr run app.hx --grant repo.read --root . --prices prices.json
                                                  # makes budget.cost a limit (exit 4 = spent)
./.venv/bin/hxr run app.hx --grant repo.read --root . --record run.json
./.venv/bin/hxr run app.hx --grant repo.read --replay run.json
                                                  # answers from the recording; exit 1 if a call in it never settled
./.venv/bin/hxc check main.hx --modules lib                        # `use notes` reads lib/notes.hx
./.venv/bin/hxc check main.hx --modules none                       # what is this without its helpers?
./.venv/bin/hxc check main.hx --manifest - > policy-request.json    # carries program_sha256
./.venv/bin/hxr run main.hx --grant repo.read --root . --program-digest <that digest>
                                                  # pins every file the program was built from, not just the entry
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
| Since M8 that pin covers **every** file: `sources[]` digests each one and a swapped helper is refused even when the entry is untouched | `manifest.py`, `authority.py` (`assert_checked_source`), `loader.py` |
| Grants are computed from the manifest, consulted again at every call site, and human-only permissions are never grantable | `authority.py`, `runtime.py` (`HXH401`) |
| Every host reply is validated against the declared nominal type; missing, extra or mistyped fields fail closed | `boundary.py` (`HXH301`) |
| `steps`, `tool_calls`, `tasks` and `duration` stop the run that exceeds them | `runtime.py` (`HXH501`) |
| `cost` is reserved *before* the crossing, against a price table only the host can supply; an unpriced operation is refused rather than free, and no table is announced as `declared-not-enforced` | `pricing.py`, `runtime.py` (`HXH501`/`HXH502`/`HXH503`) |
| A grant can be revoked mid-run: the next crossing is denied, completed ones stand, the revision is logged with its reason, and handed-in authority is verified instead of trusted | `authority.py` (`RevisableDecision`), `runtime.py` (`HXH401`, `HXH404`) |
| Cancellation takes effect at the next step; a spawned call costs one tool call, not two | `runtime.py` (`HXH601`) |
| Secret-shaped arguments are redacted before any event; evidence says `review.required: true`, `autoMerge: false` | `runtime.py`, `run.py` |
| A secret is found by *content* — on the prompt, the log, the event and the evidence — and reported as a count by kind, never as a value | `secrets.py`, `run.py` |
| `model.invoke` goes through the host's `ModelRouter` with the declared role; no router fails closed, a downgrade is recorded and never performed | `model_relay.py` (`HXH403`) |
| The driver is a `WorkflowRunner`: Section 4 runs it and publishes its events | `contract_runner.py`, `section4/src/harness_orchestrator/workflow_runs.py` |
| A patch is a proposal with a content-addressed id; nothing merges, approves or applies | `bridge.py` |
| A recorded run replays from its exchanges and never reaches a live host | `bridge.py` (`RecordingBridge`, `ReplayBridge`) |
| A recorded **failure** replays as that failure — same code, status, line, message — and a recording whose call never settled is refused instead of guessed (`HXH104`) | `bridge.py` (`hx.recording/v2`), `errors.py` (`from_record`) |
| Evidence says whether its own trace is complete and where the answers came from | `run.py` (`recording`: `origin`, `settled`, `failures`, `unanswered`) |
| A module is named, never pathed, and arrives renamed: `notes.Summary` is one identifier in the checker, the runtime and the evidence | `loader.py` (`module_program`), spec §5.3 |

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
  loader.py        resolves `use` names to bytes and renames a module for its reader
  manifest.py      policy-request manifest (sorted JSON; `sources[]`, `program_sha256`)
  api.py           check_source / check_file → CheckResult
  cli.py           hxc check (exit 0/1/2)
src/harness_hx_host/
  errors.py        typed failures; closed status vocabulary; HXH codes
  boundary.py      nominal validation of everything the host returns
  secrets.py       the one detector: PEM, provider keys, tokens, JWTs, URL credentials
  authority.py     manifest → Decision (or RevisableDecision); digest binding
  pricing.py       the host's price table and the cost reservation (USD, exact)
  bridge.py        the one door out (fake, section-2/5, local, record, replay);
                   the recording format itself: `hx.recording/v2`, outcomes per exchange
  model_relay.py   model.invoke → the host's ModelRouter; roles, scrub-in/out, downgrades
  contract_runner.py  the driver as a harness_contracts WorkflowRunner
  runtime.py       the workflow driver and its budgets
  run.py           check → bind → authorise → run → evidence
  cli.py           hxr plan / hxr run (exit 0/1/2/3/4/5)
scripts/
  capture_expected.py   regenerate goldens for tests/fixtures/*.hx
tests/
  fixtures/{valid,invalid,warnings,core}/  .hx + reviewed .expected.json
                                           (a cross-file fixture adds <name>.hx.modules.json)
  test_*.py                                one behaviour per file
  test_host_*.py                           the driver: authority, boundary, bridge,
                                           runtime, CLI, modules
```

## Adding a diagnostic

1. Choose the namespace: `HX1xxx` lexical, `HX2xxx` syntax, `HX3xxx`
   names/types, `HX4xxx` effects, `HX5xxx` budgets, `HX6xxx` profiles,
   `HX7xxx` warnings.
2. Emit it where the position is known (the token that caused it), not later.
3. Add or extend a fixture under `tests/fixtures/`. For a cross-file rule, ship the
   sidecar (`<name>.hx.modules.json`) and remember that a record must name the file its
   line sits in — the golden writes `path` when that file is not the fixture itself.
4. Regenerate goldens and **read the diff**:
   `./.venv/bin/python scripts/capture_expected.py`
5. Add a unit test that names the rule, and a seeded-fault test if a `RuleSet`
   flag can switch it off.
6. Update the code table in `docs/hx-language-spec.md`, and give the new code a
   fixture: `tests/test_code_table.py` refuses to let the "code with no fixture" list
   grow.

## Non-goals

No general interpreter, VM or scheduler: `hxc` checks, `hxr` executes the workflow
construct set and refuses anything else with `HXH102`. No module *system* — 0.4.0's
`use` names files the host has already agreed to supply, with no packages, versions,
re-exports, nesting or loading during a run — no assignment or mutable binding, no
`while`, no user-defined generics, and money and durations are bounded but not
computable. Neither is `+` numeric: it joins Strings, and that asymmetry is listed
with its reason in the spec (§12). All of it is the security boundary rather than a
to-do.
Confinement belongs to `section5`, event semantics to `section4`, model selection
to `section3`, and the manifest only asks: a price list, a grant set and a policy
answer all arrive from the host.
