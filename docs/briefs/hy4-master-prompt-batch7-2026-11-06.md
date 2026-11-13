# Master prompt for hy4, batch 7: four more tests-first tasks (no production change)

Copy everything below the line into hy4 as its first message. Written 2026-11-06 after the reviewer verified batch 6.
Batch 5 (D2 to E1, in the main checkout) is a separate job; do not touch it from here.

---

# ROLE AND RULES

Same role, hard limits, repo facts, working rules, the five "new rules", the test-run recipe and the provenance proof as
`docs/briefs/hy4-master-prompt-batch6-2026-11-06.md` (read it in full first). In short: $0 spend, no deploys, no docker, no git
writes, no secrets, no real home paths, no rulings that belong to a person; `Assumptions:` line and a numbered plan first;
smallest change; every new test gets a negative control or a strict xfail; findings stay in the hand-backs only. A rejected
permission blocks only that step (`NEEDS-REVIEWER-RUN`).

**No production file may change in this batch**, except inside a named negative control (copy to a `.mine` path, mutate with your
edit tool, restore with your edit tool, prove with `cmp`).

# WORKTREE AND START STATE

WORKTREE: `/Users/user/untitled folder 2/harness-wb-hy4`. FIRST `cd` there and save `git status --short` and
`git log --oneline -3`. Expected, and nothing else:

* HEAD `7c1acf4`, detached.
* Modified (9): the six reviewer-owned Day 1 fixes (`confine.py`, `harness-shell.js`, `engine.py`, `loop_guard.py`, `service.py`,
  `section5/.../shell_gate.py`) plus your batch 6 edits to `test_agent_tree.py`, `test_delegation.py`, `test_engine_shell_policy.py`.
* Untracked (9): your batch 6 hand-backs F1 to F4 and SUMMARY, and your four new batch 6 test files.

# WHAT THE REVIEWER FOUND IN BATCH 6 (so you do not redo it)

Reviewer re-ran everything on a scratch copy so your worktree was untouched: your consolidated section4 run reproduces exactly
(`293 passed, 2 xfailed`, with `confine.py` and `loop_guard.py` proven to load from your worktree); section5 related files
`56 passed, 4 xfailed`; the four negative controls (F1 section4, F1 section5, F2, F3) each make the intended tests fail and the
originals restore clean; both F4 stubs are `strict=True` and fail under `--runxfail` with exactly `201 == 403` and `[] != []`.
On the reviewer's host the optional real-boundary row for F1 ran instead of skipping, passed with the fix, and failed without it.
Two notes: your F4 root-delegation stub asserts `"SCOPE_EXCEEDS" in code` (a prefix) because no refusal code exists yet; that is
fine until Codex lands the fix. And your F3 null-serialisation test goes through the real plugin, which closes the reviewer's
earlier open question.

# TASKS, IN THIS ORDER

## J1. Do symlinks in command ARGUMENTS get past the scope check? (characterise, tests only)

`shell_gate.scope_violation` / `_scope_hit` judge a command's path tokens. Batch 6 pinned the scope ROOTS; this asks about the
argument scan. New file `section5/tests/unit/test_scope_hit_symlink.py`, unit level (call `scope_violation` / `_scope_hit`
directly the way `test_shell_gate_scope_edges.py` does; no confinement backend needed). Workspace in `tmp_path`: `src/`, `docs/y.txt`,
`src/link -> ../docs`, `src/real/x.txt`, scope `["src"]`. Rows, expected by meaning ("a path whose REAL location is outside the
scope is denied, exactly as `cat docs/y.txt` is"): `cat src/real/x.txt` allowed; `cat docs/y.txt` denied; `cat src/link/y.txt`
denied; `ls src/link` denied; `cat src/link` denied; `grep -r z src/link` denied; a symlink pointing at a missing target; a
symlink inside `src` pointing at another path inside `src` (allowed, characterise). Where today's answer differs from the meaning:
`xfail(strict=True)`, one-line reason, and a "Security findings" entry in the hand-back (command, wrong output, layer that should
have stopped it). Do not fix. Negative control: the existing rows (`cat docs/y.txt` denied) must fail if you stub `_scope_hit` to
return `None` in a `.mine` cycle.

## J2. Is a delegated child's scope bound to its engine? (end-to-end, tests only)

The 2026-11-05 `STATUS.md` entry says "the assignment scope is recorded but not bound to the child's engine"; commit `0c08b2f`
says it binds the delegated scope to the child run intent. Settle it with a test. In `section4/tests/unit/test_delegation.py`
(append only; reuse the fixtures and the fake engine that `test_loop_guard_wiring.py` / `test_delegation.py` use): delegate a child
with scope `["docs"]` under an agent whose ceiling is `["."]`, take it to the point where the child run's engine starts, and assert
the engine receives `scopePaths == ["docs"]` (look for how `LoopingEngine.policies` or `set_shell_policy` expose it). If the
child cannot be started in the test harness, assert the weaker, honest thing (`collab.intent_scope_paths` for the child run) and
say which in the hand-back. Passing is the expected result: pin it with a negative control (in a `.mine` cycle remove only the
`INSERT INTO run_intent_versions` block in `_open_child_run`, `delegation.py` about line 104; the new test must fail; restore, `cmp`).
If it does NOT pass today, mark it `xfail(strict=True)` and write a finding.

## J3. A repeated stop of an already-stopped run (tests only)

The 2026-11-05 `STATUS.md` records "a repeated stop of an already-stopped run re-emits a `paused` event (child B shows two)".
Append to `test_delegation.py`, next to the existing stop test (about line 317, `stops = [...]`): stop the same child twice and
assert exactly one `paused` event for it. Today that should fail: `xfail(strict=True)` with a one-line reason. Do not fix. State in
the hand-back how many `paused` events you actually observed (`--runxfail`).

## J4. Does the PITR drill's cleanup only ever touch what it created? (static test, no docker)

`scripts/pitr_drill.sh` ends with `docker volume rm "$PITR_VOLUME"` and `"${COMPOSE[@]}" down -v` on its own `-p` project (around
lines 18, 44, 52 to 58 and 94 to 95). Its comment says compose pins some volume names, so `down -v` in ANY project would remove a
live volume of that name. New file `scripts/ops/test_pitr_drill_volumes.py` (plain Python; PyYAML if the section4 venv has it,
otherwise a small line parser, say which):

* Parse `deploy/docker-compose.yml`: every top-level volume that has a pinned `name:`; and which services mount each one.
* Parse `scripts/pitr_drill.sh`: which services the drill starts, and which "refusing: ... already exists" pre-flight checks it has.
* Expectation: every pinned-name volume that a service started by the drill mounts is covered by a pre-flight refusal in the
  script (so `down -v` cannot remove a live volume of that name). Where it is not covered: `xfail(strict=True)`, one-line reason
  naming the volume. If everything is covered, the test passes and gets a negative control (a `.mine` copy of the script with one
  pre-flight check deleted must make it fail).
* No test for the other `down -v` sites (`scripts/loadtest/run_ladder.sh`, `run_probe.sh`, `deploy/runners/rollback_rehearsal.sh`):
  instead a table in the hand-back: file, line, the `-p` project argument, and whether the project name is derived from a scratch
  stamp or is the live project. Facts only.

## J5. Summary and stop

`docs/briefs/hy4-b7-handback-SUMMARY.md` in your worktree: task table (J1 to J4) with status and the one thing you are least sure
of; each negative-control result with its `cmp`; "Security findings" (J1 and J2 if any) or "none"; the J4 table; every
`NEEDS-REVIEWER-RUN`; files changed outside the tasks (expected: none); the `/private/tmp/hy4*` paths you created. End with the
decisions now waiting for the reviewer. Do not start new work. Hand-backs: `docs/briefs/hy4-b7-handback-<id>.md`.

# WHAT THE REVIEWER KEEPS

Committing, merging, deploying, spend, the fixes behind every xfail (Codex or the reviewer), flipping xfails to plain tests after a fix
lands, the refusal code for a root delegation outside the source run's intent, and every ruling in the batch 4 prompt.

# STOP CONDITIONS

Stop and write `BLOCKED:` if the tree differs from the expected start state, or a step needs a paid call, a deploy, a secret, a real
private file, or a person's decision.
