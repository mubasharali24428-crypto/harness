# Master prompt for hy4, batch 5: finish D2 to D5 after the batch 4 stop, plus one D1 follow-up

Copy everything below the line into hy4 as its first message. Written 2026-11-06 against branch
`w28-s1-delegate`, HEAD `19f5cae`. Batch 4 stopped at D2 because the tree moved under it; that stop was correct
and is explained below. D1 is done and reviewer-verified. Do not redo D1.

---

# ROLE AND RULES

Same role, hard limits, repo facts, working rules and the five "new rules" (private files, findings stay
private, tests and docs only, no fixing what you report, real execution only in a throw-away workspace) as
`docs/briefs/hy4-master-prompt-batch4-2026-11-06.md`, which itself builds on batch 2, 2b and 3. Read that file in
full first, including its sections D2 and D3, which are unchanged and still your tasks. In short: $0 spend, no
deploys, no `docker run`, no git writes, no secrets, no rulings that belong to a person; an `Assumptions:` line
and a numbered plan before code; smallest change; every new behaviour gets a test with a real negative control;
a rejected permission blocks only that step (mark it `NEEDS-REVIEWER-RUN`, carry on).

# WHAT HAPPENED IN BATCH 4, AND WHAT CHANGED SINCE

* **The tree changed legitimately while you worked.** The reviewer committed batches 1 to 3 (`57b466e` to `7cdd365`),
  merged `origin/master` (`277f007`), reconciled `STATUS.md` and `PROJECT-STATE.md` (`0f55edb`), removed finding
  wording from them (`19f5cae`) and pushed all of it as PR #24. You were right to stop: you cannot tell that from
  inside the tree. **From this batch on, those commits are the baseline, not drift.**
* **New stop rule for drift.** Save `git status --short` and `git log --oneline -5` as your first act. The expected
  start state is: HEAD `19f5cae`; modified `agent_tree.py`, `test_agent_tree.py`, `test_delegation.py` (your D1);
  untracked `docs/briefs/hy4-b4-handback-D1..D5.md`, `hy4-b4-handback-SUMMARY.md` and `docs/yc/`. If that matches,
  continue. If the reviewer has since committed D1, the three D1 files will be absent from status and the log will
  show a newer HEAD whose message starts with `D1`: also continue. Anything else you did not touch: stop and write
  `BLOCKED:` as before.
* **D1 verified by the reviewer:** 231 passed with the literal accept command on a normal host (your
  `FileExistsError` is your sandbox's shared pytest temp root; keep using `--basetemp`); ruff clean; your
  2,304-pair differential reproduced exactly (0 lost, 148 gained, none outside the dot-prefix group); the second
  negative control reproduced (22 failed with only the new `.` rule removed).
* **Negative-control trap (new):** `section4/pyproject.toml` sets pytest `pythonpath = ["src", ...]`, so running
  pytest with a mutated copy on `PYTHONPATH` silently tests the real code and "passes". If you ever run a control
  against a copy, override with `-o "pythonpath=<copy>/src ../contracts/src ../section1/src"` and print
  `module.__file__` to prove which code ran. In-place `.mine`/mutate/restore/`cmp` is still the preferred form.
* **STATUS.md and PROJECT-STATE.md now say** batches 1 to 4 are committed, W27 slices 1 to 3 are deployed, the live
  stack is on mixed tags (`vw27s3` orchestrator, `vw27s2` worker-daemon, `vw28s6` other backends, `vw24b2`
  frontend), and security items are "kept in the private hand-backs, not summarised here". Do not edit either file.
  Nothing from batches 1 to 4 is **deployed**; batches 1 to 4 are **committed and pushed** (PR #24, private repo).
  The batch 3 hand-backs, including C2, are in that PR; that is the reviewer's decision to revisit, not yours.

# TASKS, IN THIS ORDER

## D2. Real-boundary tests for the shell gate: unchanged

Exactly as in the batch 4 prompt, section D2 (fixture, groups W, R, G, accept command, negative control on
`write_roots`, out of scope). One addition: for Group R row R4 (`ls ..`) and any row where the answer depends on
the platform, record `sys.platform` and which confinement backend ran. Run the section5 tests with the section5
venv: `cd section5 && .venv/bin/python -m pytest ... --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"`.

## D3. Read-confinement decision note: unchanged

Exactly as in the batch 4 prompt, section D3. It is docs only, status Proposed, blank `Owner:`, every claim cited to
a file and line, and its content goes nowhere else.

## D4. Draft STATUS entries for batches 2 to 4 (draft only, nothing applied)

Write `docs/briefs/hy4-draft-status-batch2-4.md` in the shape of `docs/briefs/hy4-draft-status-batch1.md`. One entry
per task of batch 2 (A1 to B3), batch 3 (C1 to C3) and batch 4 (D1; D2 and D3 only if done). **Correct framing, replacing
the batch 4 prompt's:** "committed and pushed in PR #24; not deployed". Keep those two states apart in every entry,
and mark facts "reviewer re-ran" only where this prompt or an earlier prompt says so. Include a diff-style proposal for
`PROJECT-STATE.md` sections 2 and 4 against the file as it is **now**. For C2 and D2 write exactly one vague line
("shell-gate edge and real-boundary tests added; some cases recorded as strict expected failures; details in the
private hand-back") and nothing about what they are. Do not edit `STATUS.md` or `PROJECT-STATE.md`. Check with
`cd section4 && .venv/bin/python -m pytest tests/unit/test_program_docs.py -q --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"`.

## D5. Corrections to your batch 3 hand-backs (append only)

As in the batch 4 prompt, section D5, with one change: `hy4-b3-handback-C1.md` and `hy4-b3-handback-C2.md` are now
**tracked** (committed in `7cdd365`). Appending a "Superseded" section at the end is still allowed and is the only
edit allowed; leave everything above it byte-for-byte unchanged and prove it with
`git diff --stat -- <file>` showing insertions only and 0 deletions.

## E1. Four old-answer-only pairs from your D1 differential (test-only, characterise)

Your D1 step 4 found that the early-return branch in `_covered` still changes four answers that no test pins:
`./..` under `.`, `./`, `.//` and `.///`. By the gate's meaning a path with a `..` component should not be covered
by a `.`-style prefix, so a covered answer there may let a delegated child's scope step out of the workspace. In
`section4/tests/unit/test_agent_tree.py` add one table-driven test, four rows, expected value `False` (the meaning),
each `xfail(strict=True)` with a one-line reason naming the pair. Do not change `_covered`. Put the explanation
and the question "can a stored scope ever contain `..`?" (check the validators named in D1) only in
`hy4-b4-handback-E1.md`, not in a docstring. Also leave the reviewer the decision: delete the early-return branch
or not. Accept: the file passes with the xfails counted, ruff clean, and the strict xfails flip to failures if the
branch is deleted (prove with a `.mine`/mutate/restore control and `cmp`).

## D6. Summary and stop

`docs/briefs/hy4-b5-handback-SUMMARY.md`: task table (D2, D3, D4, D5, E1) with status and the one thing you are
least sure of; a "Security findings" section (every strict xfail from D2 and E1: command, wrong output, layer that
should have stopped it, or "none"); every `NEEDS-REVIEWER-RUN` item; files changed outside the tasks; the
`/private/tmp/hy4*` paths you left. End with the decisions now waiting for the reviewer. Do not start new work.
Hand-backs go to `docs/briefs/hy4-b5-handback-<id>.md`; the D1 and D2 style of batch 4 (Assumptions, files changed,
commands with exit codes, control with `cmp`, what is NOT verified, risks, scratch files) still applies.

# WHAT THE REVIEWER KEEPS

Everything listed under "WHAT THE REVIEWER KEEPS" in the batch 4 prompt, plus: committing D1 and the batch 4
hand-backs, whether to delete the early-return branch, whether to keep the batch 3 hand-backs in PR #24, and the
pre-existing items (Oct 17 Codex branches, redeploying one image tag set, `docs/yc/`).

# STOP CONDITIONS

A rejected permission blocks only that step: mark it `NEEDS-REVIEWER-RUN` and carry on. Stop and write `BLOCKED:` if
a step needs a paid call, a deploy, a secret, a real private file, or a person's decision, or if the tree has changes
you did not touch that the "expected start state" above does not explain.
