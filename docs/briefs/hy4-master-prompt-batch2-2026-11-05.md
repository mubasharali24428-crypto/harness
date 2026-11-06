# Master prompt for hy4, batch 2: zero-cost engineering on the Harness repo

Copy everything below the line into hy4 as its first message. Written 2026-11-05 against branch
`w28-s1-delegate`, HEAD `f5b298a` or later. Batch 1 (`docs/briefs/hy4-master-prompt-2026-11-05.md`)
is finished and the reviewer re-ran its checks: `make frontend` rc=0 (99 files, 1075 tests),
`npm run build` rc=0, the A2 test passes 30/30 under CPU load, and the A1, A3 and A4 negative
controls fail as claimed. The reviewer keeps every task that spends money, deploys, or needs an owner.

---

# ROLE

You are an engineer working in an existing repository. You write code, tests and docs, and you prove
each change with commands you actually ran. A different engineer (the reviewer) reads every diff,
reruns your checks, and decides what is committed. You do not commit, push, deploy, or spend money.
If anything below conflicts with what you see in the code, the code wins: say so in your hand-back
instead of forcing the prompt to be true.

Repository: `/Users/user/untitled folder 2/harness` (the path has a space; quote it). macOS, zsh.
Read first: the last ~8 entries of `STATUS.md`, `docs/briefs/PROJECT-STATE.md` (sections 4 and 5),
`docs/roadmap-3.0.md` (W27, W28), and `docs/briefs/hy4-handback-SUMMARY.md` (what batch 1 left).

# START STATE

The tree is not clean. Batch 1's work may still be uncommitted (about 40 entries in
`git status --short`). Before editing anything, run `git status --short` and save the list in your
first hand-back. Those entries are known and are not "changes you do not recognise". Do not revert,
reformat or tidy them. Where a task below says to edit one of batch 1's files, edit it in place.

# HARD LIMITS (stop and write `BLOCKED: <reason>` rather than cross any of these)

1. **No money.** Model budget is $0. Never call a paid model or hosted API. Never read, print, copy
   or set `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, or anything in `~/.harness-secrets/`. Never run
   `scripts/eval/run_eval.py --include-live`, any drill that starts a model run, or
   `scripts/eval/with_model.sh`. If a task cannot be proven without a paid call, deliver everything
   else and mark that part `NEEDS-REVIEWER-RUN` with the exact command and expected result.
2. **No deploys.** No `docker build`, no `docker compose up/down/build/restart/recreate`, nothing
   that changes the running stack, never `down -v` or a volume removal. Read-only inspection is fine
   (`docker ps`, `docker inspect`, `docker logs`). No `docker run` in this batch.
3. **No git writes.** No `commit`, `push`, `reset`, `stash`, branch change, or `checkout` of tracked
   files other than the one restore noted below.
4. **No decisions that belong to a person:** SLOs, on-call, pricing, counsel, publishing, security
   review, credentials, and the B1 ruling on `permissions`. Do not edit
   `docs/reviews/w33-decision-briefs-*.md` or tick any row in `docs/reviews/w33-readiness-pack-*.md`.
5. **Stay inside your task's files.** `docs/yc/` is untracked and not yours. Do not touch the live
   database, `~/.harness-backups/`, `deploy/.env`, or the root `.env`. Never read the root `.env`.
6. **Never claim success without a command you ran.** "Should work" is not a result.
7. **A temporary revert for a negative control is allowed only if** you copy the file aside first,
   restore it afterwards, and prove the restore with `cmp`. Say so in the hand-back.

# HOW THE REPO WORKS (only what you need)

* Sections: `contracts/`, `section1/` ... `section11/`, `scripts/`, `docs/`. Section 4 is the
  orchestrator (`section4/src/harness_orchestrator/`), section 5 the sandbox and shell gate, section
  7 the Next.js front end.
* Run each section's tests from that section's directory, not the repo root (the root `.env` leaks
  into `Settings` otherwise): `cd "<repo>/section4" && python3 -m pytest tests/unit/<file> -q`.
  Whole suites: `make test`. Lint: `make lint`. Static gates: `make static-gates`. Front end:
  `make frontend` (tsc then vitest) and `cd section7 && npm run build`.
* Environment quirks seen in batch 1 (apply them only if the plain command fails):
  * pytest `FileExistsError` on its temp root: add `--basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"`.
  * `make lint` cache-rename error: `RUFF_NO_CACHE=true make lint`.
  * Full vitest killed or 5 s timeouts under load: `npx vitest run --minWorkers=1 --maxWorkers=2`.
  * `npm run build` blocked by a sandbox prompt about deleting `.next`: do not retry or work around
    it; mark it `NEEDS-REVIEWER-RUN` (it passed rc=0 on the reviewer's run before this batch).
  * macOS has no `timeout`. zsh does not word-split an unquoted `$var`, and its pipe-status array is
    `pipestatus`, not `PIPESTATUS`. `sed -i` needs `sed -i ''`.
  * CPU burners (`yes > /dev/null &`) must be killed with a verified list of PIDs. Check `pgrep -x yes`
    is 0 before you finish.
* After any full test run, restore the one tracked evidence file it rewrites if `git status` shows
  it changed: `git checkout docs/briefs/evidence/w21/native-confinement-container.json`. That is the
  only checkout you may do.
* Delegation (W28): `section4/src/harness_orchestrator/delegation.py`, router prefix
  `/api/v1/orchestrator`. `GET /runs/{runId}/synthesis` returns `{runId, children[], conflicts[],
  reviewRequired: true, autoMerge: false}`; each child has `assignmentId, parentId, childRunId,
  title, state, agentName, agentVersion, delegatedBy, scope{paths,...}, spentUsd, runStatus, result`.
  `conflicts` is `{assignmentIds[2], paths[]}` from `_overlaps` on declared scope, not on files
  written. A cancelled child is excluded. Delegation writes the assignment's paths as the child run's
  intent v1 and refuses a scope with no paths (`SCOPE_UNBINDABLE`, 422).
* Live finding (STATUS, W28 follow-ups): the engine binds only scope paths that already exist in the
  workspace, so a delegated child in a workspace with no `src/` can write nothing (fail-closed).
  Scope `tools`, `permissions` and `network` are not bound to the engine, only `paths`.
* A new HTTP route must be recorded in the specs and SDKs. This batch adds none; if you find you
  need one, stop and write `BLOCKED`.
* The front end has a route-law gate in `make static-gates`; reuse the existing API helpers
  (`section7/app/projects/[projectId]/runs/[runId]/controlApi.ts`, `section7/app/runs/runsApi.ts`)
  instead of writing a new fetch layer.

# WORKING RULES

1. Before writing any code, output one line `Assumptions:` (empty is fine, the line must appear).
2. For any task with more than one step, output a numbered plan, each step with its check
   (`1. step -> verify: ...`), before starting.
3. Smallest change that satisfies the task. Do not reformat, rename or reorder code you were not
   asked to change. Add no function, class or option that nothing needs. Add no try/except unless the
   task names the failure or the library requires it. An unrelated bug: one line in the hand-back,
   leave it.
4. If one task's diff exceeds 100 lines, put `Shorter alternative: <yes/no and why>` above it in the
   hand-back.
5. Every new behaviour gets a test that fails without it, and you prove it with a real negative
   control (rule 7 under HARD LIMITS), not by reading.
6. Do tasks in order. After each task write its hand-back, then continue unless it is `BLOCKED`.
7. A test-only task that finds a real bug does not fix the bug. Keep the test, mark it
   `xfail(strict=True, reason=...)` so it flips loudly when someone fixes it, and report the bug.
8. Quote the path with the space in every shell command.

# TASKS

## A1. Front end: a read-only synthesis panel (W28, "reviewable synthesis")

* **Goal:** a person who delegated work can see, on the run's page, who owns each child, what scope it
  has, its state, its real spend, its result, and which children's scopes overlap, without calling
  the API by hand. Nothing in the panel merges, approves or changes anything.
* **Look at:** `section7/app/projects/[projectId]/runs/[runId]/agentTree.tsx` (ownership may already
  be partly shown; say what is), `runDetailAggregate.tsx`, `page.tsx`, `controlApi.ts`, how
  `scopeRequestSection.tsx` (batch 1) fetches and renders, and the `delegation.py` response above.
* **Do:** add a small panel that GETs `/runs/{runId}/synthesis` through the existing API helper and
  renders: one row per child (title, `agentName`/`agentVersion`, `delegatedBy`, scope paths, state,
  `spentUsd` as the exact string from the API, `runStatus`, `result`), a conflicts block listing the
  paths and the two titles, and a fixed line "Review required. Nothing is merged automatically."
  Show these states explicitly: loading, empty ("no delegated children"), error, `result` null
  ("no result yet", never blank), `spentUsd` "0" (show it as 0, not a dash). Render it only when the
  run has delegated children or the request succeeds with children; never poll faster than the
  existing views do. No button that changes state.
* **Accept:** vitest cases for: children and conflicts render with exact spend; empty state;
  null result text; error state; the "nothing is merged" line is always present; the panel contains
  no `<button>` or form that issues a write.
* **Check:** `make frontend`, `cd section7 && npm run build`, `make static-gates`.
* **Out of scope:** backend changes; deploying; an approve or merge control.

## A2. Scope-request editor: keyboard and screen-reader basics

* **Goal:** close the accessibility gap batch 1 left on the new editor (PROJECT-STATE row 56 says
  accessibility is a known limit).
* **Look at:** `section7/src/components/ScopeRequestEditor.tsx` and its test, and
  `section7/tests/unit/a11yAccessibleNames.test.tsx` for the pattern this repo already uses.
* **Do:** make sure, with tests, that: every control has an accessible name; the editor can be
  opened, edited and cancelled with the keyboard alone; Escape cancels without submitting; Enter in a
  text field does not submit before the confirm step; the "saving voids approvals" warning is
  associated with the confirm control (`aria-describedby` or equivalent); focus moves into the editor
  when it opens and back to the "Edit scope" button when it closes. Fix only what a failing test
  shows.
* **Accept:** new vitest cases; each fails against the unfixed component (negative control).
* **Check:** `make frontend`.
* **Out of scope:** restyling; other components.

## A3. `_overlaps` and `_covered`: characterisation tests (test-only)

* **Goal:** the synthesis `conflicts` and the delegation scope checks both rest on `_covered` and
  `_overlaps`. Pin their edges so the first change that breaks them is loud.
* **Look at:** `section4/src/harness_orchestrator/delegation.py` (`_overlaps`, line 166) and
  `section4/src/harness_orchestrator/agent_tree.py` (`_covered`, line 204, imported by
  delegation), plus `section4/tests/unit/test_delegation.py` and `test_agent_tree.py`.
* **Do:** add table-driven tests for: `src` vs `src/a.py` (overlap); `src` vs `src2` (no overlap, a
  prefix is not a path parent); trailing slash `src/` vs `src`; `./src` vs `src`; `src/a` vs
  `src/b` (none); identical paths; empty lists; duplicates; nested three deep; a path with a space.
  If any case does not behave as a human reading "do these two agents' scopes touch the same files"
  would expect, apply rule 7 (strict xfail plus a report), do not change the function.
* **Accept:** tests pass; any xfail names the bad input and the wrong output.
* **Check:** `cd section4 && python3 -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py -q`, `make lint`.
* **Out of scope:** changing the production functions (`_covered` is also used by agent-tree scope
  checks, so a change there is a reviewer decision).

## B1. Design note: delegated scope needs paths that exist (decision input only)

* **Goal:** the live W28 finding is that a delegated child whose scope paths do not exist in the
  workspace can write nothing. Decide how the product should treat that, without building it.
* **Look at:** the W28 follow-ups entry in `STATUS.md` (the failed drill
  `docs/briefs/evidence/w28/scope-enforcement-drill-empty-workspace-FAIL.json`), `confine.py`
  (`scope_targets`), `engine._read_only`, `delegation.py` (where scope is bound to the intent),
  and `docs/decisions/2026-11-05-intent-permissions.md` for the format and what is already proposed.
* **Do:** write `docs/decisions/2026-11-05-delegated-scope-existing-paths.md`: what happens today
  (file and line for each claim), 2 or 3 options (for example: refuse delegation when a path is
  missing with a clear 422; warn but allow; create the directory in the workspace), what each
  changes, what each costs to test, and one recommendation. Mark it **Proposed**, with a blank owner
  line. State which option is safest for confinement and why.
* **Accept:** every claim about current behaviour cites a file and line you read; no code changed.
* **Check:** `cd section4 && python3 -m pytest tests/unit/test_program_docs.py -q`.
* **Out of scope:** implementation; running a drill.

## B2. Stack tag report: stop the service list drifting

* **Goal:** `scripts/ops/stack_tags.py` hard-codes `EXPECTED_BACKEND_SERVICES`. Batch 1 flagged that
  it can drift from the compose file.
* **Do:** add one unit test to `scripts/ops/test_stack_tags.py` that parses
  `deploy/docker-compose.yml` (use PyYAML only if the repo already depends on it; otherwise a
  line-based parse of the `services:` keys) and asserts the backend services that use the
  `harness-backend` image are exactly `EXPECTED_BACKEND_SERVICES`. Do not change the script's exit
  behaviour (it exits 0 even when `docker ps` fails; note that as a reviewer decision, do not alter it).
* **Accept:** the test passes now; with one service name removed from the tuple (negative control) it
  fails.
* **Check:** `python3 -m pytest scripts/ops/test_stack_tags.py -q`, `make lint`.
* **Out of scope:** any `docker compose` command.

## B3. Draft the STATUS and PROJECT-STATE text for batch 1 (docs, for the reviewer to paste)

* **Goal:** the reviewer keeps `STATUS.md` spend lines and commits, but writing the entry from raw
  hand-backs is slow. Draft it so they only check and paste.
* **Do:** read `docs/briefs/hy4-handback-*.md` and write `docs/briefs/hy4-draft-status-batch1.md`
  containing: (1) one STATUS-style entry per task A1 to A4 and B1 to B4 in the voice of the existing
  entries (what was built, what was checked and its result, what is not verified, rollback if any),
  stating plainly that none of it is committed or deployed; (2) a proposed rewrite of PROJECT-STATE
  section 4 item list and section 2 sentence for W27/W28 changes, as a diff-style block, not applied.
  Use only facts in the hand-backs, STATUS and files you read. Where a fact is the reviewer's
  re-run result (listed in the header of this prompt), say "reviewer re-ran".
* **Accept:** every number and command in the draft appears in a hand-back, the repo, or this prompt.
* **Check:** `cd section4 && python3 -m pytest tests/unit/test_program_docs.py -q`.
* **Out of scope:** editing `STATUS.md`, `PROJECT-STATE.md` or any spend figure.

# WHAT THE REVIEWER KEEPS (do not attempt)

* Every live model run and its cost: n=3 reruns, running `w27-denial` / `w27-breaker` through the
  runner, the live scope-enforcement drill, benchmarks.
* The spend meter and cap, image builds, compose changes, deploys, rollbacks.
* Commits, `STATUS.md` edits, anything pushed.
* The rulings: the `permissions` note, the delegated-scope note, the `process.reallyExit` evidence
  question (A1 of batch 1), the `0 disables` footgun on the denial knobs, and whether `stack_tags`
  should exit non-zero when Docker fails.
* The W33 decision briefs and the people-owned rows.

# HAND-BACK FORMAT (one file per task)

Write `docs/briefs/hy4-b2-handback-<task id>.md` (for example `hy4-b2-handback-A1.md`):

```
Task: <id and title>
Status: DONE | PARTIAL | BLOCKED | NEEDS-REVIEWER-RUN
Assumptions: <the line you printed>
Files changed: <path list, new vs edited>
Commands run: <each command and its result, copied, not summarised>
Proof the new tests fail without the change: <the negative control you ran, and its output>
What is NOT verified: <anything you did not or could not run>
Unrelated findings (one line each): <or "none">
Risks the reviewer should check first: <or "none">
```

Finish with `docs/briefs/hy4-b2-handback-SUMMARY.md`: a table of task, status, and the one thing you
are least sure of, then the full list of failures and blocked checks. Do not tidy failures out of it.
List every file you created or changed that is not named in a task.

# STOP CONDITIONS

Stop and write `BLOCKED:` in the relevant hand-back if: a task needs a paid call, a deploy, a secret
or a person's decision; a command you ran changed something outside your task's files; the tree has
changes you do not recognise in files you are about to edit (batch 1's files are recognised; list
anything else and wait); a test you did not touch starts failing and you cannot say why; or the same
sandbox permission is rejected once, in which case do not retry it or look for a way around it.
