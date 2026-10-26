# Tool-output protocol — structured diagnostics, and the differential review gate

Roadmap H1 (2026-10-25). Two designs taken from Hermes
(`/Users/user/Compiler_Project`), not code: its `--json` diagnostic contract,
and the differential-verification pattern it uses to prove a change did not
alter behaviour.

Status: **the contract is implemented and in use** by the sandbox
(`section5/src/harness_sandbox/output_parser.py`), the generated Python/TS SDKs
and the run-page terminal panel. **The differential gate is not yet a review
gate** — it is applied to the parser itself today and lands as part of the W2
review-evidence work (see "Open" at the end).

---

## 1. The contract

A tool reports a problem as one record:

```json
{"path": "app.hms", "line": 3, "col": 7, "kind": "error", "message": "expected ')'"}
```

and the rules around it:

1. **Diagnostics go to stderr; the program's own output goes to stdout.** Never
   mixed. One JSON array on stderr, or one record per line (NDJSON) for a tool
   that reports as it goes.
2. **`kind`** is `error` | `warning` | `note`. An unrecognised severity word maps
   to `error`: a governance product may over-report a failure, never soften one.
3. **`path` / `line` / `col` are optional and are never invented.** A record with
   no position is a legitimate record; a fabricated position is not.
4. **Exit code still decides pass/fail.** The records explain *why*, they do not
   replace the verdict.

This is Hermes' `hermesc --json` contract verbatim — see
`hermes/src/main.cpp:330-372` (`JsonDiag`, `print_diagnostics_json`) and
`hermes/src/main.cpp:516-545` (the three emit sites: lex error, collected parse
errors, non-fatal warnings). It was chosen because it is the smallest shape that
survives a real compiler: a compiler that cannot say *where* has nothing useful
to say.

### Why it replaces scraping

Before this, `parse_exec_output` merged stdout and stderr, grepped a
`(\d+) (passed|failed)` tally and collected lines containing `error:` — so every
diagnostic reached the agent as an opaque string with no position, and a tool
that printed `error:` inside its own output looked like a failure. The live
transcript planned in W1 needs per-call structure; a string array cannot supply
it.

## 2. Three ingestion tiers, and the honesty label

The sandbox has to run *arbitrary* commands, so it cannot demand every tool
speak the contract. The parser therefore reads in honour order and records which
tier it used in `diagnostics_source` — so the UI can say where a position came
from instead of implying it was reported:

| `diagnostics_source` | What happened | Positions |
|---|---|---|
| `protocol` | the tool emitted the contract (JSON array or NDJSON on stderr) | as reported |
| `located` | no JSON, but `path:line:col: kind: message` text (gcc/clang, Hermes' human output, tsc's `file(l,c):`, eslint stylish rows) | parsed from text |
| `scan` | neither: a keyword hit in either stream (pytest's `FAILED …`, vitest's `● …`) | none — the record says so |
| `none` | nothing found | — |

Notes on the two fallback tiers:

- `located` reads **stderr only**. A stdout line shaped like a compiler error is
  the program's own text, not a diagnostic source (pinned by
  `test_located_tier_is_only_read_from_stderr`).
- `scan` is the *only* tier that reads stdout, because test runners print their
  summary there. It is labelled `scan` in the API and in the UI note "This tool
  reports no file or line, so these are matches from its output."
- Foreign JSON key names (`filename`/`line_number`/`column`, ruff and friends)
  are mapped by a small alias table. That is a compatibility tier, not the
  contract; a tool that speaks the contract needs no alias.
- Two identical records are one defect (`_dedupe`), and the 20-record display
  cap sets `diagnostics_truncated` rather than dropping the rest silently.

### Emitting

`render_diagnostics_json(records)` produces the exact wire form (Hermes field
order, absent fields omitted). A sandbox-side tool that wants its output parsed
by position writes that string to stderr; `emit → parse` is an identity,
pinned by `test_emit_then_parse_is_identity`.

## 3. Where the type lives, and why it is not in `contracts/` yet

`Diagnostic` and `StructuredExecResult` stay in `section5` today. Only section 5
produces them and nothing but the generated SDKs and the frontend mirror consume
them, so promoting them to `harness_contracts` would force the atomic four-way
contract version bump (pyproject + `__init__.__version__` + TS mirror header +
installed metadata) outside the two bumps the roadmap schedules — W2 (tool
events) and W9 (artifact types).

The promotion happens *with* W2: `tool_call` / `tool_result` event payloads need
exactly this record shape, and that bump is already planned. Until a second
section is a consumer, a shared type would be ceremony.

## 4. The differential review gate (second H1 design)

Hermes keeps two independent backends (tree-walker and bytecode VM) and treats
byte-identical output — including error text — as the equivalence property. Its
fuzzer's detection power is itself validated by planting deliberate bugs and
confirming the suite catches them.

The idea worth stealing for our review gate:

1. **Run baseline and agent-patched code over the same corpus** and require
   byte-identical output wherever behaviour must not change. A patch that "adds
   logging" or "refactors" and alters output is either wrong or dishonest about
   its scope.
2. **Periodically plant a known defect** in the corpus and confirm the gate
   fails. That last step — validating the validator — is what our current "the
   tests the agent wrote pass" evidence lacks: an agent that writes a vacuous
   test still passes.

Applied to this parser today
(`section5/tests/unit/test_tool_diagnostics_protocol.py`): a seeded corpus of
nine tool outputs across all three tiers fixes the expected kinds in order, and
a separate test asserts that the corpus actually exercises all three tiers — so
the suite cannot silently degrade into a single-tier string matcher. Clean runs
must produce zero diagnostics, and planted defects must produce exactly the
planted set.

## 5. Open — where this lands next

- [ ] **W2 (owner: S4 + S5).** Promote `Diagnostic` into `harness_contracts`
  together with the `tool_call` / `tool_result` event bump; the transcript
  renders `diagnostics_source` instead of implying every entry has a position.
- [ ] **W2 (owner: S4).** Differential gate as review evidence: baseline vs
  patched output over the run's own corpus, plus a scheduled planted-defect
  drill so the gate's detecting power is measured, not assumed.
- [ ] **W1 (owner: S5).** The sandbox's own tool wrappers (fs, glob/grep, web)
  emit this contract on stderr so `diagnostics_source` is `protocol` for our
  tools by construction.

Cross-references: `docs/roadmap-2026-10-25.md` (H1, W1, W2),
`docs/providers.md` (egress list), `docs/capability-gap-2026-10-25.md` (why the
agent's tool surface is the W1 constraint).
