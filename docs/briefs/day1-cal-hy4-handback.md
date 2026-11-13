# day1-cal-hy4 hand-back — `stack_tags.py` exit-code calibration

## Assumptions

- `NAME: <hy4 | hy3 | opencode-<model>>` and `WORKTREE: <../harness-wb-hy4| ../harness-wb-hy3| ../harness-wb-oc>` arrived as **literal, unsubstituted placeholders**. No `harness-wb-hy4`, `harness-wb-hy3` or `harness-wb-oc` directory exists (checked with `git worktree list` and targeted `ls -d`). I used `NAME=hy4` (the first listed variant) and treated the only checkout that actually contains `scripts/ops/stack_tags.py` — the primary git worktree at `/Users/user/untitled folder 2/harness` — as "the worktree". All work stayed inside that checkout.
- **BLOCKED:** the literal `WORKTREE` path could not be used because it does not exist. I did not create one: `git worktree add` is a git write (branch/worktree metadata), which the hard limits forbid. No commit, push, reset, stash or branch was run.
- A `harness-wb-codex` worktree and ~42 `/private/tmp/hy4.*` basetemp directories appeared while I worked; they belong to other concurrent workers and were not touched. The `hy4` prefix in `/private/tmp` is therefore shared; the four directories *I* created are listed explicitly below.
- Step 5's "change main()" meant a **temporary edit to the worktree's `scripts/ops/stack_tags.py`**, restored byte-for-byte with the edit tool. `/private/tmp/hy4-stack_tags.mine` is the untouched `cp` reference used for `cmp`; it is not the file under test.
- `main()` was **not** permanently changed: the module docstring's "this script always exits 0" still holds, and whether to change the exit code is the reviewer's ruling. T1 is a strict xfail, not a fix.
- Constraints observed: $0 spend, no docker command executed, no git writes, no secrets/`.env` contents read or written, no files created outside the task-supplied worktree except under `/private/tmp` (all temp artifacts live there). Paths under `/Users/user/untitled folder 2/harness` are only the task-supplied worktree and venv paths.

## Times

- Start: 2026-11-06 02:09:40 PST (epoch 1793959780)
- Finish: 2026-11-06 02:21:23 PST — final `date` after writing this hand-back. Last verification command (`date` + `shasum`) ran at 02:19:50 PST (epoch 1793960390).

## Files changed

- **NEW** `scripts/ops/test_stack_tags_exit.py` — one file, three tests. Uses `monkeypatch.setattr(stack_tags.subprocess, "run", ...)`, matching `test_stack_tags.py`.
  - T1 `test_main_should_exit_nonzero_when_docker_ps_fails`: `docker ps` rc=1, stderr `Cannot connect to the Docker daemon`; asserts `main() != 0`; decorated `@pytest.mark.xfail(strict=True, reason="input docker ps rc=1 'Cannot connect to the Docker daemon' wrongly yields main() == 0 instead of a non-zero exit")` (one line naming input and wrong output).
  - T2 `test_failed_docker_ps_still_prints_the_report`: same input; asserts `"docker ps exited 1" in out` and that a printed line starts with `Stack tag report`. Does not assert the return value, so it survives the negative control.
  - T3 `test_missing_docker_binary_raises_filenotfounderror`: patched `run` raises `FileNotFoundError`; `with pytest.raises(FileNotFoundError): main()`. One comment line: `# Current behaviour, not a decision about what should happen.`
  - The success path is not repeated.
- **TEMPORARILY** `scripts/ops/stack_tags.py`: `return 0` → `return 1 if result.returncode != 0 else 0` → `return 0`. Final sha256 `cb21877bd5173762a173bb3fea858d2b17da8cfcf711128c82ec065c570fe65a`, identical to the `cp` reference; `git diff -- scripts/ops/stack_tags.py` is empty.
- **NEW** `docs/briefs/day1-cal-hy4-handback.md` — this file.
- No other existing file was modified.

## Commands (with exit codes)

The three pytest runs used this exact inner command, from the worktree root (wrapped in a `BEFORE`/`AFTER` `ls -d /private/tmp/hy4.*` diff solely to record which `mktemp -d` directory was created):

```
cd scripts/ops && "/Users/user/untitled folder 2/harness/section4/.venv/bin/python" -m pytest test_stack_tags.py test_stack_tags_exit.py -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"
```

| # | command | exit code / result |
|---|---|---|
| 1 | `date "+%Y-%m-%d %H:%M:%S %Z"` | 0 — 02:09:40 PST |
| 2 | `git -C "/Users/user/untitled folder 2/harness" worktree list` | 0 — no `harness-wb-hy4/hy3/oc` worktree |
| 3 | `ls -d "/Users/user/untitled folder 2"/harness-wb-*` (in loop) | 1 — no matches |
| 4 | `"/Users/user/untitled folder 2/harness/section4/.venv/bin/python" -m pytest --version` | 0 — pytest 9.1.1 |
| 5 | broad `find /Users/user /private/tmp /tmp -maxdepth 5/8 ...` | terminated by SIGTERM (137); produced no results and was not relied upon |
| 6 | exact pytest command, **baseline** | **0** — `10 passed, 1 xfailed` (8 existing pass, T2/T3 pass, T1 xfailed) |
| 7 | `cp scripts/ops/stack_tags.py /private/tmp/hy4-stack_tags.mine` | 0 |
| 8 | `git status --short` (baseline, before edit) | 0 — 4 untracked entries, 3 pre-existing + my new test file |
| 9 | edit tool: `return 0` → `return 1 if result.returncode != 0 else 0` | not a shell command; no rc |
| 10 | exact pytest command, **negative control** | **1** — `1 failed, 10 passed` |
| 11 | `... -m pytest test_stack_tags_exit.py -v -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"` (supplementary per-test evidence) | **1** — `1 failed, 2 passed` |
| 12 | edit tool: restore `return 0` | not a shell command; no rc |
| 13 | `cmp scripts/ops/stack_tags.py /private/tmp/hy4-stack_tags.mine` | **0** (no output) |
| 14 | `git diff -- scripts/ops/stack_tags.py` | 0 — empty output |
| 15 | `git status --short` (after restore) | 0 — see BLOCKED note below |
| 16 | exact pytest command, **after restore** | **0** — `10 passed, 1 xfailed` |
| 17 | `shasum -a 256 scripts/ops/stack_tags.py /private/tmp/hy4-stack_tags.mine scripts/ops/test_stack_tags_exit.py` | 0 — source and copy hashes identical |
| 18 | `date "+%Y-%m-%d %H:%M:%S %Z"` | 0 — 02:19:50 PST |
| 19 | `date "+%Y-%m-%d %H:%M:%S %Z"` (after writing this hand-back) | 0 — 02:21:23 PST |
| 20 | `git status --short` (final, after writing this hand-back) | 0 — the two new files of mine plus the 3 pre-existing untracked entries |

**BLOCKED:** `git status --short` could not show *only* my new test file. The shared primary checkout already had 3 unrelated untracked entries from other workers, and a modified `shell_gate.py` that another worker changed/removed mid-run. Output at step 15 was:

```
?? docs/plans/
?? docs/yc/
?? scripts/ops/test_stack_tags_exit.py
?? section5/tests/unit/test_shell_gate_real_boundary.py
```

Verified instead: `scripts/ops/stack_tags.py` appears nowhere in `git status --short` and `git diff -- scripts/ops/stack_tags.py` is empty, so the negative control left no diff; my test file is the only entry newly added by me relative to the baseline at step 8.

## Negative-control output

Exact pytest command with `main()` returning 1 on `rc != 0` (rc 1):

```
........F..                                                              [100%]
=================================== FAILURES ===================================
______________ test_main_should_exit_nonzero_when_docker_ps_fails ______________
[XPASS(strict)] input docker ps rc=1 'Cannot connect to the Docker daemon' wrongly yields main() == 0 instead of a non-zero exit
----------------------------- Captured stdout call -----------------------------
docker ps exited 1: Cannot connect to the Docker daemon
Stack tag report (read-only: docker ps)

Running services:

Backend services: 0 of 11 expected running
Missing: events-retention, github, identity, meeting, models, orchestrator, review, sandbox, sandbox-egress, siem-push, webhook-delivery
Agreement: UNKNOWN (no backend containers found)
Worker daemon: not running
Read-only: no container, image, volume or compose state was changed.
=========================== short test summary info ============================
FAILED test_stack_tags_exit.py::test_main_should_exit_nonzero_when_docker_ps_fails - [XPASS(strict)] input docker ps rc=1 'Cannot connect to the Docker daemon' ...
1 failed, 10 passed in 0.07s
PYTEST_RC=1
```

Supplementary verbose run of the new file (rc 1) — proves T2 and T3 still pass while T1 fails:

```
test_stack_tags_exit.py::test_main_should_exit_nonzero_when_docker_ps_fails FAILED [ 33%]
test_stack_tags_exit.py::test_failed_docker_ps_still_prints_the_report PASSED [ 66%]
test_stack_tags_exit.py::test_missing_docker_binary_raises_filenotfounderror PASSED [100%]
========================= 1 failed, 2 passed in 0.06s ==========================
VERBOSE_RC=1
```

After restoring: exact pytest command → `........x.. [100%]  10 passed, 1 xfailed in 0.09s`, `PYTEST_RC=0`.

## cmp result

```
=== cmp ===
CMP_RC=0
=== git diff for stack_tags.py (expect empty) ===
GIT_DIFF_RC=0
```

`cmp` produced no output and exited 0: the restored `scripts/ops/stack_tags.py` is byte-for-byte identical to the pristine `cp` copy. `shasum -a 256` gave the same hash for both (`cb21877b…`). No `cp` was used to restore — the edit tool was used, as required.

## What is NOT verified

- No real `docker ps`, no real daemon failure and no real missing docker binary were exercised. Every case is a monkeypatched `subprocess.run`; no docker command was run (forbidden by the task).
- Whether `main()` **should** exit non-zero is not decided here — that is the reviewer's ruling. T1 intentionally stays `xfail(strict=True)` against today's `return 0`.
- The `if __name__ == "__main__": raise SystemExit(main())` path (process exit status of the script) was not tested; only `main()` directly. No monitoring script was run.
- Other failure shapes are untested: rc other than 1, empty/`None` stderr, non-empty stdout on failure, `stderr` with trailing whitespace, `TimeoutExpired`, `PermissionError`, `OSError` subclasses.
- The success path (`test_stack_tags.py`, 8 tests) was only re-run, never modified or re-asserted with new tests, per instructions.
- The three named `../harness-wb-*` worktrees were never created or checked out; the literal "work only inside your WORKTREE" and "git status shows only your new test file" conditions were therefore not satisfiable as written (see BLOCKED). The hand-back is in the primary checkout, not a `harness-wb-*` tree.
- `cmp` compares against my own `cp` copy, not a git blob; independent confirmation comes from the empty `git diff -- scripts/ops/stack_tags.py`.
- No linter, type checker, coverage tool, `git commit/push/reset/stash/branch`, `git worktree add`, `docker`, `rm`, or network/paid API call was run. I did not run the tests from a `harness-wb-*` worktree, did not inspect or touch `harness-wb-codex` or the other workers' `/private/tmp/hy4.*` directories, and did not read any `.env` file.

## /private/tmp paths I created

- `/private/tmp/hy4.I58Q` — basetemp for baseline exact pytest run (rc 0)
- `/private/tmp/hy4.qhmF` — basetemp for negative-control exact pytest run (rc 1)
- `/private/tmp/hy4.ddSQ` — basetemp for the supplementary verbose run (rc 1)
- `/private/tmp/hy4.WjkV` — basetemp for post-restore exact pytest run (rc 0)
- `/private/tmp/hy4-stack_tags.mine` — pristine `cp` of `scripts/ops/stack_tags.py`, left in place (not deleted)

The ~42 other `/private/tmp/hy4.*` directories present before/while I worked were created by another concurrent worker; I did not create, modify or delete them. No other `/private/tmp` paths were created by me.
