# hy4 hand-back — A4

Shorter alternative: **no**. A4's diff is ~198 lines, but 122 of those are the
edge/knob-semantics tests the task explicitly asks for (four edge behaviours plus
unset/garbage/off/0 semantics for two knobs). The production change is ~50 lines:
two pure knob readers, two constructor defaults, one condition, and the service
call site. Dropping the tests would leave "defaults unchanged" asserted only by
tests I was told not to touch, which is the opposite of the acceptance criterion.

Task: A4 — Make the denial thresholds configurable, and test the edges
Status: DONE
Assumptions: `loop_guard.py`'s `loop_repeat_limit()` is the knob-semantics pattern
  to copy; `service.py` constructs `LoopGuard` in exactly one place
  (`service.py:1640`); the env-knob gate derives its catalogue from literal
  `os.environ.get("KEY")` reads plus `.env.example`. All verified by reading HEAD.

## Files changed

EDITED:
* `section4/src/harness_orchestrator/loop_guard.py` — added `_denial_limit`,
  `denial_run_limit()`, `denial_window_limit()`; `LoopGuard.__init__` gained
  `denial_run_limit=DENIAL_RUN_LIMIT` and
  `denial_window_limit=DENIAL_WINDOW_LIMIT` (both `int | None`, `None` disables);
  `_observe_result` now consults the instance fields with short-circuit guards.
  `DENIAL_WINDOW` (50) is unchanged and not tunable; the JS plugin is untouched.
* `section4/src/harness_orchestrator/service.py` — import + the one `LoopGuard`
  call site now passes `denial_run_limit=denial_run_limit()` and
  `denial_window_limit=denial_window_limit()`.
* `.env.example` — both knobs documented in the "Orchestrator tuning" section
  with their defaults (3 / 10), the unset/garbage→default and empty/off/none/0→
  disabled semantics, and the fact that the 50-result window is not tunable.
* `section4/tests/unit/test_loop_guard.py` — appended only; all pre-existing
  tests are byte-for-byte unchanged.
* `section4/tests/unit/test_loop_guard_wiring.py` — appended two service-seam
  tests.

Knob semantics copied from `loop_repeat_limit()` (`loop_guard.py:85-101`):
unset → default; empty / `off` / `none` / `0` / negative → disabled; non-integer
→ default; positive integer → that value. The env keys are **string literals**
inside `os.environ.get(...)` on purpose: `scripts/check_env_knobs.py:32-35`
only catalogues literal reads, so the gate now genuinely enforces their docs.

## Commands run (verbatim results)

```
$ cd "/Users/user/untitled folder 2/harness" && section4/.venv/bin/python -m pytest section4/tests/unit/test_loop_guard.py section4/tests/unit/test_loop_guard_wiring.py -q
A4_TESTS_RC=1
42 passed, 9 errors in 2.92s
E  FileExistsError: [Errno 17] EEXIST: mkdir '/private/var/folders/.../T/pytest-of-unknown'
```
All 9 errors are `tmp_path` fixture setup in `test_loop_guard_wiring.py`, caused
by the WorkBuddy Python shim brokering `mkdir` onto an already-existing pytest
root — the same environment artifact recorded in A2 / the progress note. No test
assertion failed.

```
$ rm -rf /tmp/a4-bt && section4/.venv/bin/python -m pytest section4/tests/unit/test_loop_guard.py section4/tests/unit/test_loop_guard_wiring.py -q --basetemp=/tmp/a4-bt
A4_TESTS_BASETEMP_RC=0
51 passed, 9 warnings in 3.23s
```
(9 `aiosqlite` DeprecationWarnings, pre-existing.)

```
$ python3 scripts/check_env_knobs.py
KNOB_RC=0
env knob catalogue complete: 62 runtime reads, all in .env.example
```
Baseline before the change was `60 runtime reads`; the gate now sees both new
knobs and finds both documented.

```
$ make static-gates
STATIC_GATES_RC=0
... orchestrator specs and UI calls match the router.
{"ok": true, "tasks": 39, "heldOut": 6, "implemented": 39}
env knob catalogue complete: 62 runtime reads, all in .env.example
```

```
$ RUFF_NO_CACHE=true make lint
MAKE_LINT_RC=2
I001 [*] Import block is un-sorted or un-formatted --> tests/unit/test_loop_guard.py:212
```
Fixed the one isort ordering issue in a new test, then:
```
$ RUFF_NO_CACHE=true make lint
MAKE_LINT_RC=0   (all 10 sections "All checks passed!")

$ section4/.venv/bin/ruff check --no-cache <4 changed py files>
All checks passed!   RUFF_RC=0
```
`RUFF_NO_CACHE=true` is needed on this host because ruff's cache temp-file
rename is blocked by the sandbox file-op broker (see the progress note).

Negative control (real): temporarily replaced the instance-field condition in
`_observe_result` with the original constant comparison
(`if run >= DENIAL_RUN_LIMIT or sum(...) >= DENIAL_WINDOW_LIMIT:`), then:
```
$ section4/.venv/bin/python -m pytest <both files> -q --basetemp=/tmp/a4-negctl-bt
NEGATIVE_CONTROL_RC=1
FAILED ...::test_window_evicts_at_exactly_fifty_results
FAILED ...::test_interleaved_calls_and_results_do_not_miscount_denials
FAILED ...::test_both_limits_disabled_means_never_stops_on_denials
FAILED ...::test_a_limit_of_one_stops_on_the_first_denial
FAILED ...::test_disabled_denial_knobs_reach_the_guard_the_service_builds
5 failed, 46 passed, 9 warnings in 3.30s
```
Restored, then:
```
RESTORED_TESTS_RC=0   51 passed, 9 warnings in 3.36s
```

## Proof the new tests fail without the change

The negative control above is a real run: 5 of the new tests fail when the
guard ignores the configured limits, including the service-seam test that proves
the env knob actually reaches `LoopGuard`. Restoring the condition makes all 51
pass. Additionally, by reading HEAD: `denial_run_limit()` /
`denial_window_limit()` did not exist, so the knob-semantics tests could not
import them; `LoopGuard.__init__` accepted only two arguments, so
`_denial_guard(denial_run_limit=None, ...)` would raise `TypeError`.

## What is NOT verified

* The exact check command without `--basetemp` returns rc=1 on this host (9
  shim-induced fixture errors). A reviewer on a normal host should see rc=0 from
  that literal command; the equivalent `--basetemp` run is rc=0 with 51 passed.
* No deploy/restart was performed (out of scope), so the knobs were not exercised
  against the running stack — only against the service test harness.
* No full section4 suite was run; the W21 evidence file is therefore unchanged
  and the permitted `git checkout` restore was not needed.

## Unrelated findings (one line each)

* `HARNESS_LOOP_REPEAT_LIMIT` is read via a module constant (`REPEAT_LIMIT_ENV`), so `scripts/check_env_knobs.py` cannot see it and it is absent from `.env.example`; left alone (out of scope).
* The WorkBuddy Python shim's brokered `mkdir` breaks pytest `tmp_path` when the default temp root already exists; use a unique `--basetemp`.
* `make lint` needs `RUFF_NO_CACHE=true` here; plain `make lint` fails on a ruff cache rename, not a lint finding.

## Risks the reviewer should check first

* `None` means "this one stop is disabled", which is a real operator footgun: setting `HARNESS_DENIAL_RUN_LIMIT=0` turns off the 3-in-a-row stop (as the repeat-limit pattern dictates), it does not mean "stop immediately". The `.env.example` comment says so, but a person may still read 0 as a threshold.
* Disabling both knobs removes `denials_repeated` entirely while the shell plugin's own breaker remains — intended by the task, but worth a second look.
* Negative integers are treated as "disabled" (mirroring `loop_repeat_limit`), not as garbage→default; this is a judgment call at the edge of the stated semantics.
* The two service-seam tests use `monkeypatch.setenv`; confirm they do not leak env state in the reviewer's full-suite ordering.
