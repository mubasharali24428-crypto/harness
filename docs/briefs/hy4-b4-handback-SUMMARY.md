# hy4-b4-handback-SUMMARY — batch 4

Branch `w28-s1-delegate`. Baseline HEAD `f5b298a`; HEAD at the time of this SUMMARY `0f55edb`
(changed by someone else during the run — see below). **Overall status: BLOCKED at D2.** No new
work started after the stop. Nothing committed, deployed, paid for, or read from a real private
file by me. No git write was issued in this session.

## Task table

| Task | Status | The one thing I am least sure of |
|---|---|---|
| D1 workspace-root scope `.` | **DONE** | That the new `.` rule's `noise()`-cleaned prefix test (`prefix_key == "."`) is the right boundary for odd spellings like `.//` and `././`. The 2,304-pair differential shows it only ever adds coverage (0 lost) and every one of the 148 gains is a `.`-style prefix covering a relative path without `..`, so I believe it is narrow — but `./..` under `.`/`./`/`.//`/`.///` remains an old-answer-only case pinned by nothing. |
| D2 real-boundary shell-gate tests | **BLOCKED** (not started) | Everything. The tree drifted mid-run: eight commits plus an `origin/master` merge, and `STATUS.md` / `PROJECT-STATE.md` changed in files I did not touch and that were absent from the first hand-back's `git status`. Whether those commits were intended is a person's decision. |
| D3 read-confinement decision note | NOT STARTED (blocked at D2) | Everything, including whether the "deployed worker is Linux" hypothesis is true — the runner files were never read. |
| D4 STATUS draft batches 2–3 | NOT STARTED (blocked at D2) | Everything, plus a factual conflict: the prompt requires "nothing from batches 1–3 is deployed / uncommitted in the working tree", while the new `STATUS.md` says batches 1–4 are committed and W27 slices 1–3 deployed. |
| D5 append-only C1/C2 corrections | NOT STARTED (blocked at D2) | Everything; also whether appending to now-**tracked** hand-backs is acceptable. |

## Security findings

**None.** D2 was not started, so no strict `xfail` was written, no fail-open was observed or
recorded, and no finding text exists in any public-facing file. No `STATUS.md`,
`PROJECT-STATE.md`, commit text, PR text, or test docstring was written with finding content.
Nothing from this batch was put into `docs/decisions/` either.

Open findings from earlier batches are unchanged and still live in the private batch-3 hand-back
`docs/briefs/hy4-b3-handback-C2.md` (four strict xfails); their explanation is due a correction in
D5, which did not run. I did not re-state, re-characterise or widen them here.

## Every failure and NEEDS-REVIEWER-RUN item

* **BLOCKED (stop condition)**: tree drift — `git log --oneline -3` moved from
  `f5b298a / 4fd8b3a / 9b5e353` to `0f55edb / 277f007 / 7cdd365`; `git reflog` shows eight
  commits/merge by another author between 00:29:17 and 00:41:13; `git diff --name-only
  f5b298a..HEAD` = 69 files; `STATUS.md` (+15) and `docs/briefs/PROJECT-STATE.md` (+16/-5) were
  clean at baseline and now changed; a new `docs/briefs/2026-11-05-w28-delegation-gate.md`
  appeared; `git status --short` shrank from 66 lines to 4. Full evidence in
  `hy4-b4-handback-D2.md`.
* **BLOCKED (person's decision)**: D4's required framing contradicts the new `STATUS.md`; D5 would
  append to files that were untracked at baseline and are now tracked. Both need a ruling on which
  state to describe.
* **NEEDS-REVIEWER-RUN**: all of D2 (the new file, W/R/G groups, the `write_roots=None` negative
  control with `cmp` proof, and the section5 pytest + `make lint` checks). Exact commands are in
  the batch 4 prompt. If confinement is unavailable, the prompt requires the rows to skip rather
  than weakening the skip; I did not probe it.
* **NEEDS-REVIEWER-RUN**: all of D3, D4, D5 (see each hand-back).
* **NEEDS-REVIEWER-RUN**: the literal D1 accept command
  `cd section4 && .venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py tests/unit/test_loop_guard.py -q`
  returns rc=1 here with 61 `tmp_path` setup errors (`FileExistsError` on the shared pytest temp
  root). With the sanctioned `--basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"` it is
  `231 passed, 448 warnings`, rc=0. Re-run the literal form on a normal host.
* **NEEDS-REVIEWER-RUN**: the reviewer's own `make lint`, `make static-gates`, full section4
  suite, and a re-baselined final `git status --short` comparison.
* No rejected permission occurred. No paid call, deploy, `docker run`, secret, or real private
  file was needed or used.

## D1 results (the completed part)

* Step 1 guard: 16 tests (the reviewer's 8 pairs plus 8 found by a wider search). Mutation B
  against today's code → `16 failed`; restored with `cmp` rc=0.
* Step 2 characterisation: `34 passed, 22 xfailed` — 17 `.`-scope rows, 2 `_exceeds` rows,
  3 `_overlaps` rows, each xfail with a one-line reason naming the pair. Expected values came from
  the gate's meaning, not today's output. `_overlaps` returns paths from **either** side that fall
  under a path of the other side (`delegation.py:166-168`).
* Step 3 fix: **+4 lines in `_covered` only** — a prefix whose noise-cleaned form is `.` covers
  any path whose noise-cleaned form is non-empty, not absolute, and has no `..` component. All 22
  xfails flipped to plain passing tests by removing only the marker.
* Second negative control (remove only the new `.` rule) → `22 failed, 34 passed`; restored with
  `cmp` rc=0.
* Step 4 mutation B against the fixed code → `231 passed`, 0 failed: the early-return branch no
  longer changes any answer **in tests**. Reviewer decision: delete it? I did not delete it.
  Qualifier: over the 2,304-pair differential it still changes 4 answers (`./..` under `.`, `./`,
  `.//`, `.///`), pinned by no test; deleting the branch would silently lose those old answers.
* Step 5 differential: 48 spellings / 2,304 pairs; **0 lost coverage**; 148 gained, all in group
  "a `.`-style prefix covering a relative path without `..`"; group "anything else" = 0; gains
  with a non-dot prefix = 0; gains where the path is empty/absolute/has `..` = 0; multi-prefix
  check 13,872 combinations, 0 lost. Full grouped listing in `hy4-b4-handback-D1.md`.
* Accept checks: `231 passed` rc=0; `make lint` rc=0 (plain and with `RUFF_NO_CACHE=true`);
  `make static-gates` rc=0.

## Files changed outside the tasks

None by me. My whole diff is three D1 files plus five hand-backs:

* Edited: `section4/src/harness_orchestrator/agent_tree.py` (+4, `_covered` only),
  `section4/tests/unit/test_agent_tree.py` (+66),
  `section4/tests/unit/test_delegation.py` (+12). Total `82 insertions`, 0 deletions.
* New: `docs/briefs/hy4-b4-handback-D1.md`, `-D2.md`, `-D3.md`, `-D4.md`, `-D5.md`, and this
  SUMMARY.
* `STATUS.md`, `docs/briefs/PROJECT-STATE.md`, `section5/src/**`, `.env.example`, `docs/yc/`,
  the batch-3 hand-backs and all other files were **not touched by me**. Their committed changes
  belong to `0f55edb`/`7cdd365`/etc.

## Markdown links

These hand-backs contain no markdown links, so `test_program_docs.py` has nothing new to resolve.

## `/private/tmp/hy4*` paths I left (none deleted)

* `/private/tmp/hy4-b4/` — `git-status-baseline.txt`, `git-log-baseline.txt`,
  `git-status-after-d1.txt`, `agent_tree.d1-step1.mine`, `agent_tree.d1-fix.mine`,
  `agent_tree.d1-step4.mine`, `d1-step1-control.txt`, `d1-fix-control.txt`,
  `d1-step4-mutationB.txt`, `d1-differential.txt`, `make-lint-d1.txt`,
  `static-gates-d1.txt`, `make-lint-plain.txt`.
* `mktemp -d /private/tmp/hy4.XXXX` basetemp dirs created this session: `/private/tmp/hy4.mEnR`,
  `hy4.xjDm`, `hy4.9wTH`, `hy4.2XF1`, `hy4.HsoE`, `hy4.drKS`, `hy4.RSiC`, `hy4.lFVD`, `hy4.8YdY`.
* Older `/private/tmp/hy4*` paths from batches 2/3 exist but were not created or touched in this
  session; removing them remains with the reviewer.
