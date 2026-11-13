# hy4 batch 8 hand-back - K1 (test and skip ledger)

## Assumptions

Snapshot for every task: `7c1acf4 (HEAD -> w28-s1-delegate) D1: a workspace-root scope covers every relative in-workspace path`, created with `mkdir -p /private/tmp/hy4-b8/snap && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"`. Import-path checks passed: `harness_orchestrator` resolved to `/private/tmp/hy4-b8/snap/section4/src/harness_orchestrator/__init__.py` and `harness_contracts` to `/private/tmp/hy4-b8/snap/contracts/src/harness_contracts/__init__.py`. All runs used the main checkout's venvs, PYTHONPATH pointed at the snapshot, `-p no:cacheprovider` and a private `--basetemp` from `mktemp -d /private/tmp/hy4-b8-tmp.XXXX`. No deploys, no docker commands, no git writes, no secrets, no .env reads, no production change.

Assumptions: the ledger groups skips by the exact `-rs` reason string, not by test name; a group is FLAG when the reason ties the skip to an env var that must be set, ENV when the reason describes a missing host capability/binary/platform/venv, DEFECT when it is not environmental, UNKNOWN when the reason text is insufficient; a `resource` column is kept so a reviewer using a resource-first rule can re-bucket. Xfails are counted from the tracked snapshot tree only; the untracked K3 file is deliberately excluded.

Plan:
1. Pin the snapshot and verify the import path -> verify: `cat-file -t 7c1acf4` prints `commit`; both module `__file__` values start with `/private/tmp/hy4-b8/snap`.
2. Run all three suites with `-rs` and save the outputs -> verify: each count line in the ledger matches a saved pytest summary line.
3. Classify every reason string and locate each marker -> verify: `grep -c '| '` equals the group count and each section's group counts sum to its skipped total.
4. Write the ledger to `docs/briefs/evidence/rc/` only -> verify: `cmp` draft vs delivered.

## Times

Start 2026-11-06 23:42:34 PST (`date`, session start). Finish 2026-11-07 00:40:39 PST (mtime of the delivered ledger, OS clock; `date` at end of batch 2026-11-07 08:06:27 PST). Duration 00:58:05. The session was interrupted once during this task; the wall time includes that gap.

## Commands run

```
REPO="/Users/user/untitled folder 2/harness"; SNAP="/private/tmp/hy4-b8/snap"
mkdir -p "$SNAP" && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"
git -C "$REPO" cat-file -t 7c1acf4
git -C "$REPO" log --oneline -1 7c1acf4
cd "$SNAP/section4" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -c "import harness_orchestrator, sys; print(harness_orchestrator.__file__)"
cd "$SNAP/section5" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section5/src" "$REPO/section5/.venv/bin/python" -c "import harness_contracts, sys; print(harness_contracts.__file__)"
cd "$SNAP/section1" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src" "$REPO/section1/.venv/bin/python" -m pytest tests -q -rs -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section4" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -m pytest tests -q -rs -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section5" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section5/src" "$REPO/section5/.venv/bin/python" -m pytest tests -q -rs -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section1" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src" "$REPO/section1/.venv/bin/python" -m pytest tests -v -rs -rx -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section4" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -m pytest tests -v -rs -rx -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section5" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section5/src" "$REPO/section5/.venv/bin/python" -m pytest tests -v -rs -rx -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
```

Pytest summary lines (run 1, then the identical run 2):
```
section1 run1: 1 failed, 1236 passed, 66 skipped, 1719 warnings in 116.83s (0:01:56)
section1 run2: 1 failed, 1236 passed, 66 skipped, 1719 warnings in 123.39s (0:02:03)
section4 run1: 3 failed, 2304 passed, 60 skipped, 26144 warnings in 149.27s (0:02:29)
section4 run2: 3 failed, 2304 passed, 60 skipped, 26165 warnings in 164.73s (0:02:44)
section5 run1: 14 failed, 429 passed, 33 skipped, 4 xfailed, 1 warning, 18 errors in 42.31s
section5 run2: 14 failed, 429 passed, 33 skipped, 4 xfailed, 1 warning, 18 errors in 46.95s
```

## Results

Delivered: `docs/briefs/evidence/rc/skip-ledger-2026-11-06.md` (only file written in the main checkout for K1). `cmp /private/tmp/hy4-b8/skip-ledger-draft.md "$F"` -> `IDENTICAL`.

Verify output pasted:
```
=== verify: grep -c '| ' ===
44
=== groups listed in section 4 ===
- section1: 1+25+2+2+1+1+1+3+2+2+6+1+9+1+1+1+1+4+1+1 = 66; pytest summary says 66 skipped. Match: True. Groups in section: 20.
- section4: 17+1+1+9+2+1+5+2+4+2+1+1+2+3+2+1+2+4 = 60; pytest summary says 60 skipped. Match: True. Groups in section: 18.
- section5: 1+11+7+8+4+2 = 33; pytest summary says 33 skipped. Match: True. Groups in section: 6.
```
44 group rows = 20 + 18 + 6; 66 + 60 + 33 = 159. Class totals: FLAG 121, ENV 38, DEFECT 0, UNKNOWN 0.

Xfails: section5 has 4, all strict xfails from `section5/tests/unit/test_shell_gate_scope_edges.py:59-63`; section4 and section1 have no xfail marker on this commit. The reviewer's 14 xfailed is consistent with 4 tracked + 10 strict xfails in the untracked K3 file.

Hand-back sentence required by the task: FLAG is the largest class (121 of 159 skipped tests, with 38 ENV), and no DEFECT group exists at all.

Numbers differ from the reviewer's approximate main-checkout totals (1240/63, 2308/59, 507/8/14). Two setup causes: (a) this host is darwin with no usable OS confinement backend, producing 25 extra confinement skips plus 14 failures and 18 errors in section5; (b) the untracked `test_shell_gate_real_boundary.py` (4 defs / 31 cases, 10 of them xfails) is not in the archive snapshot. 33 - 25 = 8, matching the reviewer's 8 skips; 4 + 10 = 14, matching their xfail count.

## Failures and NEEDS-REVIEWER-RUN

Failed runs: all six suite runs exited 1 (non-zero) because of failing tests, not because the command could not run. The failing tests, all environment/snapshot artifacts, are:
- section1 (1): `test_contracts_version_pinned_across_all_four_sources` - `Installed harness-contracts 1.0.0rc3 != source-tree version 1.0.0rc7, and it is NOT a local install of this tree`. The venv's installed dist metadata is at rc3 while the snapshot source is rc7; `_installed_from_this_tree()` compares against the main-checkout path, so running from the snapshot makes it conclude the install is foreign.
- section4 (3): `test_seatbelt_isolates_concurrent_runs` (`PermissionError: [Errno 1] Operation not permitted: '/Users/user/pwn_from_run.txt'`), `test_two_concurrent_runs_cannot_touch_each_other` (`assert 'error' == 'completed'`), `test_an_unreachable_daemon_fails_the_worker_check` (`assert 'WORKER_ERROR' == 'WORKER_UNREACHABLE'`). These need the Seatbelt/worker-daemon boundary this host lacks; the tool sandbox also refused a file-delete at that home path. I did not touch that path.
- section5 (14 failed + 18 errors): all trace to `SecurityPolicyViolationError: sandbox confinement 'auto' found no usable OS boundary on this host (platform darwin); refusing to run unconstrained` raised from `src/harness_sandbox/local_runner.py:95`. The 18 errors are setup errors in `tests/conformance/test_tree_api.py` and `TestTeamScopingAndAuth` fixtures; the 14 failures are the edit/snapshot/exec tests.
I did not set `HARNESS_SANDBOX_CONFINEMENT=off` / `HARNESS_SANDBOX_ALLOW_UNCONFINED=1` to make them pass: that is a security-posture escape hatch and working around drift is out of scope. No fix was attempted.

NEEDS-REVIEWER-RUN: none for K1's own checks. The interpretation of whether the 121 FLAG skips should instead be counted as ENV is a reviewer call; the ledger's `resource` column exists for that.

## Security findings

Not applicable to this task (read and classify only); none raised.

## Least sure of

I am least sure of the FLAG/ENV boundary: 121 of 159 skips are gated by an env var but also need a real PostgreSQL or Docker service, so a resource-first rule would make ENV the largest class instead of FLAG.
