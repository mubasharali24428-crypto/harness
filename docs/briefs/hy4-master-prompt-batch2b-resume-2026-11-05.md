# Resume prompt for hy4, batch 2b: finish batch 2 after the A1 block

Copy everything below the line into hy4 as its first message. Written 2026-11-05 against branch
`w28-s1-delegate`, HEAD `f5b298a` (nothing committed yet).

---

# WHAT HAPPENED

Your batch 2 stopped at A1 because the sandbox rejected one `cp` that would overwrite a tracked file
(`controlApi.ts`) to stage a negative control. The batch 2 stop condition told you to end the whole
batch on a single rejected permission. That rule was too broad and is withdrawn (see AMENDMENTS).

The reviewer then finished A1 themselves, so do not redo it:

* The A1 code is accepted as you wrote it (`synthesisPanel.tsx`, `synthesisPanel.test.tsx`, the
  `getSynthesis` helper in `controlApi.ts`, the mount in `page.tsx`). Leave those four files alone.
* Reviewer negative controls: seven separate mutations (null-result text removed, review line
  removed, `"0"` rendered as a dash, conflicts block removed, empty-state text removed, wrong URL,
  a Merge button added) each made exactly one of your 8 tests fail; files restored and checked with
  `cmp`; the 8 tests pass again.
* Reviewer checks on the A1 diff: `make frontend` rc=0 (1083 tests), `npm run build` rc=0,
  `make static-gates` rc=0.
* Your open questions on A1 are decided: revealing the panel only after a successful response with
  children is accepted; `spentUsd` stays the raw API string; the overview tab placement is accepted.

# YOUR JOB

Do tasks **A2, A3, B1, B2, B3** exactly as written in
`docs/briefs/hy4-master-prompt-batch2-2026-11-05.md`. Read that whole file first. Everything in it
applies (role, hard limits, repo facts, working rules, task text, hand-back format, what the reviewer
keeps) except the amendments below. Skip A1. Hand-backs go in
`docs/briefs/hy4-b2-handback-<id>.md` as before, and the final summary is
`docs/briefs/hy4-b2-handback-SUMMARY.md`; the summary lists A1 as "DONE by reviewer, see prompt
2b", not as yours.

# AMENDMENTS (these override batch 2 where they differ)

1. **A rejected permission blocks only the step that needed it.** Do not retry it, do not look for
   another way to do the same thing, and do not stop the batch. Mark that one check
   `NEEDS-REVIEWER-RUN` with the exact command and expected result, then carry on with the rest of
   the task and the next tasks. Only stop the task (not the batch) if it cannot be meaningfully
   finished without that step.
2. **Negative controls must not overwrite a tracked or existing file with `cp`, `mv` or a shell
   redirect.** That is what was rejected. Do them this way instead:
   * First save the file you will mutate to a new path with `cp <file> /private/tmp/hy4-b2-<id>/<name>.mine`
     (creating a new file is allowed).
   * Mutate it with your file-edit tool (a single targeted change that removes the behaviour under
     test), run the test and capture the failing output, then restore it with your file-edit tool.
   * Prove the restore: `cmp <file> /private/tmp/hy4-b2-<id>/<name>.mine` must print nothing and
     `git diff --stat <file>` must be the same as before the control.
   * Alternatively, write the mutated version to a new file under a new name beside the original and
     point a copy of the test at it; then delete only the files you created, or list them in the
     hand-back if deleting is refused.
   * If the edit tool is also refused, record the control as `NEEDS-REVIEWER-RUN` per amendment 1.
3. **Cleanup is part of the task.** Remove any `/private/tmp/hy4-b2-*` copies you made only if deletion
   is allowed; otherwise list them. Never leave a mutated file in the tree; show `cmp` or
   `git diff --stat` proving every file you mutated is back.

# TASK REMINDERS (details are in the batch 2 file)

* **A2** `ScopeRequestEditor` keyboard and screen-reader behaviour: tests first, each failing without
  the fix; only fix what a failing test shows.
* **A3** `_overlaps` / `_covered` characterisation tests, test-only; a case the function gets wrong
  becomes `xfail(strict=True)` plus a report, never a production change. `_covered` is in
  `section4/src/harness_orchestrator/agent_tree.py:204`.
* **B1** design note `docs/decisions/2026-11-05-delegated-scope-existing-paths.md`, Proposed, blank
  owner line, every current-behaviour claim cited to a file and line, no code.
* **B2** one unit test in `scripts/ops/test_stack_tags.py` tying `EXPECTED_BACKEND_SERVICES` to
  `deploy/docker-compose.yml`; do not change the script's exit behaviour.
* **B3** draft `docs/briefs/hy4-draft-status-batch1.md` from the hand-backs; do not edit `STATUS.md`
  or `PROJECT-STATE.md`. In the draft, record that the reviewer re-ran the batch 1 checks and the A1
  (batch 2) checks listed above.

# FINISH

End with `docs/briefs/hy4-b2-handback-SUMMARY.md`: a table of task, status, and the one thing you are
least sure of, then every failure, blocked check and `NEEDS-REVIEWER-RUN` item, then every file you
created or changed that is not named in a task. Do not tidy failures out of it.
