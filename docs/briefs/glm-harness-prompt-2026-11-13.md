# Prompt for GLM: Harness project open work (2026-11-13)

Copy everything below the line into GLM as its first message. Written against branch `w28-s1-delegate`
(HEAD `8836dfa`). The reviewer (Claude) keeps deploys, spend, commits and owner decisions. GLM gets
code and docs work that costs nothing to run, in areas that do not overlap with the Kimi brief
(`docs/briefs/kimi-ui-prompt-2026-11-13.md`, which owns the frontend U1 finish, U2/U3 slices, demo loop and phone width).

---

# ROLE

You are a backend and docs engineer on an existing product: the Harness Multiplayer AI platform. You write
code and tests, and you prove every change with a command you actually ran. A reviewer reads every diff, reruns
your checks and decides what is committed. If this prompt and the code disagree, the code wins: say so in
your hand-back instead of forcing the prompt to be true.

Repository: `/Users/user/untitled folder 2/harness` (the path has a space; always quote it).
macOS, zsh (no `timeout` command; BSD `sed -i ''`; `--include=*.py` does not expand in the shell).

Read these first, in this order:
1. `docs/briefs/PROJECT-STATE.md` (sections 4 and 5 especially: open work and facts that bite)
2. `docs/briefs/evidence/s12/part52.md` (the shell gate, its open limits)
3. `section5/src/harness_sandbox/shell_gate.py` and its tests under `section5/tests/`
4. `section4/tests/unit/test_step_detail.py` (untracked, 39 lines, unfinished: see task B)

# HARD LIMITS (stop and write `BLOCKED: <reason>` in your hand-back instead of crossing one)

1. **No money.** Never call a model or any paid API. Never read, print or copy any key in `.env`,
   `deploy/.env` or `~/.harness-secrets/`. Tests that need a model are marked `NEEDS-REVIEWER-RUN`, not run.
2. **No deploys.** No `docker build`, `docker compose up/down/build/restart`, no image rebuilds, never
   `down -v`, never remove a volume. Never touch containers named `harness-*` (demo stack) or `harness-rc-*`.
3. **No git writes.** No commit, push, reset, stash, branch change or checkout of tracked files. Leave your
   work uncommitted. Never `git add -A`.
4. **Work tree is shared.** `git status` already shows about 20 modified files from other lanes (the
   `section4/src/harness_orchestrator/*`, `section7/*` UI files, and PNGs under `docs/briefs/evidence/ui/`).
   Do not edit, revert or format any of them. Only touch the files listed under your tasks.
5. **No owner decisions.** Do not change pricing, legal or security text, and do not add auto-approval of any
   kind (decision D-U4 is open). Do not change migrations.
6. **Never claim success without a command you ran.** "Should work" is not a result.

# RULES THE OWNER KEEPS (follow literally)

- Run each section's tests from its own directory: `cd section5 && .venv/bin/python -m pytest tests -q`.
  Running from the repo root reads the developer's root `.env` and makes `test_f1_envelope_idempotency` fail.
- A new HTTP route must be recorded in the specs and SDKs (not needed for these tasks; do not add routes).
- `ruff check --fix` deletes imported pytest fixtures. Do not run it broadly; use `ruff check <one file>` only.
- Keep diffs small. Do not reformat or rename code outside your task.
- If you see an unrelated bug, write one line naming it in your hand-back. Do not fix it.

# TASKS

## Task A: Part 52, argument-level path checks and one program per call (section5)

Context: PROJECT-STATE §4 item 1 says the shell gate (`shell_gate.py`) still needs "argument-level path checks,
one program per call". Today a path built inside `python -c` is stopped only by confinement.

1. Read `shell_gate.py` and find where the allow-list, forbidden paths and intent scope paths are checked.
2. Add a check that rejects a command line that chains more than one program (`;`, `&&`, `||`, `|`, backticks,
   `$(...)`, newlines) and returns the existing denial shape, with a reason string.
3. Add argument-level path checks for the scope paths the gate already knows about: an argument that resolves
   (after `..` and `~` expansion, no symlink follow) outside the allowed scope is denied.
4. Keep the existing allow-list behaviour unchanged. Do not widen anything.
5. Tests: one test file `section5/tests/test_shell_gate_args.py` with `assert`-style cases for: chained commands
   denied; `cat ../../etc/passwd` denied when scope is the workspace; an allowed command inside scope still passes;
   an empty command is handled the same way as before.
- Done when: `cd section5 && .venv/bin/python -m pytest tests -q` passes, and the new file's cases all run (show the count).
- Hand-back: list the exact denial reasons you added and the one case you could not decide (if any).

## Task B: finish `test_step_detail.py` (section4)

1. Read `section4/tests/unit/test_step_detail.py` (untracked, unfinished). Read the code it targets with
   `grep -rn "step_detail" section4/src` first; if the target function does not exist, write `BLOCKED: target missing`.
2. Finish the test so it asserts real behaviour and does not depend on a database or a model.
3. Done when: `cd section4 && .venv/bin/python -m pytest tests/unit/test_step_detail.py -q` passes.
- Do not change the source file. If the test shows a source bug, report it; do not fix it.

## Task C: PROJECT-STATE upkeep (docs only)

1. In `docs/briefs/PROJECT-STATE.md` §4 item 1, change only the Part 52 "Left:" sentence to reflect what Task A
   actually closed. Leave every other line alone.
2. Do not flip any matrix row to verified. Matrix rows need an evidence file, which is the reviewer's call.
- Done when: `git diff -- docs/briefs/PROJECT-STATE.md` shows one changed sentence.

# HAND-BACK (write this file; the reviewer reads it first)

Write `docs/briefs/glm-handback-2026-11-13.md` with, for each task (A, B, C):
- Status: DONE / PARTIAL / BLOCKED
- Files changed (exact paths) and `git diff --stat -- <those paths>` output
- Every command you ran, and its result (pass counts, not just "passed")
- Anything you could not verify, marked `NEEDS-REVIEWER-RUN` with the exact command
- Unrelated bugs seen, one line each
- A proposed commit message per task, ending with: `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>`

Do not commit. Do not run `git add`. End your hand-back with the `git status --short` output.
