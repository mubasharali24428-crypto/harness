# Master prompt for hy4, batch 6: regression tests for the Day 1 hardening, and two tests-first stubs

Copy everything below the line into hy4 as its first message, AFTER batch 5 has finished and its SUMMARY is written.
Written 2026-11-06. Batch 5 (D2 to E1) stays in the main checkout and is not touched here.

---

# ROLE AND RULES

Same role, hard limits, repo facts, working rules and the five "new rules" as
`docs/briefs/hy4-master-prompt-batch4-2026-11-06.md` and `docs/briefs/hy4-master-prompt-batch5-2026-11-06.md`: $0 spend,
no deploys, no docker, no git writes (no commit, push, reset, stash, branch, `worktree add`), no secrets, no real home paths,
no rulings that belong to a person; an `Assumptions:` line and a numbered plan (step -> verify) before code; smallest change;
every new test gets a real negative control (copy to a NEW path with `cp`, mutate and restore with your edit tool, prove with
`cmp`). A rejected permission blocks only that step: mark it `NEEDS-REVIEWER-RUN` and carry on. Findings stay in your
hand-backs only: never in a test docstring beyond a one-line xfail reason, `STATUS.md`, `PROJECT-STATE.md`, commit or PR text.

# YOUR WORKTREE (this time it exists)

WORKTREE: `/Users/user/untitled folder 2/harness-wb-hy4`. FIRST run `cd "/Users/user/untitled folder 2/harness-wb-hy4"`
and work ONLY there (the path has spaces: quote it). Expected start state, save `git status --short` and
`git log --oneline -3` as your first act:

* HEAD `7c1acf4`, detached.
* Exactly six modified files, which are **Codex's Day 1 fixes** (reviewer-owned): `section4/src/harness_orchestrator/confine.py`,
  `.../dsh_plugins/harness-shell.js`, `.../engine.py`, `.../loop_guard.py`, `.../service.py`, `section5/src/harness_sandbox/shell_gate.py`.
* Nothing else. Anything else you did not touch: stop and write `BLOCKED:`.

**Do not edit those six files** except for the negative controls (each restored with `cmp`). The reviewer owns the fixes; you
write the tests that pin them.

# HOW TO RUN TESTS HERE (verified by the reviewer)

The worktree has no `.venv`. Use the main checkout's interpreters and point `PYTHONPATH` at THIS worktree, otherwise the main
checkout's code is imported and every result is meaningless:

```
W="/Users/user/untitled folder 2/harness-wb-hy4"
V4="/Users/user/untitled folder 2/harness/section4/.venv/bin/python"
V5="/Users/user/untitled folder 2/harness/section5/.venv/bin/python"
# section4:
cd "$W/section4" && PYTHONPATH="$W/section4/src:$W/section4/tests:$W/contracts/src:$W/section1/src:$W/section5/src" "$V4" -m pytest <files> -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"
# section5:
cd "$W/section5" && PYTHONPATH="$W/section5/src:$W/section5/tests:$W/contracts/src" "$V5" -m pytest <files> -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"
```

Prove which code ran, once per hand-back, with the same `PYTHONPATH`:
`"$V4" -c "import harness_orchestrator.confine as m; print(m.__file__)"` must print a path under `harness-wb-hy4`.
Do NOT use `-o pythonpath=...` (pytest splits it on the spaces in the path and silently falls back to the main checkout).

# TASKS, IN THIS ORDER

## F1. Symlinked scope roots (tests only)

Two functions were changed to skip a scope path whose lexical path differs from its real path (a symlink): `scope_roots` in
`section5/src/harness_sandbox/shell_gate.py` and `scope_targets` in `section4/src/harness_orchestrator/confine.py`. New files:
`section5/tests/unit/test_scope_roots_symlink.py` and `section4/tests/unit/test_scope_targets_symlink.py`. Build real symlinks
in `tmp_path` with `os.symlink` (no docker, no confinement backend needed). Workspace: `src/`, `docs/`, `src/real/`,
`src/link -> ../docs`, `src/uplink -> ..`. Expected values come from the meaning ("a symlinked scope is never granted;
ordinary scopes are unchanged"), not from today's output. Rows, for both functions:

| scope paths | expected |
|---|---|
| `["src"]` | `src` |
| `["src/real"]` | `src/real` |
| `["src/link"]` | nothing granted (empty list) |
| `["src/uplink"]` | nothing granted (empty list, NOT `None`, which means the whole workspace) |
| `["src/real", "src/link"]` | only `src/real` |
| `["src/nope"]` | nothing (missing path) |
| `["."]` | `None` (whole workspace) |
| a path with a trailing slash, `["src/"]` | `src` |
| an intermediate symlink: `lnk -> src`, scope `["lnk/real"]` | nothing granted |
| a symlink to a path INSIDE the workspace, scope `["src/inlink"]` where `inlink -> real` | nothing granted (decision: fail closed; note it in the hand-back) |

For `scope_targets` also: a target at or under a `forbidden` path is still dropped. Reviewer measurement on a workspace like this one,
baseline code -> fixed code (same for both functions): `src` -> `src`; `src/real` -> `src/real`; `src/link` -> `docs` becomes
nothing; `src/uplink` -> the whole workspace (`None`) becomes nothing (`[]`); `[src/real, src/link]` -> `docs` + `src/real`
becomes `src/real`; `src/nope` -> nothing.
**Negative control** per function: save the file to a `.mine` path, delete only the `if lexical != real: continue` lines, run
the new tests (the symlink rows must fail), restore with the edit tool, `cmp`. Optional, `NEEDS-REVIEWER-RUN` if the
backend is missing: one real-boundary row with `shell_gate.run` under the existing `requires_confinement` skip, scope
`["src/link"]`, a Python one-liner writing `docs/x.txt`: the file must not exist.

## F2. Denial marker only counts on a failed result

`LoopGuard._observe_result` in `loop_guard.py` now counts a denial only when `record.get("ok") is False` and the marker
`Denied by the harness command policy` is in the error or output. New file `section4/tests/unit/test_loop_guard_marker_ok.py`
(look at `test_loop_guard.py` for how records and the guard are built). Rows: `ok=False` + marker, three in a row ->
`stop_reason == "denials_repeated"`; `ok=True` + marker in `output` three times -> no stop; `ok=True` + marker in `error` ->
no stop; marker absent, `ok=False` -> no stop; a record with no `ok` key at all (characterise current behaviour, comment
"current behaviour, not a decision"; real engine records always set `ok`, see `engine.py` near the `"ok": not failed` line).
**Negative control:** restore the old expression (`DENIAL_MARKER in f"..."` without the `ok` test) in a `.mine` cycle; the
`ok=True` rows must fail.

## F3. Configured thresholds reach the shell breaker

`denialBreaker(consecutive, windowHits, windowSize)` in `dsh_plugins/harness-shell.js` now treats `null` as "that stop is
off", and `apply()` passes `config.denialRunLimit` and `config.denialWindowLimit`; `engine.py` writes them into the policy
patch (defaults 3 and 10), `service.py` fills them from `denial_run_limit()` / `denial_window_limit()`. Read
`test_shell_breaker.py` first and reuse however it runs the JS. New file `section4/tests/unit/test_denial_thresholds.py`:

* `denialBreaker(1, null)` trips after one denial; `denialBreaker(null, null)` never trips; `denialBreaker(5, null)` trips on
  the 5th denial, not the 4th; `denialBreaker(undefined, undefined)` keeps 3 and 10 (the defaults).
* `_execution_policy_patch` (engine.py): with a `shell_policy` lacking the keys the patch carries 3 and 10; with
  `denialRunLimit: None` it carries a disabled value; find how the `shell` dict is serialised into
  `harness-execution-policy.yml` and prove with a test that a disabled (`None`) value reaches the plugin as `null`, not as the
  string `None` and not dropped. **If it does not, that is a finding: write it in the private hand-back and mark that row
  `xfail(strict=True)`.**
* The service wiring: with `HARNESS_DENIAL_RUN_LIMIT=10` and `HARNESS_DENIAL_WINDOW_LIMIT=off` in the environment, the policy
  the service hands to `set_shell_policy` carries `10` and a disabled window (see `test_loop_guard_wiring.py` for a harness
  that observes it; if none can, say so and mark `NEEDS-REVIEWER-RUN`).
* **The one existing-test edit you may make:** `test_engine_shell_policy.py::test_policy_reaches_the_engine_and_its_calls_count_toward_the_cap`
  asserts the old policy dict; add `denialRunLimit` 3 and `denialWindowLimit` 10 to its expectation. Show the diff in the
  hand-back; touch nothing else in that file.

**Negative control:** change `denialBreaker(config.denialRunLimit, config.denialWindowLimit)` back to `denialBreaker()` in a
`.mine` cycle; the threshold rows must fail. Restore, `cmp`.

## F4. Two tests-first stubs for fixes Codex has not made yet (strict xfail, no production change)

In `section4/tests/unit/test_delegation.py` and `test_agent_tree.py` add (do not edit existing rows):

1. A root delegation (no parent assignment) whose scope is outside the SOURCE RUN's own intent. Run intent `scopePaths ["src"]`,
   approved agent ceiling `["."]`, delegate `scope.paths ["docs"]`, `parentAssignmentId` null. Meaning: it must be refused
   (HTTP 403, a scope-exceeds code). Today it is accepted (201). Read how `test_delegation.py` builds the run, the agent
   definition and the intent first (look for `scopePaths`, `intent_scope_paths`, `run_intent_versions`). Mark
   `xfail(strict=True)` with a one-line reason naming the input and the wrong output (e.g. "delegate docs under run intent [src]
   returns 201").
2. `_overlaps(["src/./file.py"], ["src/file.py"])`. Meaning: non-empty (same file). First check whether the public `_norm_scope`
   already normalises an internal `.` before storing (if it does, the case is unreachable through the API: say so in the
   hand-back and write the row against `_overlaps` as a unit characterisation with that comment, not xfail).
   Otherwise `xfail(strict=True)`.

Do not fix either one.

## F5. Summary and stop

`docs/briefs/hy4-b6-handback-SUMMARY.md` in your worktree: task table (F1 to F4) with status and the one thing you are least
sure of; every negative-control result with its `cmp`; a "Security findings" section (command or input, wrong output, layer that
should have stopped it) or "none"; every `NEEDS-REVIEWER-RUN` item; the diff of your one allowed existing-test edit; files
changed outside the tasks (expected: none); the `/private/tmp/hy4*` paths you created. End with the decisions now waiting
for the reviewer. Do not start new work. Hand-backs for each task: `docs/briefs/hy4-b6-handback-<id>.md`.

# WHAT THE REVIEWER KEEPS

Committing (the reviewer will make a branch in this worktree), whether symlinks INSIDE the workspace should stay refused, the
fixes behind F4's two xfails (Codex), merging, deploying, spend, and every ruling listed in the batch 4 prompt.

# STOP CONDITIONS

Stop and write `BLOCKED:` if the tree differs from the expected start state, or a step needs a paid call, a deploy, a secret, a
real private file, or a person's decision. A rejected permission blocks only that step.
