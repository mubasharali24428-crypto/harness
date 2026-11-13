# Review: section5/tests/unit/test_shell_gate_real_boundary.py - 2026-11-06 (hy4 batch 8, K3)

Review only. The file under review is untracked in the main checkout and is not in the pinned commit `7c1acf4`; it was copied into the snapshot with `cp` so the review ran on a fixed copy. The copy was proven identical with `cmp` before and after the review, and neither copy of the test file was edited. Snapshot: `/private/tmp/hy4-b8/snap`. Interpreter: the main checkout's `section5/.venv/bin/python` with `PYTHONPATH=/private/tmp/hy4-b8/snap/contracts/src:/private/tmp/hy4-b8/snap/section5/src`; each run used a private `--basetemp` and `-p no:cacheprovider`.

Assumptions: "one row per test function" means one row for each `def test_` in the module; parametrised cases are listed under their function, not as extra rows; the review describes assertions and markers only and deliberately contains no conclusion about the gate's enforcement behaviour.

## 1. Run results

| Run | Command | Result |
|-|-|-|
| 1 | `pytest tests/unit/test_shell_gate_real_boundary.py -q -rs -rx -p no:cacheprovider --basetemp=<private>` | `31 skipped in 0.35s` - 0 passed, 0 failed, 0 xfailed |
| 2 | same command, fresh private basetemp | `31 skipped in 0.28s` |
| 3 | `pytest tests/unit/test_shell_gate_real_boundary.py -rs -p no:cacheprovider --basetemp=<private>` | `============================= 31 skipped in 0.25s ==============================` |

Collection: `pytest --collect-only -q` reports `31 tests collected in 0.24s`. The prompt's dry run reported 31 collected with 21 passed and 10 xfailed; on this host all 31 are skipped, so that split could not be reproduced here.

**Module-level skip applies on this host: yes.** `pytestmark = requires_confinement` at line 29 imports `requires_confinement` from `tests/unit/test_shell_gate.py`; the marker is defined at `section5/tests/unit/test_shell_gate.py:14-16` as `pytest.mark.skipif(not (confinement.seatbelt_available() or confinement.landlock_supported()), reason="no OS confinement on this host")`. Run 3's short summary shows the reason verbatim for all four functions:

```
SKIPPED [7] tests/unit/test_shell_gate_real_boundary.py:129: no OS confinement on this host
SKIPPED [1] tests/unit/test_shell_gate_real_boundary.py:187: no OS confinement on this host
SKIPPED [8] tests/unit/test_shell_gate_real_boundary.py:200: no OS confinement on this host
SKIPPED [15] tests/unit/test_shell_gate_real_boundary.py:266: no OS confinement on this host
```

The four counts are 7 + 1 + 8 + 15 = 31, matching collection. One consequence worth recording: `test_boundary_runtime_is_recorded`'s docstring (lines 187-193) says it "does not execute a command, so it runs even where confinement is unavailable", but the module-level `pytestmark` skips it too. Run 3 confirms it is 1 of the 31 skips, so the docstring does not hold on a host without a usable OS boundary.

## 2. One row per test function

Because all 31 cases skip before any fixture or gate call runs, no assertion executes on this host. The "can the assertion fail" column below is therefore a static reading of the assert statements plus the observed collection outcome; a mutation-based confirmation on a `.mine` copy of `shell_gate.py` is recorded as NEEDS-REVIEWER-RUN in the hand-back, since no mutation of the gate can change a skipped test's outcome.

| # | Function (line) | Parametrised cases | What it asserts, in plain words | Can the assertion fail? How checked | Assertions that only check a return code |
|-|-|-|-|-|-|
| 1 | `test_write_boundary` (line 130) | 7: W1-W7 (line 108-126) | That whether the target file was created matches the row's `expect_created`; that `docs/y.txt` is byte-for-byte unchanged; that the docs marker never appears in the command output; for W5 that `code == "COMMAND_REFUSED"`; for W1 that `exitCode == 0` and the new file contains `ok`; for W6, when git is unavailable, that the result is denied or has a non-zero exit. | Yes in principle - `created is expect_created` and the file-content comparisons read real filesystem state, so a change in what the gate allows changes the outcome. Checked by reading lines 130-143; not exercised here because all 7 rows skip. A `.mine` mutation of `shell_gate.py` would still show 7 skips on this host, so that confirmation is NEEDS-REVIEWER-RUN on a confined host. | The W6 branch (line 142-143) asserts only `denied or exitCode not in (0, None)` with no filesystem check in that same branch. W5's `code == "COMMAND_REFUSED"` (line 138-139) is a code check, but the row also checks file state, so it is not code-only. |
| 2 | `test_boundary_runtime_is_recorded` (line 187) | 1 (no parametrisation) | That the `runtime()` dict reports the interpreter's own `sys.platform`, that its `backend` value is one of `seatbelt`, `landlock`, `none`, and that the dict has exactly those two keys. | Yes in principle - renaming a key or widening the backend set would break all three asserts. Checked by reading lines 187-197 and by the observed `SKIPPED [1]` in run 3, which also shows the module-level marker overrides this test's docstring claim. | None; the test inspects no result dict from a command at all. |
| 3 | `test_read_boundary` (line 201) | 8: R1-R8 (line 155-172) | That the canary marker never appears in the output and that the result is `blocked`; for R1 that `code == "OUT_OF_SCOPE"`; for R8 that `code == "COMMAND_REFUSED"`; that `runtime()["sys.platform"]` matches `sys.platform`. | Yes in principle - the marker comparison reads real output and `blocked` is derived from the result, so a change in gate output changes the outcome; the presence of six strict xfail marks on R2-R7 in the file itself shows these asserts are expected to fail on darwin. Checked by reading lines 201-214 and the marks at 174-183; not exercised here because all 8 rows skip. NEEDS-REVIEWER-RUN for a `.mine` mutation check. | `blocked` (line 206) is computed solely from `denied` and `exitCode`; any unrelated non-zero exit satisfies it. The R1 and R8 code checks (lines 209-212) are also code-only, though each row still carries the marker comparison. |
| 4 | `test_expansion_and_option_forms` (line 267) | 15: G1, G2, G3, G4, G5, G6a, G6b, G7a, G7b, G7c, G7d, G7e, G7f, G7i, G7k (line 226-254) | That the docs marker never appears in the output, and that `plain-notes.txt` in the canary directory still reads back exactly as written. | Yes in principle - a marker in the output would break the first assert; four strict xfail marks on G2, G6a, G6b and G7i in the file itself state those rows do not meet it. Checked by reading lines 266-272 and the marks at 257-263; not exercised here because all 15 rows skip. NEEDS-REVIEWER-RUN for a `.mine` mutation check. | No return-code assertion at all: the function never reads `denied`, `code` or `exitCode`. It checks output text and one file's contents only. |

No assertion in this module is a bare `assert res.get("code") == ...` standing alone: every row that inspects a code also inspects output text or filesystem state. The two places where a result-code check is the only evidence for a specific sub-claim are listed in the last column above.

## 3. xfail reasons

Ten parametrised cases carry an xfail mark: six read rows (the six-row set at line 174, applied by `r_params()` at line 177-184) and four expansion rows (G2, G6a, G6b, G7i, selected by `g_params()` at line 257-263). This matches the 10 xfailed in the prompt's dry run.

| Marker | Line | strict | Reason shape | Symptom or fix? |
|-|-|-|-|-|
| `read_xfail` | 146-150 | `strict=True` | A one-line `rule 7: ...` reason naming the command, the darwin platform and an observed result versus the human-expected answer. | Symptom. It names no fix, owner, defect id or remediation. Condition is `sys.platform == "darwin"`, so on other platforms no xfail mark is attached at all. |
| `leak_xfail` | 217-220 | `strict=True` | A one-line `rule 7: ...` reason naming the command, an in-workspace file and an observed `<wrong>` string. | Symptom. It names no fix, owner, defect id or remediation. Unconditional. |

**Does a strict=True marker exist on every xfail?** Yes - both factories set `strict=True` and both are the only two `pytest.mark.xfail` call sites in the file (`grep -nE "xfail|strict"` returns lines 10, 146, 147, 148, 181, 217, 218, 219, 261). All ten marks come from these two factories. **List of xfails without strict=True: none.** Note that on this host no xfail is evaluated because every case skips before the mark can take effect.

## 4. Verification

- `grep -c 'def test_' "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"` -> `4`.
- `grep -n 'def test_'` output (paste):
  ```
  130:def test_write_boundary(boundary, rid, command, scope, spelling, expect_created, code):
  187:def test_boundary_runtime_is_recorded():
  201:def test_read_boundary(boundary, monkeypatch, rid, command, scope, home):
  267:def test_expansion_and_option_forms(boundary, rid, command, scope):
  ```
- This review has exactly 4 rows in section 2, one per `def test_`; the 7 / 1 / 8 / 15 parametrised cases are listed under their function, not as extra rows.
- `pytest --collect-only -q` -> `31 tests collected in 0.24s`, higher than the 4 function count because of parametrisation.
- `cmp "$REPO/section5/tests/unit/test_shell_gate_real_boundary.py" "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"` -> `UNCHANGED`; the test file was not modified in either location.
