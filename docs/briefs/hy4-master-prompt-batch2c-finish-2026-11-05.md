# Finish prompt for hy4, batch 2c: close out batch 2

Copy everything below the line into hy4 as its first message. Written 2026-11-05 (late) against
branch `w28-s1-delegate`, HEAD `f5b298a`, nothing committed.

---

# WHERE BATCH 2 STANDS

Read `docs/briefs/hy4-master-prompt-batch2-2026-11-05.md` and
`docs/briefs/hy4-master-prompt-batch2b-resume-2026-11-05.md` first. Both still apply in full (hard
limits, repo facts, working rules, the 2b amendments on permissions and negative controls). This
prompt only covers what is left.

| Task | State | Evidence |
|---|---|---|
| A1 synthesis panel | DONE, accepted by the reviewer | reviewer ran seven mutations, `make frontend`, build, static-gates, all green |
| A2 editor accessibility | DONE (your hand-back) | reviewer re-ran `make frontend` rc=0 and `make lint` rc=0 |
| A3 overlap tests | DONE (your hand-back) | reviewer re-ran `test_delegation.py` + `test_agent_tree.py`: 83 passed, 3 xfailed |
| B1 delegated-scope design note | written | not yet reviewed; do not change it |
| B2 stack-tag drift test | **BROKEN, no hand-back** | see below |
| B3 STATUS draft | **NOT STARTED** | `docs/briefs/hy4-draft-status-batch1.md` does not exist |
| SUMMARY | **STALE** | still says the batch stopped at A1 and A2 to B3 never started |

**B2 defect the reviewer found:** `python3 -m pytest scripts/ops/test_stack_tags.py -q` fails at
collection: `scripts/ops/test_stack_tags.py:108: NameError: name 'Path' is not defined`. The
compose-alignment test uses `Path` without importing it. Batch 2 B2 said the test must pass now;
it does not.

# YOUR JOB, IN ORDER

## 1. Finish B2

* Fix the missing import (nothing else in the file). Run
  `python3 -m pytest scripts/ops/test_stack_tags.py -q`: the original 7 tests plus the new
  compose-alignment test must pass.
* Negative control per the 2b amendments (never overwrite with `cp`/`mv`; save a `.mine` copy to a
  new path, mutate with your edit tool, restore with your edit tool, prove with `cmp`): remove one
  service name from `EXPECTED_BACKEND_SERVICES` in `scripts/ops/stack_tags.py` and show the new test
  fails; separately, show it fails if the test were pointed at a compose service that does not use
  the `harness-backend` image (for example by naming `frontend`).
* Run `make lint` and one real read-only run `python3 scripts/ops/stack_tags.py` (paste its output).
* Do not change the script's exit behaviour.
* Write `docs/briefs/hy4-b2-handback-B2.md` in the standard format. State in "What is NOT verified"
  that the first version failed at collection and that you caught it only when told.

## 2. Do B3

Write `docs/briefs/hy4-draft-status-batch1.md` exactly as batch 2 task B3 describes: one STATUS-style
entry per batch 1 task (A1 to A4, B1 to B4) and a diff-style proposal for PROJECT-STATE sections 2
and 4, none applied. Use only facts from the batch 1 hand-backs, `STATUS.md`, files you read, and
these reviewer re-runs (mark them "reviewer re-ran"):

* Batch 1: `make frontend` rc=0 (99 files, 1075 tests); `npm run build` rc=0; `test_reclaimed_run_fences_the_original_worker`
  30/30 under CPU load while the old version failed 1 of 30; A4 negative control 23 failed / 28
  passed without the change and 51 passed with it; A3 negative control failed
  `ScopeRequestEditor.test.tsx` without the `scopeRequest` assignment; A1 drill `PASS`, and `FAIL`
  with a writable workspace mount.
* Batch 2: A1 seven mutations each failed exactly one test; `make frontend` rc=0 (1083 tests), build
  rc=0, `make static-gates` rc=0; later `make frontend` rc=0 and `make lint` rc=0 with A2 in the tree.

Keep two states clearly apart in every entry: "uncommitted in the working tree" and "deployed". Nothing
from batches 1 or 2 is deployed. Check with the test the batch 2 prompt names:
`cd section4 && python3 -m pytest tests/unit/test_program_docs.py -q`.

## 3. Refresh the summary and the A1 hand-back

* Rewrite `docs/briefs/hy4-b2-handback-SUMMARY.md` from scratch to match reality: task table for
  A1 to B3 with real statuses (A1 "DONE by reviewer, your code accepted", your own A2, A3, B1, B2, B3
  statuses), then every failure, `NEEDS-REVIEWER-RUN` item and file changed outside the tasks. Keep
  the history that the batch stopped once on a rejected permission, as one short paragraph; do not
  delete it. Include the three A3 `xfail` findings by name (bad input, wrong output) as items for the
  reviewer to rule on.
* Append a section "Superseded" at the end of `docs/briefs/hy4-b2-handback-A1.md` saying the
  negative control and the three acceptance checks were run by the reviewer (results as above). Do not
  edit the rest of that file.

## 4. Stop

Do not start new work. After the summary, list in your final message the decisions waiting for the
reviewer: the three `xfail` findings, B1's recommendation, and anything marked `NEEDS-REVIEWER-RUN`.

# STOP CONDITIONS

Same as batch 2 as amended in 2b: a rejected permission blocks only that step (mark it
`NEEDS-REVIEWER-RUN`, carry on). Stop and write `BLOCKED:` if a task needs a paid call, a deploy, a
secret or a person's decision, or if you find changes in files you did not touch that are not listed in
`git status` from your first hand-back.
