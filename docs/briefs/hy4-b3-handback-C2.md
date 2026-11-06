# hy4 batch 3 hand-back — C2

## Task
C2 — Shell gate: scope-bypass edge tests (test-only).

## Status
DONE (4 fail-open cases kept as `xfail(strict=True)`; **no production change**)

## Assumptions
Batches 1–2 left `f5b298a` with everything uncommitted; the three `./` defects are reachable until the writer table proves otherwise; no paid call, deploy, `docker run`, secret or git write is needed. C2 adds only that the expected values must be measured from the code as it is, not from what a human would want, and that `shell_gate.py` stays untouched.

## Files changed
- `section5/tests/unit/test_shell_gate_scope_edges.py` (new) — 25 table-driven cases, 3 `scope_violation` cross-checks, 1 `scope_roots` assertion, and 4 strict xfails.
- `docs/briefs/hy4-b3-handback-C2.md` (new) — this hand-back.

`section5/src/harness_sandbox/shell_gate.py` was temporarily mutated once for the
negative control and restored byte-for-byte (`cmp` rc=0, `git status --short`
empty). It is **not** a changed file. No JS plugin, Landlock or confinement file
was touched.

## Method (before writing any assertion)

Read `shell_gate.py:46-104` (`deny`, `scope_violation`, `_scope_hit`,
`scope_roots`) and `:122-178` (`run`), `policy.py:312-380`
(`validate_command`) and `docs/briefs/evidence/s12/part52.md:139` ("Path checks
are argument-level ... a path built inside `python -c` is stopped only by
confinement"). Then ran a read-only probe of `shell_gate.run` for all 25
commands with `scopePaths=["src"]` in a workspace containing `src/x.txt`,
`docs/y.txt` and `src2/x.txt`. Execution was stubbed (`Popen`,
`confine_argv` and `signal.signal`) so no command actually ran and the answer
does not depend on whether the host has Seatbelt/Landlock. The probe output is
the source of every expected value below.

The committed test uses the same stubs via `monkeypatch` (restored after each
case), and calls `run()` directly rather than the subprocess `gate()` helper, so
the decision path is measured without depending on OS confinement.

## The gate's current answer, one case per line

`denied` is `"denied" in run(...)`; `code` is `res.get("code")`; `hit` is
`_scope_hit(command, cwd, ["src"])`. `{WS}` is the workspace root.

| # | Command | denied | code | `_scope_hit` | Side |
|---|---|---|---|---|---|
| 1 | `cat docs/y.txt` | True | `OUT_OF_SCOPE` | `docs/y.txt` | deny |
| 2 | `cat ./docs/y.txt` | True | `OUT_OF_SCOPE` | `./docs/y.txt` | deny |
| 3 | `cat src/../docs/y.txt` | True | `OUT_OF_SCOPE` | `src/../docs/y.txt` | deny |
| 4 | `cat src/./x.txt` | False | None | None | allow |
| 5 | `cat "docs/y.txt"` | True | `OUT_OF_SCOPE` | `docs/y.txt` | deny |
| 6 | `cat 'docs/y.txt'` | True | `OUT_OF_SCOPE` | `docs/y.txt` | deny |
| 7 | `cat docs/y.txt src/x.txt` | True | `OUT_OF_SCOPE` | `docs/y.txt` | deny |
| 8 | `echo hi > docs/y.txt` | True | `COMMAND_REFUSED` | `docs/y.txt` | deny (policy) |
| 9 | `echo hi >> docs/y.txt` | True | `COMMAND_REFUSED` | `docs/y.txt` | deny (policy) |
| 10 | `tee docs/y.txt` | True | `COMMAND_REFUSED` | `docs/y.txt` | deny (policy) |
| 11 | `cp src/x.txt docs/` | True | `COMMAND_REFUSED` | `docs/` | deny (policy) |
| 12 | `mv docs/y.txt src/` | True | `COMMAND_REFUSED` | `docs/y.txt` | deny (policy) |
| 13 | `sed -i s/a/b/ docs/y.txt` | True | `COMMAND_REFUSED` | `docs/y.txt` | deny (policy) |
| 14 | `cat --file=docs/y.txt` | **False** | **None** | **None** | **allow (fail-open)** |
| 15 | `cat -- docs/y.txt` | True | `OUT_OF_SCOPE` | `docs/y.txt` | deny |
| 16 | `cat $HOME/x` | **False** | **None** | **None** | **allow (fail-open)** |
| 17 | `cat ~/x` | **False** | **None** | **None** | **allow (fail-open)** |
| 18 | `cat /etc/passwd` | True | `COMMAND_REFUSED` | None | deny (policy) |
| 19 | `ls ..` | **False** | **None** | **None** | **allow (fail-open)** |
| 20 | `cd .. && cat docs/y.txt` | True | `COMMAND_REFUSED` | `docs/y.txt` | deny (policy) |
| 21 | `cat {WS}/docs/y.txt` | True | `OUT_OF_SCOPE` | `{WS}/docs/y.txt` | deny |
| 22 | `cat {WS}/src/x.txt` | False | None | None | allow |
| 23 | `cat docs/../src/x.txt` | False | None | None | allow |
| 24 | `cat src2/x.txt` | True | `OUT_OF_SCOPE` | `src2/x.txt` | deny |
| 25 | `""` (empty command) | True | `COMMAND_REFUSED` | None | deny (policy) |

`src2/x.txt` is denied as required: `_scope_hit:82` compares the realpath
against `root/src` by segment boundary, so `src2` is not under `src`. Rows 14,
16, 17 and 19 are the strict xfails below — they assert the human-expected
denial and therefore fail today.

## Security findings

Recorded only in this hand-back and in `hy4-b3-handback-SUMMARY.md`. The new
test file carries only the required one-line xfail reason (exact command plus
wrong output), no analysis. **The gate was not fixed**; deciding whether to
change `shell_gate.py` belongs to the reviewer.

1. **`cat --file=docs/y.txt`** — wrong output: `denied=False`, `code=None`,
   `_scope_hit=None` (the command is allowed). `_scope_hit:73` skips every token
   starting with `-`, and the comment at `:77` says `--flag=path` is not parsed.
   A human reading "may this command touch a file outside `src`?" would say
   deny: the flag carries `docs/y.txt`.
2. **`cat $HOME/x`** — wrong output: `denied=False`, `code=None`,
   `_scope_hit=None`. The env-var spelling is never expanded; `os.path.lexists`
   on the literal `$HOME/x` fails and its parent does not exist, so `:74-78`
   skips it. `policy.validate_command:344-348` blocks only sensitive env names,
   not `$HOME`. A human would say deny.
3. **`cat ~/x`** — wrong output: `denied=False`, `code=None`,
   `_scope_hit=None`. Tilde is not expanded either, so the token is skipped by
   the same branch as finding 2.
4. **`ls ..`** — wrong output: `denied=False`, `code=None`,
   `_scope_hit=None`. `..` resolves outside the workspace root, and
   `_scope_hit:80-81` deliberately `continue`s for anything outside the
   workspace because "confinement already stops writes there". A read of the
   parent directory is not stopped by that write boundary, so a human saying
   "may this command touch a file outside `src`?" would say deny.

All four are `xfail(strict=True)`: they flip to XPASS-and-fail the moment the
gate starts denying them. The reasons name the input and the wrong output as
required.

## Fail-closed notes (not findings)

- `""` (empty command) is denied with `COMMAND_REFUSED` although it touches
  nothing; `policy.validate_command:319-320` refuses it.
- `echo hi > docs/y.txt`, `echo hi >> docs/y.txt`, `tee docs/y.txt`,
  `cp src/x.txt docs/`, `mv docs/y.txt src/`, `sed -i s/a/b/ docs/y.txt` and
  `cd .. && cat docs/y.txt` are refused by the allow-list / shell-syntax layer
  **before** the scope check; the scope layer would also have flagged their
  path tokens (`_scope_hit` values in the table). This is layered defence, not a
  failure.
- `cat /etc/passwd` is denied by `BLOCKED_PATTERNS` (`policy.py:157`), not by
  scope; `_scope_hit` returns None because that absolute path is outside the
  workspace.

## Commands run

```
cd "/Users/user/untitled folder 2/harness/section5" && .venv/bin/python - <<'PY'   # 25-command probe of run()
→ WORKSPACE: /var/folders/.../hy4-b3-probe-f47vu34b
→ 25 JSON lines, one per command (denied / code / hit / reason); the table above is that output
```
```
cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate.py tests/unit/test_shell_gate_scope_edges.py -q --basetemp="$BT"
→ .........ssss..s..s.............x.xx.x..........   [100%]
→ 38 passed, 6 skipped, 4 xfailed in 4.60s   (PYTEST_RC=0)
```
```
cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate.py tests/unit/test_shell_gate_scope_edges.py -q --basetemp="$BT"   (after restore)
→ ................................x.xx.x..........   [100%]
→ 44 passed, 4 xfailed in 5.04s   (PYTEST_RC=0)
```
The six skips are the existing `requires_confinement` tests; whether they skip
depends on whether the command is run inside the sandbox (Seatbelt/Landlock
availability). Both runs are rc=0 and the new file's 4 xfails are identical.
```
cd "/Users/user/untitled folder 2/harness" && make lint > /private/tmp/hy4-b3/make-lint-c2.txt 2>&1
→ MAKE_LINT_RC=0   (all sections "All checks passed!")
```
Plain `make lint` worked; `RUFF_NO_CACHE=true` was not needed on this run.

## Proof the new tests fail without the change (real negative control)

Per the 2b amendment: `shell_gate.py` was saved to a **new** `.mine` path, one
line was mutated with the edit tool, restored with the edit tool, and proved
with `cmp`.

```
cd "/Users/user/untitled folder 2/harness" && cp section5/src/harness_sandbox/shell_gate.py /private/tmp/hy4-b3/shell_gate.mine
→ (new file); pre-control `git status --short -- shell_gate.py` empty
```
Mutation: `_scope_hit`'s `if not scope_paths:` → `if True:` (so it always
returns `None`, i.e. no scope denial at all).
```
cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate_scope_edges.py -q --basetemp="$BT"
→ FAILED test_scope_edge_answer[cat-docs]
→ FAILED test_scope_edge_answer[leading-dot-slash-docs]
→ FAILED test_scope_edge_answer[dotdot-escapes-src]
→ FAILED test_scope_edge_answer[double-quoted-docs]
→ FAILED test_scope_edge_answer[single-quoted-docs]
→ FAILED test_scope_edge_answer[mixed-args-first-outside]
→ FAILED test_scope_edge_answer[single-redirection]
→ FAILED test_scope_edge_answer[append-redirection]
→ FAILED test_scope_edge_answer[tee]
→ FAILED test_scope_edge_answer[cp]
→ FAILED test_scope_edge_answer[mv]
→ FAILED test_scope_edge_answer[sed-in-place]
→ FAILED test_scope_edge_answer[double-dash-then-path]
→ FAILED test_scope_edge_answer[chained-cd]
→ FAILED test_scope_edge_answer[absolute-in-workspace-outside-src]
→ FAILED test_scope_edge_answer[src2-prefix-lookalike]
→ FAILED test_scope_violation_agrees_with_scope_hit[deny-side]
→ FAILED test_scope_violation_agrees_with_scope_hit[prefix-lookalike]
→ 18 failed, 7 passed, 4 xfailed in 2.52s   (NEG_CONTROL_RC=1)
```
Restore proof:
```
cmp section5/src/harness_sandbox/shell_gate.py /private/tmp/hy4-b3/shell_gate.mine
→ (no output, CMP_RC=0)
git status --short -- section5/src/harness_sandbox/shell_gate.py
→ (empty: unchanged from HEAD)
```
No mutated file remains in the tree.

## What is NOT verified
- No command was actually executed: `Popen`, `confine_argv` and `signal.signal`
  are stubbed in the test. These tests pin the **decision layer**, not OS
  confinement, the JS plugin or Landlock (all out of scope).
- Whether the four fail-opens are exploitable against the deployed worker was
  not tested; part52.md:139 says a path built inside `python -c` is stopped only
  by confinement, and no attempt was made to change that.
- The exact check command without `--basetemp` was not run for C2 (section 5's
  tests use `tmp_path`; the known pytest temp-root quirk is why `--basetemp`
  was used). `NEEDS-REVIEWER-RUN` on a normal host:
  `cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate.py tests/unit/test_shell_gate_scope_edges.py -q`,
  expected rc=0 with 4 xfailed.
- Nothing was deployed; no live model run, no paid call.

## Unrelated findings (one line each)
- `_scope_hit:73` skips any token starting with `-`, so `--` is skipped too; it
  is harmless because the following path token is still checked (row 15).
- `policy.validate_command` rejects `tee`/`cp`/`mv`/`sed` as unallowlisted
  binaries, so several "write outside scope" spellings never reach the scope
  layer. The tests pin both answers so a future allow-list change is visible.

## Risks the reviewer should check first
- Rule on the four fail-opens above before anyone changes `shell_gate.py`; the
  strict xfails will fail loudly the moment a fix lands.
- Confirm that stubbing `Popen` and `confine_argv` is acceptable for this
  characterisation suite; it makes the table host-independent but means the
  tests do not prove the OS boundary.
- `ls ..` as a finding is the judgement call most open to disagreement: the
  gate deliberately skips paths outside the workspace because confinement stops
  writes there. It is listed because the human test is "touch a file outside
  `src`", which a read satisfies.

## Scratch files (all under /private/tmp/hy4-*)
- `/private/tmp/hy4-b3/shell_gate.mine` (negative-control restore copy)
- `/private/tmp/hy4-b3/make-lint-c2.txt`
- One `mktemp -d /private/tmp/hy4.XXXX` directory per pytest run, plus the
  probe workspace `/var/folders/.../hy4-b3-probe-f47vu34b` (created by Python's
  `tempfile.mkdtemp(prefix="hy4-b3-probe-")` in the probe script, left in place).
