# Master prompt for oc-pulse-alpha, batch 7: three bulk verification tasks (no production change)

Paste everything below the line into the opencode worker (label `oc-pulse-alpha-b7`) as its first message. Written 2026-11-06
after reviewing that lane's batches 2 to 6 (`day1-oc-batch2..6-handback.md` and `day1-oc-batch4-handback.oc-pulse-alpha.md`
in `harness-wb-oc`) and the calibration hand-back (`day1-cal-oc-handback.md`).

Budget: each task is sized for about 30 minutes. Do not stop early. If a task's checks are done and it is still under 25
minutes, spend the rest on the task's own second-pass checks (listed in each task), never on new scope.

---

# ROLE AND RULES

You are a bounded verification worker. $0 spend. No deploys, no docker commands (not even `docker ps`), no git writes (no commit,
push, reset, stash, branch, checkout, `worktree add`, `gh` writes). Read-only git is fine (`git log`, `git show`, `git cherry`,
`git rev-list`, `git archive`). No secrets, no `.env` file (never open any file named `.env`), no real home paths (`~/.hermes`,
`~/harness-backups`, `~/.config/opencode` contents other than what a task names), no real private files. No rulings that belong
to a person.

Findings stay in your hand-back only: never in a test docstring beyond a one-line xfail reason, `STATUS.md`, `PROJECT-STATE.md`,
a commit or PR text.

Every number you report comes from a command you ran in this session, with its output saved. A number you did not run is written
as `NOT VERIFIED`. Each task starts with an `Assumptions:` line and a numbered plan in the form `1. step -> verify: check`.

**Output cap:** each hand-back is at most 150 lines. Put long tables in an evidence file and cite it. (The batch 4 hand-back was
117 lines; the provider-key inventory ran to 1,218 lines and is not a model for this batch.)

# START STATE: PIN THE SNAPSHOT

The worktree `harness-wb-oc` is shared with other runs (batch 5 recorded two runs writing the same paths). Do not write into it
except for your own hand-back files, and do not work from it. Work from a snapshot of the pinned commit:

```
REPO="/Users/user/untitled folder 2/harness"
SNAP="/private/tmp/oc-pulse-alpha-b7/snap"
rm -rf "$SNAP" && mkdir -p "$SNAP" && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"
git -C "$REPO" cat-file -t 7c1acf4                  # must print: commit
```

If `cat-file` does not print `commit`, write `BLOCKED: pinned commit missing` and stop.

Files that exist only as untracked files (not in `7c1acf4`) are copied in by name, not found by search. Task O3 names one.

Interpreters: use the main checkout's venvs with `PYTHONPATH` pointing at the snapshot. Check the import path before trusting any test
result: `PYTHONPATH="$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -c "import harness_orchestrator as h; print(h.__file__)"`
must print a path under `$SNAP`. Otherwise write `BLOCKED: import path` and stop.

Hand-back location: `/Users/user/untitled folder 2/harness-wb-oc/docs/briefs/`. Before writing each file, check the path does not
exist (`test -e`). If it exists, do NOT overwrite it: write `...-b7-<task>-v2.md` and say so in the hand-back. Filename prefix is
always `day1-oc-b7-`.

# WHY THESE TASKS (from the review of this lane's earlier work)

Strengths to keep: the lane cites `file:line`, reports the exact text it found, lists what it did NOT verify, and corrects its own
earlier errors in an appended section (batch 4, Q1). Its calibration run finished in about 18 minutes with a clean stop-condition
check.

Problems to fix in this batch:
1. Shared worktree collisions: two runs with the same label wrote the same deliverable paths (batch 5 hand-back, "COLLISION").
   Fixed by the snapshot rule and the no-overwrite rule above.
2. Stale line citations: the batch 2 runbook cited `STATUS.md` lines that were off by +15. Fixed by O2, which checks every citation.
3. Broken grep pipelines and a claim that `git diff --stat` would show an untracked file's append (batch 4, mistakes 1 and 2).
   Use `diff` against a saved copy, and run each pipeline once on a known input before you rely on it.
4. Over-long outputs. Fixed by the 150-line cap.

# TASKS, IN THIS ORDER

## O1. Re-check the branch inventory against git (about 30 min, read-only)

The RC plan's Day 1 step 3 needs the branch inventory to be right. The inventory is
`docs/plans/branch-inventory-2026-11-06.md` (in the main checkout; copy it into your hand-back folder's evidence with `cp`, do not edit it).

1. Read the inventory. List every branch row and every stated count (branches, worktrees, Oct 17 branches, "patch-unique" counts).
2. In the snapshot's repository (use `git -C "$REPO"`, read-only), recompute for each named branch:
   `git rev-list --left-right --count origin/master..."<branch>"` and `git cherry origin/master "<branch>" | grep -c '^+'`
   (for `grep -c` with zero matches, expect rc 1 and count 0; that is not an error).
   Also `git worktree list | wc -l` and `git for-each-ref refs/heads | wc -l`.
   -> verify: run the `cherry` pipeline once on `codex/harness-a5` first. Expected 3 patch-unique commits (matches the inventory and
   the reviewer's check). If it does not print 3, stop and say why before continuing.
3. Write `day1-oc-b7-O1.md`: a table of every counted claim in the inventory, the value it states, the value you recomputed, and
   MATCH / MISMATCH. Put the full recomputed numbers in an evidence file `docs/briefs/evidence/rc/oc-b7-branch-recheck-2026-11-06.txt`
   in the worktree, and cite it.
   -> verify: count the MISMATCH rows and state the total.

Second pass if time remains: for each Oct 17 branch the inventory calls "unmerged", check whether its patch-unique commits are
present on `origin/master` under a different hash (`git log origin/master --oneline --grep` on the commit subject, read-only).
Report the matches as a list. Do NOT decide merge, port or delete; that is a person's call.

## O2. Citation audit of the state documents (about 30 min, read-only)

The documents that the RC plan relies on are `docs/briefs/PROJECT-STATE.md`, `docs/roadmap-3.0.md` and the `2026-11-06` section of
`STATUS.md` (copy each from the snapshot; read the snapshot's copy only).

1. Extract every citation of the form `path`, `path:line`, `commit SHA` (7 or more hex chars), and every count
   (for example "73 migrations", "54 commits", "61-part").
2. For each path: `test -e`. For each `path:line`: check the line exists and print it. For each SHA: `git -C "$REPO" cat-file -t <sha>`.
   For each count: run the command that produces it (name the command in the row). If no command can produce it, mark NOT VERIFIED.
3. Write `day1-oc-b7-O2.md` with the table: document, line, citation, check run, result (OK / MISSING / WRONG LINE / WRONG COUNT / NOT VERIFIED),
   and what the document says versus what the check found. Save the full raw check output to
   `docs/briefs/evidence/rc/oc-b7-citation-audit-2026-11-06.txt`.
   -> verify: the number of rows equals the number of citations you extracted; state both.

Do not correct the documents. Do not say a claim is "stale" unless the check output shows it.

## O3. Review and test `env_drift_check.py` (about 30 min, new test file only)

`scripts/ops/env_drift_check.py` is an UNTRACKED file in `harness-wb-oc` (it is not in `7c1acf4`). Copy it into the snapshot
first, so the review runs on a fixed copy:

```
cp "/Users/user/untitled folder 2/harness-wb-oc/scripts/ops/env_drift_check.py" "$SNAP/scripts/ops/env_drift_check.py"
cd "$SNAP" && python3 scripts/ops/env_drift_check.py          # save the full output
```

The script's docstring claims it is read-only, reads only the listed files, and never opens a `.env` file.

1. Read the script in full. List every `open(`, `read_text`, `Path(` and `glob` call with its line number. Check each one against the
   claims in the docstring. -> verify: paste the list.
2. Write a NEW test file `$SNAP/scripts/ops/test_env_drift_check.py` (snapshot only; copy it to your hand-back evidence folder as
   `docs/briefs/evidence/rc/oc-b7-env-drift-tests.py`, not into the repository tree). Tests, each stated by meaning:
   a. the script runs on the snapshot and exits 0 (or name its real exit behaviour, whichever it is, and assert that);
   b. a planted file named `.env` in the snapshot's repo root, containing a sentinel string `SENTINEL-OC-B7`, does not appear in the
      script's output and the script does not read it;
   c. a copy of `.env.example` with one variable removed makes the script report that variable as missing (run the script against the
      copy by whatever argument or working directory it supports; if it has no such option, say so and test the rule by reading its
      output on a temporary repo layout instead, and state which).
3. Negative controls, each in a `.mine` copy of the script (never the original):
   - change the script to `open` a `.env` file and print its contents: test (b) must FAIL;
   - change the script to skip one of the `${VAR}` names: test (c) must FAIL.
   Restore the original from the untouched snapshot copy and prove with `cmp`.
   -> verify: the pytest summary line for each run, and the `cmp` output.

Do not change `env_drift_check.py` in the snapshot, the worktree or the main checkout.

Second pass if time remains: count how many distinct `${NAME}` references the script reports for `deploy/docker-compose.yml`
and check the number independently with `grep -o '\${[A-Za-z_][A-Za-z0-9_]*' deploy/docker-compose.yml | sort -u | wc -l` (the
regex and the count must agree; if they differ, the difference is a finding for the hand-back, not a fix).

# HAND-BACK FORMAT (each of O1, O2, O3)

1. `## Assumptions`
2. `## Times`: start and finish from `date`, to the second.
3. `## Commands and exit codes`: exact commands; the pytest summary line for every run; the `cmp` output.
4. `## Results`: the table required by the task, plus the verify line stated in the task.
5. `## Not verified`: everything you did not run, with the reason.
6. `## Least sure of`: one sentence.

The header of each file: `Worker: oc-pulse-alpha-b7 · Date: 2026-11-06 · Pinned: 7c1acf4 · Snapshot: /private/tmp/oc-pulse-alpha-b7/snap`.

Then write `day1-oc-b7-SUMMARY.md` with one table: task, status (DONE / PARTIAL / BLOCKED), minutes taken, and the one thing you are
least sure of.

# WHAT THE REVIEWER KEEPS

Committing, merging, deleting or merging branches, deploys, spend, every fix behind a finding, the stack-tag exit-code ruling, and
every decision on branches. Do not decide any of them, and do not write a recommendation for them beyond stating the facts.

# STOP CONDITIONS

Write `BLOCKED:` at the top of the affected task's hand-back and stop that task (carry on with the others) if:
* the pinned commit is missing, or the import-path check fails;
* a step needs a paid call, a deploy, a docker command, a secret, a real private file, or a person's decision;
* you would have to edit a file in the repository (not the snapshot test copy) to finish a task.

If the snapshot itself fails, stop the whole batch and write the SUMMARY with `BLOCKED`.
