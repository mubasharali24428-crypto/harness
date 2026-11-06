# hy4 hand-back — A2

Task: A2 — the flaky `test_reclaimed_run_fences_the_original_worker`
Status: DONE
Assumptions: the flake is load-dependent and reproducible on this host within 60 loaded runs; the section 4 venv is usable from the repo root; running the test file alone does not rewrite the tracked W21 evidence file.

## Reproduction and cause

**Reproduced under load, before any change.** The failing assertion is
`assert await replica_b.reap_zombies() == [RUN]` at
`section4/tests/unit/test_lifecycle_recovery.py:180`, i.e. `[] == ['life-run']`.
It is **not** the 5 s wait at line 187 that `STATUS.md:927`/`:963` guesses: the
code wins over the prompt here, so the STATUS guess is wrong for this reproduction.

Root cause is a **test bug**, not a code race:

* The docstring says "A's heartbeat stalls", but the test never stalls it. It only
  monkeypatches `_HEARTBEAT_INTERVAL_SECONDS` to `0.05` (making A's heartbeat
  *faster*) and then ages the claim row to year 2000 with `_age_claim`.
* `service.py:1530-1549` runs A's heartbeat task every 50 ms;
  `run_queue.py:299-313` `heartbeat_run` does
  `UPDATE run_claims SET heartbeat_at = :now WHERE ... AND worker_id = :worker_id`.
* `run_queue.py:111-141` `release_timed_out_claims` only deletes rows whose stored
  `heartbeat_at` is stale by the DB clock.
* `_age_claim` and `reap_zombies` are separate `await`s on the shared aiosqlite
  store. Under CPU load, A's heartbeat tick lands in that window and refreshes
  `heartbeat_at` to *now*, so B's reaper finds nothing stale and returns `[]`.
  Unloaded, the window is too small for a 50 ms tick, which is why the test passes
  40/40 alone.

I did not instrument individual `heartbeat_run` calls (see "NOT verified"), so the
mechanism is established by reading the three code sites above plus the exact
failure, not by a call trace.

## The change

`section4/tests/unit/test_lifecycle_recovery.py`, only inside
`test_reclaimed_run_fences_the_original_worker`:

* Added a monkeypatched `service_mod.heartbeat_run` that parks A's ticks on an
  `asyncio.Event` (B's ticks still call the real function). This makes the
  docstring's "A's heartbeat stalls" actually true.
* `resume_heartbeat.set()` after B holds the claim, so A wakes onto B's claim,
  gets `matched=0`, and fences A's run through the unchanged production path.

No assertion was changed, loosened or removed. No production code was touched.
Diff is 15 lines.

## Files changed

* EDITED `section4/tests/unit/test_lifecycle_recovery.py` (one test function)
* No other file. `git status --short` shows only the A1 deliverables plus this file.

## Commands run (verbatim results)

Environment discovery — `git branch --show-current`, `git rev-parse --short HEAD`,
`git status --short`: `w28-s1-delegate`, `f5b298a`, `?? docs/yc/`.

`section4/.venv/bin/python -m pytest section4/tests/unit/test_lifecycle_recovery.py -q --basetemp=/tmp/a2-bt-probe1`:

```
5 passed, 4 warnings in 2.29s
RC=0
```

(A unique `--basetemp` per run is required on this host: without it the WorkBuddy
Python shim's brokered `Path.mkdir` raises `FileExistsError` on the already-existing
pytest-of-user root and all 5 tests error at setup. That is an env artifact, not a test failure.)

**Loop 1 — 40 isolated runs, before the change:**

```
cd "/Users/user/untitled folder 2/harness" && echo "cpu=$(sysctl -n hw.ncpu)" && pass=0; fail=0; for i in {1..40}; do bt="/tmp/a2-iso-$i"; rm -rf "$bt"; out=$(section4/.venv/bin/python -m pytest section4/tests/unit/test_lifecycle_recovery.py -q --basetemp="$bt" 2>&1); rc=$?; if [ $rc -eq 0 ]; then pass=$((pass+1)); else fail=$((fail+1)); printf '%s\n' "$out" > "/tmp/a2-iso-fail-$i.log"; echo "FAIL i=$i rc=$rc :: $(printf '%s' "$out" | tail -1)"; fi; done; echo "ISOLATED_RUNS=40 PASS=$pass FAIL=$fail"
```

```
cpu=12
ISOLATED_RUNS=40 PASS=40 FAIL=0
```

**One loaded timing run** (12 `yes` burners): `5 passed, 4 warnings in 4.51s`,
`real 8.13`, `remaining_yes=0`.

**Loop 2 — loaded runs, before the change** (12 `yes > /dev/null &` burners, PIDs
captured, `trap cleanup EXIT`, break on first failure, same pytest command with
`--basetemp=/tmp/a2-load-$i`):

```
LOAD: 12 yes burners, pids=97098 97099 97100 97101 97102 97103 97104 97105 97106 97107 97108 97109
FIRST_FAILURE i=6 rc=1
  ...
E       AssertionError: assert [] == ['life-run']
E         Right contains one more item: 'life-run'
section4/tests/unit/test_lifecycle_recovery.py:180: AssertionError
1 failed, 4 passed, 4 warnings in 2.42s
LOADED_RUNS=6 PASS=5 FAIL=1
remaining_yes=0
```

Full failure kept at `/tmp/a2-load-fail-6.log`.

After the change, one isolated file run: `5 passed, 4 warnings in 2.41s`, `RC=0`.

**Loop 3 — 60 loaded runs, after the change** (same loop, no early break):

```
LOAD: 12 yes burners, pids=4428 4429 4430 4431 4432 4433 4434 4435 4436 4437 4438 4439
POST_FIX_LOADED_RUNS=60 PASS=60 FAIL=0
remaining_yes=0
```

`section4/.venv/bin/ruff check --no-cache section4/tests/unit/test_lifecycle_recovery.py`:

```
All checks passed!
RUFF_RC=0
```

`RUFF_NO_CACHE=true make lint` (`MAKE_LINT_RC=0`), tail:

```
All checks passed!
== ruff section11 ==
cd section11 && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
All checks passed!
```

`git diff -- section4/tests/unit/test_lifecycle_recovery.py`: adds the
`resume_heartbeat` Event + `stalled_heartbeat` monkeypatch (14 lines) and one
`resume_heartbeat.set()` line. `git diff --stat docs/briefs/evidence/w21/native-confinement-container.json`
is empty — no full run was made, so the tracked evidence file is untouched.

`/bin/ps -Ao comm= | grep -c '^yes$'` after every loaded loop: `0`.

## Proof the new tests fail without the change

The unmodified test **did** fail: loaded loop 2 above produced
`AssertionError: assert [] == ['life-run']` at line 180 on run 6, while the same
40 isolated runs all passed. The whole point of the patch is to close that
load-only window; with the patch the identical 60-run loaded loop is 60/60 green.
Additionally, by reading `run_queue.py:299-313`, removing the stall necessarily
lets A's `UPDATE ... heartbeat_at = now` land before
`release_timed_out_claims`, so `reap_zombies()` must return `[]`. No assertion was
weakened, so a failure cannot be hidden by the change.

## What is NOT verified

* I did not run the full `section4/tests` suite or `make test` (that would rewrite
  the tracked W21 evidence file, and A2's check does not ask for it). No other test
  file was run, so a regression elsewhere is not excluded here.
* I did not capture a per-call trace of `heartbeat_run`; the mechanism is inferred
  from the three code sites plus the reproduced failure.
* Only one failure mode was reproduced (line 180). I did not observe a failure at
  the line-187 `timeout=5` wait, so I cannot say whether the STATUS-guessed second
  mode also exists.
* Load was 12 `yes` CPU burners, not the full section4 suite in a second process;
  the prompt allows either.
* `make lint` needed `RUFF_NO_CACHE=true` on this host (below). I did not get a
  plain `make lint` to complete after the A2 edit.

## Unrelated findings (one line each)

* WorkBuddy's Python shim brokers `Path.mkdir` to the host and raises `FileExistsError` on an existing pytest temp root, so `pytest` here needs a unique `--basetemp` per invocation.
* Ruff's cache temp-file rename is blocked by this sandbox's file-op broker; `RUFF_NO_CACHE=true make lint` is the working form, while plain `make lint` fails with "Failed to rename temporary cache file".
* `STATUS.md:927` / `:963` attribute this flake to the fixed 5 s wait; the reproduced failure is at line 180, so that guess does not describe the failure I saw.

## Risks the reviewer should check first

* Confirm that parking A's heartbeat is the intended simulation of "A's heartbeat stalls"; the test now no longer exercises the (accidental) case where A refreshes a claim that B is about to reap. If reviewers want that case, it needs its own test, not this one.
* The monkeypatch targets the module-level `service_mod.heartbeat_run` name imported at `service.py:65`; a future refactor that imports it differently would silently disable the stall and reintroduce the flake.
* The loop used `--basetemp` and `RUFF_NO_CACHE=true`; rerun with the reviewer's own environment before treating these as exact reproductions of the check commands.
