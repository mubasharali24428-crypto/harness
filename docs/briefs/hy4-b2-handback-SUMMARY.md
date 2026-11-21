# hy4 batch 2 hand-back — SUMMARY (rewritten 2026-11-05, batch 2c)

Branch `w28-s1-delegate`, HEAD `f5b298a`. **All work is uncommitted in the
working tree. Nothing from batches 1 or 2 is deployed.** Nothing was committed,
pushed, built into an image, recreated in compose, or spent. No secret was read
or set, no paid or hosted model call was made, no drill that starts a model run
was run, no `docker build` / `docker compose up|down|build|restart|recreate`
was run, and no git write (commit, push, reset, stash, branch change, checkout)
was performed. The live stack was read only (`docker ps`); `~/.harness-backups/`,
`deploy/.env`, the root `.env`, `docs/yc/`, `STATUS.md`,
`docs/briefs/PROJECT-STATE.md` and the W33 decision/readiness docs were left
alone. `docs/briefs/evidence/w21/native-confinement-container.json` is
unchanged (no full suite was run), so the one permitted `git checkout` restore
was never needed.

## History: the batch stopped once on a rejected permission

Batch 2 first stopped at A1: the rule-7 negative control needed a temporary
revert of a tracked file, and a `cp` overwrite of
`section7/app/projects/[projectId]/runs/[runId]/controlApi.ts` was refused by
the sandbox (`delete .../controlApi.ts (rule: **)`, exit 13). The stop condition
then in force ended the whole batch; it was not retried, no equivalent was
substituted, and no workaround was sought. Batch 2b withdrew that too-broad
rule (a rejected permission now blocks only the step that needed it), the
reviewer finished A1 themselves, and 2b/2c completed the rest. That history is
kept here in one paragraph; the original detail stays in
`hy4-b2-handback-A1.md`, whose new "Superseded" section records the reviewer's
runs.

## Task table — nothing tidied out

| Task | Status | The one thing I am least sure of |
|---|---|---|
| A1 — read-only synthesis panel | **DONE by reviewer, your code accepted** (see prompt 2b; not my work) | The reviewer's seven mutations each failed exactly one of the 8 tests, but no test proves the browser-side mount in `page.tsx`; node-env tests render the pure panel only. |
| A2 — ScopeRequestEditor keyboard / screen-reader basics | **DONE** (my hand-back) | Real browser focus behaviour and a screen reader reading the warning are unproven: node-env vitest walks the element tree and invokes handlers directly. |
| A3 — `_overlaps` / `_covered` characterisation tests | **DONE** (my hand-back) | Whether `trailing-slash-prefix-asymmetry` (`src` under `["src/"]` → `False`) also deserves a strict xfail; I characterised it as current behaviour rather than pinning it. |
| B1 — delegated-scope / existing-paths design note | **DONE** (my hand-back), written, not yet reviewed | Option A (refuse delegation with 422 when a path is missing) is a product call. No owner has ruled; the note stays Proposed with a blank owner line. |
| B2 — stack tag drift unit test | **DONE** (fixed in 2c; my hand-back) | The compose parse is line-based and only recognises `<<: *backend-build`; if compose expresses backend services another way, the test drifts silently. |
| B3 — draft STATUS / PROJECT-STATE batch-1 text | **DONE** (done in 2c; my hand-back) | The reviewer re-run numbers (A2 30/30, A4 23 failed / 28 passed) differ from the batch 1 hand-back numbers (60/60, 5 failed / 46 passed). Both are recorded, not reconciled. |
| SUMMARY (this file) | **DONE** — rewritten from scratch in 2c | The previous version was stale: it said the batch stopped at A1 and A2–B3 were never started. The stop is kept as history above; the table now matches reality. |

## What was delivered (one line each)

- **A1** — `controlApi.ts` `getSynthesis` + three wire types (28 lines), one
  import and `<SynthesisSection>` in `page.tsx` (12/1), new
  `synthesisPanel.tsx` (131 lines) and `synthesisPanel.test.tsx` (135 lines, 8
  cases). My runs: 8/8 vitest rc=0, `tsc --noEmit` rc=0. **Reviewer re-ran**
  seven mutations (each failed exactly one test, restored with `cmp`),
  `make frontend` rc=0 (1083 tests), `npm run build` rc=0, `make static-gates`
  rc=0. Accepted.
- **A2** — 15 lines in `ScopeRequestEditor.tsx`, 9 in
  `scopeRequestSection.tsx`, new `scopeRequestEditorA11y.test.tsx` (7 cases).
  Tests first: 7 failed against the unfixed component; after the fix 13 passed
  (2 files) and four negative controls covered all 7 cases; `make frontend`
  rc=0 (101 files / 1090 tests) with a 600 s tool timeout. **Reviewer re-ran**
  `make frontend` rc=0 and `make lint` rc=0.
- **A3** — table-driven characterisation tests in `test_delegation.py` and
  `test_agent_tree.py` (10 + 11 cases) plus three `xfail(strict=True)` cases.
  83 passed / 3 xfailed, `make lint` rc=0. Two real negative controls, `cmp`
  restore proofs. **Reviewer re-ran** the two files: 83 passed, 3 xfailed.
- **B1** — `docs/decisions/2026-11-05-delegated-scope-existing-paths.md`:
  Proposed, blank owner line, every current-behaviour claim cited to a
  file:line, three options with test costs, one recommendation. No code.
  `test_program_docs.py` 22 passed.
- **B2** (2c) — `scripts/ops/test_stack_tags.py`: added the one missing
  `from pathlib import Path`. 7 original + 1 compose-alignment test pass (8
  passed, rc=0). Two negative controls (remove `siem-push` from the tuple;
  name the non-backend `frontend` service) each made the new test fail;
  `cmp` proved both restores; `make lint` rc=0; root ruff on the two files
  rc=0; one real read-only `stack_tags.py` run rc=0 printed
  `Agreement: MIXED` (10 × `vw28s6`, `orchestrator` `vw27s3`). The script's
  exit behaviour is unchanged.
- **B3** (2c) — `docs/briefs/hy4-draft-status-batch1.md` (296 lines): eight
  STATUS-style entries, a marked "reviewer re-ran" list for both batches, and
  diff-style proposals for PROJECT-STATE §2 and §4. Nothing applied;
  `test_program_docs.py` 22 passed.

## Full list of failures, blocked checks and NEEDS-REVIEWER-RUN items (not tidied out)

1. **Rejected sandbox permission** (history paragraph above) — the batch 2
   stop. Not retried; the rule was withdrawn in 2b.
2. **A1 was BLOCKED in its first hand-back**: the negative control and
   `make frontend` / `npm run build` / `make static-gates` were NOT RUN by me,
   and A1 was explicitly "not accepted". Closed by the reviewer (seven
   mutations, 1083 tests, build rc=0, static-gates rc=0); recorded in the A1
   hand-back's "Superseded" section. The gap is closed by the reviewer, not by
   a rerun of mine.
3. **A1 unverified**: no test proves the `SynthesisSection` mount in
   `page.tsx`; the 10 s poll and reveal-after-children gating are pinned by a
   source-parity assertion; no live `/synthesis` call (stubbed `fetch`).
   Nothing deployed.
4. **A2 environment failures**: two foreground `make frontend` / vitest
   attempts were killed at the 120 s tool ceiling (exit 137 SIGTERM, no
   summary). With a 600 s timeout the same commands passed (101 files / 1090
   tests, rc=0). Not code faults.
5. **A2 unverified**: real browser focus movement, a screen reader reading
   `aria-describedby`, and real DOM keyboard events. Node-env vitest cannot run
   `useEffect` or DOM focus. A Playwright/browser check is a reviewer item; I
   did not invent a command for it.
6. **A2 reviewer question**: whether editing `scopeRequestSection.tsx`
   (the editor's own host) counts as "other components" under A2's out-of-scope
   line; also `useRef` reads `document.activeElement` during render.
7. **A3 three strict xfails are real bugs, reported and not fixed** (listed by
   name below). `make static-gates` was not run for A3 (the task requires only
   the two pytest files and `make lint`). Batch 1's pytest `FileExistsError`
   on the shared temp root still appears without a unique `--basetemp`.
8. **A3 reviewer question**: the `trailing-slash-prefix-asymmetry` case was
   characterised, not xfailed; it may be a second bug at the reviewer's
   discretion.
9. **B1 unverified**: no drill was run (out of scope); the recommendation is
   Proposed with no owner ruling; whether the existence check belongs in
   `_norm_scope` or only the delegation endpoint is left open. The note's
   `service.py:1617-1624` line numbers were read at HEAD `f5b298a`.
10. **B2 first version was BROKEN and had no hand-back**: the compose-alignment
    test used `Path` without importing it, so
    `python3 -m pytest scripts/ops/test_stack_tags.py -q` failed at collection
    (`scripts/ops/test_stack_tags.py:108: NameError: name 'Path' is not
    defined`). **I did not catch this myself**; the reviewer found it and told
    me in the 2c prompt. Fixed in 2c with one import; 8 passed afterwards.
    This is stated in `hy4-b2-handback-B2.md` under "What is NOT verified".
11. **B2 `NEEDS-REVIEWER-RUN`**: the literal
    `cd "/Users/user/untitled folder 2/harness" && python3 -m pytest scripts/ops/test_stack_tags.py -q`
    returns rc=1 on this host (`No module named pytest`) because `python3` on
    PATH is the WorkBuddy-managed 3.13.12 interpreter. Expected on the
    reviewer's shell: `8 passed`, rc=0. It passed here with `/usr/bin/python3`
    (pytest 8.4.2) and with `section4/.venv/bin` on PATH (pytest 9.1.1).
12. **B3 `NEEDS-REVIEWER-RUN`**: the literal
    `cd "/Users/user/untitled folder 2/harness/section4" && python3 -m pytest tests/unit/test_program_docs.py -q`
    has the same interpreter issue here. Expected on the reviewer's shell:
    `22 passed`, rc=0. It passed here twice with `section4/.venv/bin` on PATH
    (`22 passed in 8.12s`, then `22 passed in 7.29s` after the B3 hand-back
    landed).
13. **B2 lint coverage**: `make lint` loops over section1..section11 and does
    not include `scripts/ops/`. I ran root ruff on the two files directly
    (`All checks passed!`, rc=0), but a reviewer may want their own plain
    `make lint` on their host.
14. **B3 not applied (by design, not a failure)**: the eight STATUS entries and
    both PROJECT-STATE diff blocks are proposals. `git diff --stat -- STATUS.md
    docs/briefs/PROJECT-STATE.md` is empty. The proposed §4 rewrite would grow
    `PROJECT-STATE.md` from 112 to about 124 lines (docs gate allows 130); that
    is arithmetic, not a gate run against a modified file.
15. **Historical batch 1 items carried into the draft**: `make frontend` at
    default parallelism rc=1 on two pre-existing 5 s timeouts;
    `npm run build` NEEDS-REVIEWER-RUN (broker `EPIPE` plus a user-rejected
    `.next` delete). The reviewer re-ran both rc=0 (99 files / 1075 tests;
    build rc=0).
16. **No other failure**: both 2c negative controls failed as intended and
    were restored (`cmp` rc=0); no command changed a file outside my task
    files; the final `git status --short` differs from the start-state baseline
    only by the three new untracked files written in 2c (see below).
17. **No `git checkout` restore was needed or done.**

## Three A3 xfail findings for the reviewer to rule on (bad input → wrong output)

All three are `xfail(strict=True)`; they flip to XPASS-and-fail the moment
someone fixes the `./` normalisation. `_covered` (`agent_tree.py:204`) compares
strings without normalising a leading `./`, and `_norm_scope`
(`agent_tree.py:179-201`) accepts that shape, so it can reach the engine.
Fixing `_covered` is a reviewer decision because it is also used by the
agent-tree scope checks.

1. **bad input `a=['./src'], b=['src']` → wrong output `[]` (no overlap).**
   (`test_delegation.py::test_overlaps_dot_slash_is_the_same_directory`) Both
   scopes name the same directory, so a human expects a non-empty shared-path
   list.
2. **bad input `path='./src', prefixes=['src']` → wrong output `False`.**
   (`test_agent_tree.py::test_covered_dot_slash_path_is_under_src`) `./src`
   and `src` are the same directory, so a human expects `True`.
3. **bad input `path='src', prefixes=['./src']` → wrong output `False`.**
   (`test_agent_tree.py::test_covered_dot_slash_prefix_covers_src`) The
   prefix carries the `./`; a human again expects `True`.

## Every file created or changed that is not named in a task

In the repository (all are hand-backs required by working rule 6 / the
HAND-BACK FORMAT, not by any task text):

- `docs/briefs/hy4-b2-handback-A1.md` (new in batch 2; in 2c I **appended** a
  "Superseded" section only — the rest of the file is untouched)
- `docs/briefs/hy4-b2-handback-A2.md` (new, 2b)
- `docs/briefs/hy4-b2-handback-A3.md` (new, 2b)
- `docs/briefs/hy4-b2-handback-B1.md` (new, 2b)
- `docs/briefs/hy4-b2-handback-B2.md` (new, 2c — explicitly named in step 1 of
  the 2c prompt, listed here for completeness)
- `docs/briefs/hy4-b2-handback-B3.md` (new, 2c — **not** named in any task)
- `docs/briefs/hy4-b2-handback-SUMMARY.md` (this file, new in batch 2,
  rewritten from scratch in 2c)

Outside the repository (scratch, all left in place; none contains a secret):

- `/private/tmp/hy4-b2-a1/` — `controlApi.orig.ts`, `controlApi.fixed.ts`,
  `page.orig.tsx`, `synthesisPanel.fixed.tsx` (batch 2's first attempt, left
  for the reviewer; the reviewer used them for the negative control).
- `/private/tmp/hy4-b2-a2/` — `ScopeRequestEditor.orig.tsx`,
  `scopeRequestSection.orig.tsx`, `ScopeRequestEditor.mine`,
  `scopeRequestSection.mine` (2b; listed in the A2 hand-back).
- `/private/tmp/hy4-b2-a3/` — `agent_tree.mine`, `test_delegation.mine`,
  `test_agent_tree.mine`, `agent_tree.fixed_control` (2b; listed in the A3
  hand-back).
- `/private/tmp/hy4-b2-b2/` — created in 2c for the two `.mine` copies; both
  were deleted after the `cmp` proofs (`rm -f` rc=0). The directory is now
  empty.
- `/private/tmp/hy4-b2c/` — `git-status-baseline.txt`,
  `git-status-after-b2.txt`, `git-status-after-b3.txt`, `make-lint.txt`,
  `stack-tags-run.txt` and the final status snapshot. 2c's baseline was saved
  before any edit; the final snapshot adds exactly three entries relative to
  it: `hy4-b2-handback-B2.md`, `hy4-b2-handback-B3.md` and
  `hy4-draft-status-batch1.md`. The rewritten `hy4-b2-handback-SUMMARY.md` and
  the appended `hy4-b2-handback-A1.md` were already untracked in the baseline,
  so their edits do not appear as new `git status` entries.

Files named in tasks (not repeated above): `scripts/ops/test_stack_tags.py`
(one import added), `docs/briefs/hy4-draft-status-batch1.md` (new draft), plus
all A2/A3/B1 task files listed in their own hand-backs. `scripts/ops/stack_tags.py`
was temporarily mutated for negative controls and restored byte-for-byte
(`cmp` rc=0); it is not a changed file.

## Guardrails observed

$0 model spend; no paid/hosted API call, no `--include-live`, no drill that
starts a model run. No `docker build`, no `docker compose up/down/restart`, no
compose-managed container, image or volume touched (only `docker ps`, read
only). No git commit/push/reset/stash/branch change and **no `git checkout`**.
No `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `~/.harness-secrets/`,
`deploy/.env` or root `.env` read. No edits to `docs/reviews/w33-*`,
`STATUS.md`, `PROJECT-STATE.md` or any spend figure; `docs/yc/` untouched; no
person decisions taken (SLOs, on-call, pricing, counsel, publishing, security
review, credentials, the B1 ruling, the stack-tag exit-code question or the
three xfail fixes).

## For the reviewer, in order

1. Rule on the three A3 xfail findings above (bad input → wrong output), and on
   whether the trailing-slash case is a fourth.
2. Rule on B1's recommendation (Option A: refuse with 422) — it is a product
   call, and the note is Proposed with a blank owner line.
3. Re-run the two literal `python3 -m pytest ...` commands on your own shell
   (items 11 and 12, both `NEEDS-REVIEWER-RUN` here because of this host's
   default interpreter).
4. Re-run your own `make lint` (and optionally root ruff on `scripts/ops/`).
5. Check the B3 draft's eight entries and both PROJECT-STATE diff hunks before
   pasting, especially the "reviewer re-ran" numbers versus my own batch 1
   hand-back numbers, and the §4 `-` lines against the live 112-line file.
6. Decide whether `stack_tags.py` should exit non-zero when `docker ps` fails;
   exit behaviour was deliberately unchanged in B2.
7. A2's real browser focus / screen-reader check remains unverified, as does
   A1's browser-side mount. Neither needs a paid call; both need a browser.
