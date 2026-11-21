# A-01 shell argument/path-scope dispositions (2026-11-21, P-06)

Fleet disposition record for `90-day-execution-plan-2026-11-11.md` P-06 ("A-01
shell argument/path scope dispositions + negative tests"). Companion evidence:
`docs/briefs/evidence/pilot-prep/a01-deployed-gate-matrix-2026-11-21.json`.
Revisions: deployed tag `vw24b1` (worker image
`sha256:e59ed0c6…`), HEAD `160dd30` (w28-s1-delegate).

## 1. What the A-01 surface is

The engine's bash tool sends every command through Section 5's shell gate
(`section5/src/harness_sandbox/shell_gate.py`): policy validation (operators,
secret files, binary allow-list, installs), forbidden-path read-only check,
intent `scopePaths` check, then OS confinement (Seatbelt on macOS, Landlock on
Linux) with write roots narrowed to the intent scope (M03). The argument-level
checks — relative-traversal, tilde, env-var, `--flag=path` and short-flag
argument resolution against the workspace and scope — were added 2026-11-13/14
(`e24b830`) and are pinned by:

- `section5/tests/test_shell_gate_args.py` — 25 decision-layer cases (25 passed
  2026-11-21, macOS and re-run in the Linux VM where relevant)
- `section5/tests/unit/test_shell_gate_scope_edges.py` — scope-edge rows
- `section5/tests/unit/test_shell_gate_real_boundary.py` — real sandbox-exec
  boundary (34-suite row with `test_confinement.py`: 34 passed, 1 skipped,
  4 xfailed)
- `section5/tests/test_shell_gate_args.py` note: decision-layer tests stub the
  child, so they are exact on hosts with and without OS confinement; the real
  boundary keeps its own rows.

## 2. Disposition per item

| # | Item | State at HEAD `160dd30` | State on deployed tag `vw24b1` | Disposition |
|---|---|---|---|---|
| 1 | Direct secret-file read (`.env` etc.) | Closed — policy denies, pinned | Closed — policy denies (in-image probe) | **Closed both sides** |
| 2 | `../../etc/passwd` traversal | Closed — policy pattern denies (either layer counts) | Closed — policy pattern denies | **Closed both sides** |
| 3 | `../../etc/hosts` traversal read | Closed — `OUT_OF_SCOPE` at the gate | **Open** — decision layer allows; the probe EXECUTED the read | **Deployed-tag gap; redeploy required (A-06, owner)** |
| 4 | Tilde argument (`cat ~/x`) | Closed — `OUT_OF_SCOPE` | **Open** — allowed through; confinement decides | **Deployed-tag gap; redeploy required** |
| 5 | Parent-directory argument (`ls ..`) | Closed — `OUT_OF_SCOPE` | **Open** — allowed through; confinement stopped the read in-probe | **Deployed-tag gap; redeploy required** |
| 6 | Absolute read outside the workspace | Closed — `OUT_OF_SCOPE` | **Open** — allowed through; confinement stopped the read in-probe | **Deployed-tag gap; redeploy required** |
| 7 | Traversal write (`tar -cf ../out.tar`) | Closed — binary allow-list denies `tar`; write roots = intent scope | Closed — same deny (in-image probe) | **Closed both sides** (the write-half also sits under confinement write-roots, M03) |
| 8 | In-scope control (`cat docs/y.txt`) | Works (allowed; real reads pinned by the boundary suite) | Probe artifact: confined child denied even this — **not product truth**, see the matrix caveat | No action; CI drill proves the real path works |
| 9 | Env-var argument (`$HOME/...`) | Closed — expanded and denied; unresolvable vars denied fail-closed (`test_env_var_path_is_expanded_and_denied`, `test_var_the_gate_cannot_resolve_is_denied`) | Not probed individually — same vintage as items 4–6, assumed open | **Deployed-tag gap assumed; redeploy required** |
| 10 | One program per call (chained commands) | Closed — pinned (`test_one_program_per_call_chained_commands_are_denied`) | Predates the check — same vintage gap | **Deployed-tag gap assumed; redeploy required** |

## 3. The one-line disposition

**Code-side A-01 is closed at HEAD and pinned; the deployed tag vw24b1 predates
the argument checks, and four negatives (items 3–6, plus 9–10 by vintage) pass
its decision layer — one of them demonstrably executed an out-of-workspace
read.** Closing the deployed side is not a code task: it is the one-tag-set
redeploy already owned by A-06 (env-manifest §5.3 / week-02 §5 R10), which
should build from a post-`e24b830` revision. Until then the A-06 drift verdict
("AGREE, vw24b1") is an agreement of an OLD set, not an endorsement of it.

## 4. Negative-test results ledger (acceptance evidence)

| Suite | Host / kernel | Revision | Result |
|---|---|---|---|
| `test_shell_gate_args.py` (25) | macOS + Linux VM (6.12.76-linuxkit) | `160dd30` | 25 passed |
| `test_shell_gate_scope_edges.py` + real boundary + confinement | macOS | `160dd30` | 34 passed, 1 skipped, 4 xfailed |
| section5 full suite | macOS | `ff112dd` | 542 passed, 8 skipped, 4 xfailed |
| Deployed-gate matrix (8 cases, in-image) | worker-daemon:vw24b1 (Linux VM kernel) | vw24b1 image `e59ed0c6…` | 4 deltas recorded (c3–c6); JSON above |

Owner actions this feeds: **A-06 one-tag-set redeploy from a post-`e24b830`
revision** (closes deployed-side A-01 items 3–6, 9–10), and the G1/G2
risk-acceptances already standing in week-02 §2.
