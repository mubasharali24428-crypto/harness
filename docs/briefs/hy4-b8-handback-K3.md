# hy4 batch 8 hand-back - K3 (shell-gate real-boundary review)

## Assumptions

Snapshot for every task: `7c1acf4 (HEAD -> w28-s1-delegate) D1: a workspace-root scope covers every relative in-workspace path`, created with `mkdir -p /private/tmp/hy4-b8/snap && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"`. Import-path checks passed: `harness_orchestrator` resolved to `/private/tmp/hy4-b8/snap/section4/src/harness_orchestrator/__init__.py` and `harness_contracts` to `/private/tmp/hy4-b8/snap/contracts/src/harness_contracts/__init__.py`. All runs used the main checkout's venvs, PYTHONPATH pointed at the snapshot, `-p no:cacheprovider` and a private `--basetemp` from `mktemp -d /private/tmp/hy4-b8-tmp.XXXX`. No deploys, no docker commands, no git writes, no secrets, no .env reads, no production change.

Assumptions: "one row per test function" means one row for each `def test_`, with parametrised cases listed under their function; the review describes assertions and markers only and contains no conclusion about the gate's enforcement behaviour; the file under review is copied into the snapshot with `cp` and is never edited in either location; fail-open observations belong here, not in the review file.

Plan:
1. Copy the untracked file into the snapshot and prove the copy -> verify: `cmp` before and after the review says `UNCHANGED`.
2. Run it and record the split and the module-level skip -> verify: three saved pytest summary lines and the verbatim skip reason.
3. Write one row per `def test_` and check every xfail mark -> verify: `grep -c 'def test_'` = 4 and 4 rows in the review; strict=True on all 10 marks.
4. Write the review to the evidence dir only -> verify: no test file modified.

## Times

Start 2026-11-07 07:53:22 PST (after the K2 draft mtime). Finish 2026-11-07 07:57:02 PST (mtime of the delivered review). Duration 00:03:40. `date` at end of batch: 2026-11-07 08:06:27 PST.

## Commands run

```
REPO="/Users/user/untitled folder 2/harness"; SNAP="/private/tmp/hy4-b8/snap"
cp "$REPO/section5/tests/unit/test_shell_gate_real_boundary.py" "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"
cmp "$REPO/section5/tests/unit/test_shell_gate_real_boundary.py" "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"
grep -c 'def test_' "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"
grep -n 'def test_' "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"
grep -nE "xfail|strict" "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"
cd "$SNAP/section5" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section5/src" "$REPO/section5/.venv/bin/python" -m pytest tests/unit/test_shell_gate_real_boundary.py -q -rs -rx -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section5" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section5/src" "$REPO/section5/.venv/bin/python" -m pytest tests/unit/test_shell_gate_real_boundary.py --collect-only -q -p no:cacheprovider
cd "$SNAP/section5" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section5/src" "$REPO/section5/.venv/bin/python" -m pytest tests/unit/test_shell_gate_real_boundary.py -rs -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
```

Pytest summary lines:
```
run1 (-q -rs -rx): 31 skipped in 0.35s
run2 (-q -rs -rx, fresh basetemp): 31 skipped in 0.28s
run3 (-rs): ============================= 31 skipped in 0.25s ==============================
collect-only: 31 tests collected in 0.24s
```
The module-level `requires_confinement` skip DOES apply on this host. Reason verbatim: `no OS confinement on this host`, from `section5/tests/unit/test_shell_gate.py:14-16`. Run 3 short summary: `SKIPPED [7] :129`, `SKIPPED [1] :187`, `SKIPPED [8] :200`, `SKIPPED [15] :266` (7+1+8+15 = 31).

## Results

Delivered: `docs/briefs/evidence/rc/shell-gate-real-boundary-review-2026-11-06.md`. The test file is unmodified: `cmp "$REPO/.../test_shell_gate_real_boundary.py" "$SNAP/.../test_shell_gate_real_boundary.py"` -> `UNCHANGED`.

Verify output pasted:
```
=== def test_ count ===
4
=== def test_ grep (paste) ===
130:def test_write_boundary(boundary, rid, command, scope, spelling, expect_created, code):
187:def test_boundary_runtime_is_recorded():
201:def test_read_boundary(boundary, monkeypatch, rid, command, scope, home):
267:def test_expansion_and_option_forms(boundary, rid, command, scope):
=== xfail / strict markers ===
146:def read_xfail(command: str, wrong: str):
147:    return pytest.mark.xfail(
148:        sys.platform == "darwin", strict=True,
181:        marks = [read_xfail(command, wrong)] if rid in _R_FAIL_OPEN else []
217:def leak_xfail(command: str, wrong: str):
218:    return pytest.mark.xfail(
219:        strict=True,
261:        marks = [leak_xfail(command, wrong)] if wrong else []
```
The review has 4 rows, one per `def test_`; parametrised cases (7 / 1 / 8 / 15) are listed under their function. The prompt's dry run (31 collected, 21 passed, 10 xfailed) could not be reproduced here because all 31 skip.

Xfails: 10 marks, all from two factories, both `strict=True`. `read_xfail` (line 146-150, condition `sys.platform == "darwin"`, 6 rows R2-R7) and `leak_xfail` (line 217-220, unconditional, 4 rows G2/G6a/G6b/G7i). Both reasons describe a symptom (the observed result and the human-expected answer) and name no fix, owner or defect id. List of xfails without strict=True: none.

One structural finding kept in the review (not a security finding): `test_boundary_runtime_is_recorded`'s docstring says it runs even where confinement is unavailable, but the module-level `pytestmark = requires_confinement` skips it too; run 3 confirms it is 1 of the 31 skips.

## Failures and NEEDS-REVIEWER-RUN

No failed runs: all three pytest runs exited 0. The gap is that no assertion executes on this host because every case skips before the fixture or gate is called.

NEEDS-REVIEWER-RUN: the `.mine` mutation of `shell_gate.py` used to prove each assertion can fail. A mutation cannot change the outcome of a skipped test, so this needs a host with a usable Seatbelt or Landlock backend (the dry-run environment that produced 21 passed / 10 xfailed). The review records the assertion-failability column as a static reading of the asserts plus the observed collection outcome and marks all four rows NEEDS-REVIEWER-RUN for that check. Also NEEDS-REVIEWER-RUN: reproducing the 21/10 split itself.

## Security findings

The file under review is security-path, so all findings about the gate going fail-open are here and nowhere else.

1. Ten `strict=True` xfails in the file assert answers the gate does not meet on darwin. `read_xfail` says six read rows (R2-R7, absolute canary read, canary listing, parent-directory listing, relative canary read, `~` and `$HOME` reads with the canary as HOME) come back `denied=False, exitCode=0` on macOS; `leak_xfail` says four expansion rows (G2 `cat d?cs/y.txt`, G6a/G6b recursive grep, G7i a `python3 -c` string) let the docs marker into the output. In plain terms: on this platform the gate can allow reads outside the workspace and outside the intent scope without denying them, i.e. fail open for reads, including via glob expansion, recursive grep and interpreter code strings. The reasons name no fix, owner or defect id.
2. The four tracked strict xfails in `section5/tests/unit/test_shell_gate_scope_edges.py` (K1 section 5) independently record the same shape of symptom for `cat --file=docs/y.txt`, `cat $HOME/x`, `cat ~/x` and `ls ..`: the gate returns `denied=False, code=None, hit=None` where a denial is expected.
3. W5 (`echo hi > docs/y.txt`, scope `["src"]`) expects `created=True` with `COMMAND_REFUSED`; the target file already exists from the fixture (line 88), so that existence check is satisfied before any command runs. The row's real protection is the separate assertion that `docs/y.txt` content is unchanged. This is a weak-assertion observation, not a demonstrated bypass.
4. `test_expansion_and_option_forms` never inspects `denied`, `code` or `exitCode`; it checks only output text and canary contents, so a command that fails for an unrelated reason would still pass.

I made no ruling on any of these, changed no code, and did not put any of this text in the review file. Section 5's 14 failures / 18 errors in K1 are the opposite posture (the runner refuses to run unconstrained, fail closed) and are not the same finding.

## Least sure of

I am least sure of the assertion-failability column, because all 31 cases skip on this host and no assertion was actually exercised.
