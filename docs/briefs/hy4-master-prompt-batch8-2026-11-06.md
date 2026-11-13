# Master prompt for hy4, batch 8: four bulk tasks, test and review only (no production change)

Copy everything below the line into hy4 as its first message. Written 2026-11-06 after reviewing hy4's batches 1 to 4 and the
day-1 calibration hand-back. Batch 7 (J1 to J5) is a separate prompt and is NOT part of this batch; do not run it from here.

Budget: each task is sized for about 30 to 45 minutes of real work. Do not stop early. If a task's listed checks are done and it
is still under 30 minutes, spend the rest on the verification steps the task lists (more rows, a second negative control, a
second run), never on new scope.

---

# ROLE AND RULES

You are hy4, a bounded worker. $0 spend. No deploys, no docker commands (not even `docker ps`), no git writes (no commit, push,
reset, stash, branch, checkout, `worktree add`). Read-only git is fine (`git log`, `git show`, `git archive`). No secrets, no `.env`
contents, no real home paths, no real private files. No rulings that belong to a person. Findings stay in your hand-back only:
never in a test docstring beyond a one-line xfail reason, `STATUS.md`, `PROJECT-STATE.md`, a commit or PR text.

Before code, each task starts with an `Assumptions:` line and a numbered plan in the form `1. step -> verify: check`.

Every new test needs a real negative control: copy the file under test to a `.mine` path with `cp`, mutate it with your edit tool,
run the test (it must fail), restore it with your edit tool, and prove the restore with `cmp`. A rejected permission blocks only
that step: mark it `NEEDS-REVIEWER-RUN` and carry on with the rest of the task.

# START STATE: PIN THE SNAPSHOT (do this first, before anything else)

The main checkout moves under you (batch 4 was blocked by exactly that). So do not work in it. Build a read-only snapshot of the
pinned commit and work only there:

```
REPO="/Users/user/untitled folder 2/harness"
SNAP="/private/tmp/hy4-b8/snap"
mkdir -p "$SNAP" && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"
git -C "$REPO" log --oneline -1 7c1acf4      # save this line in your hand-back
```

Expected: commit `7c1acf4` exists. If `git -C "$REPO" cat-file -t 7c1acf4` does not print `commit`, write `BLOCKED: pinned commit
missing` and stop. Do not pick a different commit.

Test interpreters: use the main checkout's venvs, with `PYTHONPATH` pointing at the SNAPSHOT so the snapshot's code is what gets
imported, not the main checkout's:

```
PY4="$REPO/section4/.venv/bin/python"; PY5="$REPO/section5/.venv/bin/python"
PP4="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src"
PP5="$SNAP/contracts/src:$SNAP/section5/src"
```

Run each suite from its own directory in the snapshot, and use a private basetemp (batch 4 hit a shared-temp collision otherwise):

```
cd "$SNAP/section4" && PYTHONPATH="$PP4" "$PY4" -m pytest tests/unit/<file>.py -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section5" && PYTHONPATH="$PP5" "$PY5" -m pytest tests/unit/<file>.py -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
```

Check which interpreter imported the code before trusting any result: run `PYTHONPATH=... "$PY4" -c "import harness_orchestrator, sys; print(harness_orchestrator.__file__)"`
and confirm the path starts with `$SNAP`. If it does not, write `BLOCKED: import path` and stop.

# WHY THIS BATCH (from the review of your earlier work)

What your past batches did well, keep doing: negative controls with `cmp` proof; honest "I am least sure of" lines; stopping on
drift instead of working around it; reporting NEEDS-REVIEWER-RUN rather than claiming a result.
What went wrong and must not repeat: batch 4 stopped at task D2 because the main checkout changed mid-run (eight commits, 69 files);
the calibration task found its WORKTREE placeholder unsubstituted and had to improvise. This batch fixes both by pinning the
snapshot and by giving each task a concrete path.

# TASKS, IN THIS ORDER

## K1. Test and skip ledger for the three suites (about 40 min, read and classify, no code change)

Purpose: the RC plan's Stage 0 needs "a test/skip ledger; separate environmental skips from defects". Produce it.

1. Run each suite in the snapshot with skip reasons shown:
   `cd "$SNAP/section1" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src" "$REPO/section1/.venv/bin/python" -m pytest tests -q -rs -p no:cacheprovider --basetemp=...`
   and the same for section4 and section5 (use each section's own PYTHONPATH from the START STATE block).
   Expected approximate totals from the reviewer's run on the main checkout: section1 1240 passed / 63 skipped; section4 2308 passed /
   59 skipped; section5 507 passed / 8 skipped / 14 xfailed. Your numbers may differ at the pinned commit; report yours, and say if they differ.
   -> verify: each count line in your ledger matches the pytest summary line you saved.
2. Classify every skipped test (group by skip reason text, not by test name) into one of:
   `ENV` (needs Docker, Postgres, Linux kernel feature, a real network, a model key), `FLAG` (skipped unless an env var is set),
   `DEFECT` (skipped for a reason that is not environmental, for example a stale marker or an unimplemented feature),
   `UNKNOWN` (you cannot tell from the reason text; say what you would need to read).
   For each group give: the reason string, the count, one example test id, the file and line of the `skip`/`skipif` marker
   (`file:line`), and the class with one sentence of why.
3. Same for xfails in section5 (14) and section4 if any: test id, the one-line reason, and whether the reason names a fix.
4. Write `docs/briefs/evidence/rc/skip-ledger-2026-11-06.md` in the MAIN checkout (this is an evidence file, not code; create
   the directory if missing). Do not write anywhere else in the main checkout.
   -> verify: `grep -c '| ' ` on the ledger's group table equals the number of groups you listed; the sum of group counts equals the
   skipped total from step 1 for each section (state the arithmetic).

Hand-back sentence required: which class is the largest, and whether any `DEFECT` group exists at all.

## K2. Draft `known-limits.md` from the project state, every line sourced (about 45 min, doc only)

The RC plan's Day 2 wants `docs/known-limits.md` drafted from `docs/briefs/PROJECT-STATE.md` section 4 (open work) and section 6
(not cleared). This file does not exist yet (checked on 2026-11-06).

1. Read PROJECT-STATE.md sections 4 and 6 in full in the snapshot.
2. Write `docs/briefs/evidence/rc/known-limits-draft-2026-11-06.md` in the main checkout (draft only, not `docs/known-limits.md`).
   One bullet per limit. Each bullet has three parts: the limit in one plain sentence; the source (`PROJECT-STATE.md` section and
   number); and the evidence it rests on, as `file:line` in the snapshot or a named evidence file path that exists.
3. For each bullet, actually open the evidence path and confirm the claim. If the evidence file is missing or says something
   different, do not repair the claim: list it under "Unverified" at the bottom with what you found.
   -> verify: count the bullets, count the evidence paths you opened, and state both numbers. Run
   `for f in <every path you cited>; do test -e "$SNAP/$f" || test -e "$REPO/$f" || echo MISSING $f; done` and paste the output.

Do not write any claim about readiness, security or pen-test status beyond what the source sentence says. Do not reproduce any
private hand-back content.

## K3. Review the untracked shell-gate real-boundary test file (about 40 min, review only, no edits)

`section5/tests/unit/test_shell_gate_real_boundary.py` (272 lines) exists in the MAIN checkout as an untracked file. It is not in
the snapshot, because it is not committed. Copy it into the snapshot with `cp` so the review runs on a fixed copy:

```
cp "$REPO/section5/tests/unit/test_shell_gate_real_boundary.py" "$SNAP/section5/tests/unit/test_shell_gate_real_boundary.py"
```

Then, without editing it:
1. Run it in the snapshot. Record pass, fail, skip and xfail counts. Note whether the module-level `requires_confinement` skip
   applies on this host (report what the skip reason says).
2. For each test function, write one row: name, what it asserts in plain words, whether the assertion can fail (say how you checked:
   a mutation in the `.mine` copy of `shell_gate.py`, or a reason it cannot), and any assertion that only checks a return code
   without checking what happened.
3. Check each `xfail` reason: does it describe a symptom or a fix? Does a `strict=True` marker exist on every xfail? List any that do not.
4. Write the review to `docs/briefs/evidence/rc/shell-gate-real-boundary-review-2026-11-06.md` (main checkout). Do NOT modify the
   test file in the main checkout or in the snapshot.
   -> verify: the review has one row per `def test_` function (`grep -c 'def test_'` gives 4 on the pinned commit). Parametrised
   cases are listed under their function, not as extra rows. Paste the grep and the pytest collected count, which is higher
   (31 on the dry run: 21 passed, 10 xfailed).

Security rule for this task: the file under review is security-path. Your review is a note only. Any finding about the gate going
fail-open goes in the hand-back under "Security findings", never in the review file, and never in a test docstring.

## K4. Characterise the W28 synthesis conflict handling (about 45 min, new test file only)

The read-only synthesis of delegated child runs is in `section4/src/harness_orchestrator/delegation.py` (it reports sibling
path-overlap conflicts). Existing tests are in `section4/tests/unit/test_delegation.py`. Your job: pin the conflict rules with tests
that a future change would break.

1. Read `delegation.py` in the snapshot, find the function(s) that compute overlap between sibling children's paths, and the
   function(s) that build the synthesis. Write down, in your plan, the rule as you read it (for example: two paths conflict when
   one is a prefix of the other after normalisation). -> verify: you cite each function with `file:line`.
2. Read the existing synthesis/conflict tests in `test_delegation.py`. List what they already cover, so you do not repeat them.
3. Write a NEW file `section4/tests/unit/test_delegation_synthesis_conflicts.py` in the snapshot (copy it to the main checkout's
   `docs/briefs/evidence/rc/` as well, not into the repo tests tree). Rows, each with an expected value stated by meaning:
   identical paths conflict; a child path that is a parent directory of a sibling's path conflicts; two unrelated directories do
   not; a trailing slash or `./` spelling of the same path does not hide a conflict; a child with NO paths conflicts with nothing;
   the synthesis reports each conflicting pair once, not twice (check the order of the pair in the output).
   Where the code's answer differs from the meaning, mark that row `xfail(strict=True)` with a one-line reason, and add a finding
   to the hand-back, not to the code.
4. Negative control: in a `.mine` copy of `delegation.py` (never the original), make the overlap check always return "no conflict",
   run the new file, confirm the conflict rows fail, restore with your edit tool, prove with `cmp`.
   Run the full `test_delegation.py` and the new file together; both must pass except the xfails you declared.
   -> verify: report the pytest summary line for each run, and the `cmp` output.

Do not change `delegation.py`. Do not add a new function, class or config option to the code.

# HAND-BACK (one file per task, plus a summary)

Write `docs/briefs/hy4-b8-handback-K1.md`, `...-K2.md`, `...-K3.md`, `...-K4.md` and `docs/briefs/hy4-b8-handback-SUMMARY.md` in the
MAIN checkout (untracked; these are the same kind of files as the earlier hand-backs). Each task file has, in this order:

1. `## Assumptions`
2. `## Times`: start and finish, from `date`, to the second.
3. `## Commands run`: exact commands, with the pytest summary line for every run.
4. `## Results`: the task's own verify output, pasted.
5. `## Failures and NEEDS-REVIEWER-RUN`: every failed run and every step you could not do, with the reason.
6. `## Security findings`: K3 and K4 only; say "none" if none. Never put finding text in the review files.
7. `## Least sure of`: one sentence.

The SUMMARY holds one table: task, status (DONE / PARTIAL / BLOCKED / NOT STARTED), duration, and the one thing you are least sure of.

# WHAT THE REVIEWER KEEPS

Committing, merging, deploys, spend, all fixes behind any xfail, the refusal code for a root delegation, the exit-code ruling on
`stack_tags.py`, and every ruling in earlier batch prompts. Do not decide any of those.

# STOP CONDITIONS

Write `BLOCKED:` at the top of the affected task's hand-back and stop that task (carry on with the others) if:
* the pinned commit is missing or the import path check fails;
* a step needs a paid call, a deploy, a secret, a real private file, a docker command, or a person's decision;
* you would have to edit a production file (`*/src/*`) to make a test pass.

Finish the other tasks after a block, unless the block is in the snapshot itself (then stop the whole batch and write the SUMMARY).
