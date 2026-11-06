# hy4-b4-handback-D1

Task: D1 — Workspace-root scope `.`: pin the guard, characterise, then a narrow fix
Status: DONE (D1 only). The batch is BLOCKED from D2 onward — see `hy4-b4-handback-D2.md` and the SUMMARY.

Assumptions: batches 1–3 left `f5b298a` with everything uncommitted; the only production edit
allowed is `_covered` in D1; all D2 read tests use a `/private/tmp/hy4-canary.*` canary with `HOME`
overridden per-test — no real home path is ever read; confinement availability decides whether D2's
real-boundary rows skip; no paid call, deploy, secret, git write, or person's ruling is needed.

## Start state (saved at session start; required in the first hand-back)

`git log --oneline -3`, verbatim:

```
f5b298a Master prompt for hy4: zero-cost engineering tasks, reviewer keeps spend and deploys
4fd8b3a W33: same-task comparison with opencode, decision briefs for people-owned rows, SLO row corrected
9b5e353 Deploy W27 slice 3 on vw27s3; live breaker drill passes
```

Branch `w28-s1-delegate`, HEAD `f5b298a`. `git status --short`, verbatim (66 lines):

```
 M .env.example
 M docs/eval/corpus.json
 M scripts/e2e/m07_daemon_drill.py
 M scripts/e2e/w27_denial_drill.py
 M section4/src/harness_orchestrator/agent_tree.py
 M section4/src/harness_orchestrator/loop_guard.py
 M section4/src/harness_orchestrator/service.py
 M section4/tests/unit/test_agent_tree.py
 M section4/tests/unit/test_delegation.py
 M section4/tests/unit/test_lifecycle_recovery.py
 M section4/tests/unit/test_loop_guard.py
 M section4/tests/unit/test_loop_guard_wiring.py
 M section7/app/projects/[projectId]/runs/[runId]/controlApi.ts
 M section7/app/projects/[projectId]/runs/[runId]/page.tsx
 M section7/app/projects/[projectId]/runs/[runId]/runDetailAggregate.tsx
 M section7/app/runs/runsApi.ts
 M section7/src/components/ToolActivity.tsx
 M section7/src/components/WorkingArea.tsx
 M section7/src/lib/deriveView.ts
 M section7/tests/unit/deriveView.test.ts
 M section7/tests/unit/runAggregate.test.ts
 M section7/tests/unit/runDetailPage.test.tsx
 M section7/tests/unit/toolActivity.test.tsx
 M section7/tests/unit/userStatus.test.tsx
?? docs/briefs/evidence/pilot-prep/m01-native-edit.json
?? docs/briefs/hy4-b2-handback-A1.md
?? docs/briefs/hy4-b2-handback-A2.md
?? docs/briefs/hy4-b2-handback-A3.md
?? docs/briefs/hy4-b2-handback-B1.md
?? docs/briefs/hy4-b2-handback-B2.md
?? docs/briefs/hy4-b2-handback-B3.md
?? docs/briefs/hy4-b2-handback-SUMMARY.md
?? docs/briefs/hy4-b3-handback-C1.md
?? docs/briefs/hy4-b3-handback-C2.md
?? docs/briefs/hy4-b3-handback-C3.md
?? docs/briefs/hy4-b3-handback-SUMMARY.md
?? docs/briefs/hy4-draft-status-batch1.md
?? docs/briefs/hy4-handback-A1.md
?? docs/briefs/hy4-handback-A2.md
?? docs/briefs/hy4-handback-A3.md
?? docs/briefs/hy4-handback-A4.md
?? docs/briefs/hy4-handback-B1.md
?? docs/briefs/hy4-handback-B2.md
?? docs/briefs/hy4-handback-B3.md
?? docs/briefs/hy4-handback-B4.md
?? docs/briefs/hy4-handback-PROGRESS.md
?? docs/briefs/hy4-handback-SUMMARY.md
?? docs/briefs/hy4-master-prompt-batch2-2026-11-05.md
?? docs/briefs/hy4-master-prompt-batch2b-resume-2026-11-05.md
?? docs/briefs/hy4-master-prompt-batch2c-finish-2026-11-05.md
?? docs/briefs/hy4-master-prompt-batch3-2026-11-05.md
?? docs/briefs/hy4-master-prompt-batch4-2026-11-06.md
?? docs/decisions/2026-11-05-delegated-scope-existing-paths.md
?? docs/decisions/2026-11-05-intent-permissions.md
?? docs/runbooks/redeploy-worker-and-orchestrator.md
?? docs/yc/
?? scripts/e2e/m01_native_edit_drill.py
?? scripts/ops/
?? section5/tests/unit/test_shell_gate_scope_edges.py
?? section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.tsx
?? section7/src/components/ScopeRequestEditor.test.tsx
?? section7/src/components/ScopeRequestEditor.tsx
?? section7/src/lib/scopeRequest.ts
?? section7/tests/unit/scopeRequestEditorA11y.test.tsx
```

## Files changed

Edited (all within D1's allowed scope):

* `section4/src/harness_orchestrator/agent_tree.py` — **+4 lines in `_covered` only**, no other
  function touched. The whole production diff:
  ```diff
       path_key = noise(path)
  +    in_workspace = (bool(path_key) and not path_key.startswith("/")
  +                    and ".." not in path_key.split("/"))
       for q in prefixes:
           if path == q or path.startswith(q.rstrip("/") + "/"):
               return True  # the batch 2 answer is never taken away
           prefix_key = noise(q)
  +        if prefix_key == "." and in_workspace:
  +            return True  # a . scope is the whole workspace (scope_roots -> None)
           if path_key == prefix_key or path_key.startswith(prefix_key.rstrip("/") + "/"):
               return True
       return False
  ```
  No `realpath`/`normpath`/`resolve`, no symlink handling, no change to validators, `_overlaps`
  return format, or which paths a run may have.
* `section4/tests/unit/test_agent_tree.py` — +66 lines: 16 guard params, one 49-row table-driven
  dot-scope test, one 4-row `_exceeds` test.
* `section4/tests/unit/test_delegation.py` — +12 lines: one 3-row `_overlaps` test.

New: this hand-back. `git diff --stat` = 3 files, 82 insertions.

## Why the expected values are what they are

`shell_gate.scope_roots` (`shell_gate.py:94-104`) returns `None` (= whole workspace writable) when a
scope path resolves to the workspace root, which is what `.` and `./` do. Validators
(`collab.py:115-130`, `agent_tree.py:179-201`) accept `.` and `./` and reject only empty, over-long,
NUL, backslash, leading `/` and `..`. So the gate's meaning, not today's output, sets Step 2's
expectations:

* any non-empty relative path with no `..` component is inside the whole workspace → **True**;
* the path `.` or `./` itself → **True**;
* empty string, absolute path, any path with a `..` component → **False**;
* `.` under prefix `src` → **False** (a whole-workspace child is broader than a `src` parent).

`_overlaps` rule (`delegation.py:166-168`): it returns `sorted({x for x in a if _covered(x, b)} |
{y for y in b if _covered(y, a)})` — **paths from either side that fall under a path of the other
side**, i.e. a union of both lists, not just `a`'s. Hence `_overlaps(['.'], ['src'])` and
`_overlaps(['src'], ['.'])` both return `['src']` (the `src` path is covered by the `.` ceiling),
and `_overlaps(['./'], ['src/x'])` returns `['src/x']`.

`_exceeds` (`agent_tree.py:227-233`) appends `"paths"` when any child path is not covered by the
ceiling. A `src` child under a `.` or `./` ceiling is within it → `[]`.

Every changed assertion, explained:

* 16 guard params `test_covered_never_lose_batch_2_answer`: the 8 pairs the reviewer named
  (`./src`,`./src/`,`./src/x`,`./x` × prefixes `.`,`./`) plus 8 more found by an
  `old=True / noise-only=False` search (`.//`, `.///`, two-deep, sibling, `src2`, `././src` under
  `./.`, doubled slash, trailing slash). All assert `True` and pin the batch-2 early return.
* 49 rows `test_covered_dot_style_scopes`: 4 prefixes (`.`, `./`, `.//`, `./.`) × 12 paths
  (`src`, `./src`, `src/x`, `docs`, `src2`, `.`, `./`, `""`, `/etc/passwd`, `..`, `src/..`,
  `../src`) + the extra `.`-under-`src` row. 17 rows began as `xfail(strict=True)` with a one-line
  reason naming the pair, then had **only the marker removed** after the fix. Expected values are
  literal, not computed from the implementation.
* 4 rows `test_exceeds_with_workspace_root_ceiling`: 2 began as strict xfails (both `['paths']`
  wrong answers), 2 were already-passing controls (identical scopes `[]`, disjoint child
  `['paths']`).
* 3 rows `test_overlaps_workspace_root_scope`: all began as strict xfails (wrong answer `[]`).
* Total: **22 strict xfails flipped to plain passing tests**; no assertion value was weakened.

## Commands run (exit codes copied, not summarised)

```
cd "<repo>/section4" && PYTHONPATH=src .venv/bin/python <probe>   # read-only characterisation
  step1 old=True/noise_only=False guard pairs: count 46 (the 8 required ones present)
  step2 today's _covered prefix='.'     : 'src'=0 './src'=1 'src/x'=0 'docs'=0 'src2'=0 '.'=1 './'=1 ''=0 '/etc/passwd'=0 '..'=0 'src/..'=0 '../src'=0
  step2 today's _covered prefix='./'    : 'src'=0 './src'=1 'src/x'=0 'docs'=0 'src2'=0 '.'=1 './'=1 ''=0 '/etc/passwd'=0 '..'=0 'src/..'=0 '../src'=0
  step2 today's _covered prefix='.//'   : 'src'=0 './src'=1 'src/x'=0 'docs'=0 'src2'=0 '.'=1 './'=1 ''=0 '/etc/passwd'=0 '..'=0 'src/..'=0 '../src'=0
  step2 today's _covered prefix='./.'   : 'src'=0 './src'=0 'src/x'=0 'docs'=0 'src2'=0 '.'=1 './'=1 ''=0 '/etc/passwd'=0 '..'=0 'src/..'=0 '../src'=0
  extra: _covered('.', ['src']) = False
  _overlaps(['.'],['src']) = []; _overlaps(['src'],['.']) = []; _overlaps(['./'],['src/x']) = []
  _exceeds(src child, ceiling paths=['.'])  = ['paths']
  _exceeds(src child, ceiling paths=['./']) = ['paths']

pytest -k never_lose_batch_2_answer (today's code)     -> 16 passed, 77 deselected   GUARD_BASELINE_RC=0
pytest -k "dot_style_scopes or exceeds_with_workspace_root or overlaps_workspace_root" (before fix)
                                                       -> 34 passed, 133 deselected, 22 xfailed  STEP2_RC=0
fixed-matcher probe (48 dot rows)                      -> dot-scope expected-value mismatches: 0
  _covered('.', ['src']) = False
  _overlaps(['.'],['src']) = ['src']; _overlaps(['src'],['.']) = ['src']; _overlaps(['./'],['src/x']) = ['src/x']
  _exceeds(src, ['.']) = []; _exceeds(src, ['./']) = []

make lint                -> MAKE_LINT_RC=0  ("All checks passed!" for every section)
make lint (plain, no RUFF_NO_CACHE) -> PLAIN_LINT_RC=0
make static-gates        -> STATIC_GATES_RC=0
  "orchestrator specs and UI calls match the router."
  {"ok": true, "tasks": 41, "heldOut": 6, "implemented": 39}
  "env knob catalogue complete: 62 runtime reads, all in .env.example"
pytest (3 suites, --basetemp fresh) -> 231 passed, 448 warnings  PYTEST_RC=0

pytest (literal accept command, no --basetemp) -> 170 passed, 1 warning, 61 errors in 6.08s  LITERAL_PYTEST_RC=1
  FileExistsError: [Errno 17] EEXIST: mkdir '/private/var/folders/.../T/pytest-of-user'
  (the documented pytest temp-root quirk; sanctioned --basetemp form passes 231/231)
```

## Negative controls (each: new `.mine` path, edit tool only, restore with edit tool, `cmp`)

1. **Step 1 / mutation B, against today's code** — `/private/tmp/hy4-b4/agent_tree.d1-step1.mine`;
   deleted the early-return block (`if path == q or path.startswith(...): return True`).
   `pytest -k never_lose_batch_2_answer` → `16 failed, 77 deselected`, `NEG_CONTROL_RC=1`.
   All 8 reviewer-named pairs appear among the `FAILED` lines. Restored; `cmp` → `CMP_RC=0`;
   guard tests again `16 passed` `GUARD_RC=0`. This closes the reviewer's mutation-B test gap
   (batch 3: 0 failed / 117 passed).
2. **Against the fix** — `/private/tmp/hy4-b4/agent_tree.d1-fix.mine`; removed **only** the new
   `.` rule (the `in_workspace` computation and the `if prefix_key == "."` branch).
   `pytest -k "dot_style_scopes or exceeds_with_workspace_root or overlaps_workspace_root"` →
   `22 failed, 34 passed, 133 deselected`, `NEG_CONTROL_RC=1`. The 22 failures are exactly the
   flipped tests. Restored; `cmp` → `CMP_RC=0`; `git diff` shows the 4 insertions again.
3. **Step 4 / mutation B re-run against the fixed code** —
   `/private/tmp/hy4-b4/agent_tree.d1-step4.mine`; deleted the early-return block again.
   Full three suites → `231 passed, 448 warnings`, `MUTATION_B_RC=0`, `FAILED_COUNT=0`.
   Restored; `cmp` → `CMP_RC=0`; full suites again `231 passed` `PYTEST_RC=0`.

No control overwrote an existing file with `cp`/`mv`/redirect. All `.mine` copies were created at
**new** paths. Nothing was left mutated in the tree.

## Step 4 finding: is the early-return branch redundant?

Against the suite: **yes** — mutation B leaves `231 passed, 0 failed`. Reviewer decision as asked:
"the early branch no longer changes any answer in tests; delete?" I did **not** delete it.

Important qualifier the suite does not show: over the 48-spelling / 2,304-pair differential the
early-return branch still changes **4 answers**, all of them `path='./..'` under prefixes `.`, `./`,
`.//`, `.///` (`with=True`, `without=False`). `./..` contains a `..` component, so the new `.` rule
correctly excludes it, but the batch-2 early return still answers `True`. No test pins those four;
deleting the branch would silently lose four old answers and break the batch-3 "never lose
coverage" invariant. That is a reviewer decision, not a code change I made.

## Step 5 differential (old-vs-new, 48 spellings / 2,304 pairs)

Spellings include `.`, `./`, `.//`, `./.`, `.///`, `././`, `""`, `/`, `//`, `/src`, `/src/x`,
`//src`, `/etc/passwd`, `..`, `../src`, `src/..`, `src/../docs`, `./..`, `src`, `./src`, `src/`,
`src//`, `./src/`, `src2`, `src2/x`, `src/x`, `./src/x`, `src//x`, `a`, `a/b`, `a//b`, `a/b/c`,
`b`, `docs`, `docs/y.txt`, `./docs`, `src/./x`, `src with space`, `~`, `~/src`, `src/a.py`,
`././src`, `src/../src/x`, `SRC`, `src.`, `x`, `x/y`, `./x`.

```
live vs reimplementation mismatches: 0
batch2->batch3: gained 83 lost 0
batch3->D1new : gained 148 lost 0
batch2->D1new : gained 231 lost 0
```

**Every D1 gained pair** (148), grouped by reason. Group 1 = "a `.`-style prefix covering a
relative path with no `..` component"; each `(prefix, path)` below is one gained pair:

| prefix | count | paths (each paired with this prefix) |
|---|---|---|
| `.` | 23 | `src`, `src/`, `src//`, `src2`, `src2/x`, `src/x`, `src//x`, `a`, `a/b`, `a//b`, `a/b/c`, `b`, `docs`, `docs/y.txt`, `src/./x`, `src with space`, `~`, `~/src`, `src/a.py`, `SRC`, `src.`, `x`, `x/y` |
| `./` | 23 | same 23 paths as `.` |
| `.//` | 23 | same 23 paths as `.` |
| `.///` | 23 | same 23 paths as `.` |
| `./.` | 28 | the 23 above plus `./src`, `./src/`, `./src/x`, `./docs`, `./x` |
| `././` | 28 | the 23 above plus `./src`, `./src/`, `./src/x`, `./docs`, `./x` |

Group 2 ("anything else") = **0 pairs**. `gains with non-dot prefix: 0`;
`gains where path is empty/absolute/has ..: 0`; `lost: 0`. The remaining 83 pairs in
`batch2->D1new` are the batch-3 equivalent-spelling gains (including `/src` under `//src`, which
the reviewer already noted as harmless because absolute scope paths are rejected at write time).
Multi-prefix check: 13,872 `(path, 2-or-3-prefix)` combinations, `multi-prefix LOST coverage: 0`.

## What is NOT verified

* D2, D3, D4, D5, D6 were not started: the batch is BLOCKED (see D2 hand-back). No real-boundary
  test, no canary file, no decision note, no STATUS draft, no `Superseded` append.
* No Linux/Landlock run, no `make frontend`, no `npm run build`, no live model run, no deploy, no
  commit.
* The literal accept command without `--basetemp` returns rc=1 with 61 `tmp_path` setup errors
  (`FileExistsError` on the shared pytest temp root). The sanctioned `--basetemp` form passes
  231/231. **NEEDS-REVIEWER-RUN**: re-run the literal command on a normal host.
* The reviewer's own `make lint` / `make static-gates` / full section4 suite re-run.
* D1's differential is single-prefix lists `[q]` plus the 13,872 multi-prefix combinations above;
  it is not an exhaustive proof over all strings.

## Unrelated findings (one line each)

* The tree drifted mid-run: HEAD moved `f5b298a` → `0f55edb` via eight commits and an
  `origin/master` merge by another author, and `STATUS.md` / `PROJECT-STATE.md` gained content —
  see the D2 hand-back; this is the BLOCKED reason.
* `_covered('/src', ['//src'])` is newly `True` (two spellings of one absolute path) — a batch-3
  gain, not a D1 gain; harmless because absolute scope paths are rejected at write time. D5 owns
  the exact correction to the C1 hand-back.
* `~` and `~/src` are treated by `_covered` as ordinary relative strings; `sh -c` expansion is a
  separate layer (D2/D3 territory), not touched here.
* The batch-4 prompt's premise "nothing is committed unless the reviewer says so in the first line
  of this message" no longer matches the repository; D4 would have to draft against changed
  `STATUS.md` / `PROJECT-STATE.md`.

## Risks the reviewer should check first

1. **The tree drift** — eight commits and a merge appeared during this task. Confirm whether the
   new HEAD is intended before resuming; all my diffs above are now relative to `0f55edb`, not the
   `f5b298a` baseline. My three D1 files are still uncommitted and intact.
2. **The four `./..` pairs** above — deleting the early-return branch would lose them with no test
   failing. Decide the branch's fate deliberately.
3. Whether the new `.` rule's use of `noise()` (which strips leading `./`, trailing `/`, doubled
   `/`) is acceptable for a prefix spelled `.//` or `././`; the differential shows it only ever
   *adds* coverage, never removes it.
4. Re-run the literal pytest command without `--basetemp` on a normal host.

## Scratch files

All created by me this session, all left in place (no deletion attempted):

* `/private/tmp/hy4-b4/` — `git-status-baseline.txt`, `git-log-baseline.txt`,
  `git-status-after-d1.txt`, `agent_tree.d1-step1.mine`, `agent_tree.d1-fix.mine`,
  `agent_tree.d1-step4.mine`, `d1-step1-control.txt`, `d1-fix-control.txt`,
  `d1-step4-mutationB.txt`, `d1-differential.txt`, `make-lint-d1.txt`,
  `static-gates-d1.txt`, `make-lint-plain.txt`.
* `mktemp -d /private/tmp/hy4.XXXX` basetemp dirs created in this session:
  `/private/tmp/hy4.mEnR`, `hy4.xjDm`, `hy4.9wTH`, `hy4.2XF1`, `hy4.HsoE`, `hy4.drKS`,
  `hy4.RSiC`, `hy4.lFVD`, `hy4.8YdY`.
* Older `/private/tmp/hy4*` paths from batches 2/3 were not touched and are not mine to delete.
