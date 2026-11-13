# hy4 batch 8 hand-back - K4 (W28 synthesis conflict characterisation)

## Assumptions

Snapshot for every task: `7c1acf4 (HEAD -> w28-s1-delegate) D1: a workspace-root scope covers every relative in-workspace path`, created with `mkdir -p /private/tmp/hy4-b8/snap && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"`. Import-path checks passed: `harness_orchestrator` resolved to `/private/tmp/hy4-b8/snap/section4/src/harness_orchestrator/__init__.py` and `harness_contracts` to `/private/tmp/hy4-b8/snap/contracts/src/harness_contracts/__init__.py`. All runs used the main checkout's venvs, PYTHONPATH pointed at the snapshot, `-p no:cacheprovider` and a private `--basetemp` from `mktemp -d /private/tmp/hy4-b8-tmp.XXXX`. No deploys, no docker commands, no git writes, no secrets, no .env reads, no production change.

Assumptions: the rows are pinned at the `/synthesis` endpoint level (not just at `_overlaps` unit level, which `test_delegation.py` already covers); children are inserted directly as `agent_assignments` rows so each test can state an expected answer by meaning, including an empty path scope that the delegate API refuses; expected values are stated as meanings, and no xfail is declared unless the code's answer differs.

Plan:
1. Read `delegation.py` and the helpers, and write down the rule as read -> verify: each function cited with file:line below.
2. Read the existing synthesis/conflict tests and list their coverage -> verify: the list below, so the new rows do not repeat them.
3. Write a NEW test file in the snapshot and copy it to the evidence dir, not the repo tests tree -> verify: `cmp` says `EVIDENCE COPY IDENTICAL`.
4. Negative control in a `.mine` copy of `delegation.py` -> verify: conflict rows fail, `cmp` proves the restore.
5. Run `test_delegation.py` and the new file together -> verify: both pass except declared xfails (none declared).

Rule as read: two sibling children conflict when `_overlaps` returns a non-empty union of paths from either side that are covered by a path of the other side (`section4/src/harness_orchestrator/delegation.py:166-168`). "Covered" is `_covered` (`section4/src/harness_orchestrator/agent_tree.py:204-228`): exact equality, a `/`-boundary string prefix, or equality/prefix after a normalisation that removes only cosmetic noise (leading `./`, trailing `/`, doubled `/`); a `.` scope is the whole workspace and covers every relative in-workspace path; leading `/`, `..` and internal `.` stay significant. Scopes entering through the API are first sorted/deduped and validated by `_norm_scope` (`agent_tree.py:179-201`). `synthesis` (`delegation.py:171-214`) builds `children` from rows with a `child_run_id`, restricts conflicts to non-cancelled children (`delegation.py:205`), then walks `for i, a in enumerate(live): for b in live[i+1:]` (`delegation.py:207-208`) and appends `{"assignmentIds": [a.id, b.id], "paths": shared}` once for each unordered pair with the same `parentId` and a non-empty `shared` (`delegation.py:209-212`); the result is ordered by the SQL `ORDER BY created_at, id` at `delegation.py:181-182`. No merge occurs (`reviewRequired: True`, `autoMerge: False`, `delegation.py:213-214`).

Existing coverage in `section4/tests/unit/test_delegation.py` (not repeated): `:237-250` children listed with owner/state/result and review flags; `:253-264` final response; `:267-274` both children holding `default_scope()`'s identical `["src/a"]` paths produce 1 conflict with the right id set and a truthy `paths`; `:277-284` cancelled children produce no conflicts; `:287-288` no delegations gives empty children; `:291-306` spend comes from the ledger; `:309-319` stop envelope tombstone; `:327-334` child run intent bound to scope; `:337-344` empty scope refused with 422 `SCOPE_UNBINDABLE`; `:353-395` an extensive `_overlaps` unit characterisation (parent/child file, string-prefix lookalike, trailing slash, disjoint siblings, identical, both empty, one empty, duplicates, three-deep nesting, path with a space, dot-slash, doubled slash, absolute vs relative, `..`, empty-string side, and the D1 workspace-root rows).

## Times

Start 2026-11-07 07:57:03 PST (after the K3 review mtime). Finish 2026-11-07 08:06:11 PST (`date`). Duration 00:09:08.

## Commands run

```
SNAP="/private/tmp/hy4-b8/snap"; MUT="/private/tmp/hy4-b8/mutsrc"
cp -R "$SNAP/section4/src/harness_orchestrator" "$MUT/"
cp "$MUT/harness_orchestrator/delegation.py" "$MUT/harness_orchestrator/delegation.py.mine"
cd "$SNAP/section4" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -m pytest tests/unit/test_delegation_synthesis_conflicts.py -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section4" && PYTHONPATH="$MUT:..." "$REPO/section4/.venv/bin/python" -m pytest tests/unit/test_delegation_synthesis_conflicts.py -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section4" && PYTHONPATH="..." "$REPO/section4/.venv/bin/python" -m pytest tests/unit/test_delegation_synthesis_conflicts.py -q -p no:pythonpath -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section4" && PYTHONPATH="$SNAP/section4:$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -m pytest tests/unit/test_delegation_synthesis_conflicts.py -q -o "pythonpath=$MUT" -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cmp "$MUT/harness_orchestrator/delegation.py" "$MUT/harness_orchestrator/delegation.py.mine"
cmp "$SNAP/section4/src/harness_orchestrator/delegation.py" "$MUT/harness_orchestrator/delegation.py.mine"
cd "$SNAP/section4" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -m pytest tests/unit/test_delegation.py tests/unit/test_delegation_synthesis_conflicts.py -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cd "$SNAP/section4" && PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" -m pytest tests/unit/test_delegation.py -q -p no:cacheprovider --basetemp="$(mktemp -d /private/tmp/hy4-b8-tmp.XXXX)"
cp "$SNAP/section4/tests/unit/test_delegation_synthesis_conflicts.py" "$REPO/docs/briefs/evidence/rc/test_delegation_synthesis_conflicts.py"
```

Pytest summary lines:
```
new file, first version (9 rows): 9 passed, 1 warning in 0.97s
new file, expanded (16 rows): 16 passed, 1 warning in 1.21s
negative control attempt A (shadow via PYTHONPATH only): 16 passed, 1 warning in 1.07s   [did not fail - see Failures]
negative control attempt B (-p no:pythonpath): 16 passed, 1 warning in 1.13s             [did not fail - see Failures]
negative control attempt C (-o pythonpath=<shadow>, mutated _overlaps): 11 failed, 5 passed, 1 warning in 1.33s
combined test_delegation.py + new file: 59 passed, 148 warnings in 2.47s
new file only, final: 16 passed, 1 warning in 1.12s
test_delegation.py only: 43 passed, 148 warnings in 1.88s
```

## Results

Delivered: `section4/tests/unit/test_delegation_synthesis_conflicts.py` in the snapshot and a byte-identical copy at `docs/briefs/evidence/rc/test_delegation_synthesis_conflicts.py` in the main checkout (not in the repo tests tree). `cmp` -> `EVIDENCE COPY IDENTICAL`.

Six meaning rows, expanded with parametrisation: identical paths conflict (1 case); a parent directory of a sibling's path conflicts (3 cases: parent first, parent second, `.` workspace root); unrelated directories do not (3 cases: src vs tests, prefix lookalike, disjoint siblings); trailing slash / `./` / doubled slash spellings do not hide a conflict (6 cases); a child with NO paths conflicts with nothing (2 cases); synthesis reports each conflicting pair once in row order (3 overlapping siblings give `[01,02]`, `[01,03]`, `[02,03]`). No xfail was declared: the code's answer matched every stated meaning, so there is no code/meaning mismatch finding to hand back. `delegation.py` was not changed: `git status --short -- section4/src/harness_orchestrator/delegation.py` in the main checkout printed nothing, and the snapshot original is byte-identical to the `.mine` pristine copy.

Verify output pasted:
```
=== full negative-control summary ===
FAILED ...::test_identical_paths_conflict
FAILED ...::test_parent_directory_of_sibling_path_conflicts[parent-first]
FAILED ...::test_parent_directory_of_sibling_path_conflicts[parent-second]
FAILED ...::test_parent_directory_of_sibling_path_conflicts[workspace-root-parent]
FAILED ...::test_spelling_of_the_same_path_does_not_hide_a_conflict[trailing-slash]
FAILED ...::test_spelling_of_the_same_path_does_not_hide_a_conflict[trailing-slash-reversed]
FAILED ...::test_spelling_of_the_same_path_does_not_hide_a_conflict[leading-dot-slash]
FAILED ...::test_spelling_of_the_same_path_does_not_hide_a_conflict[leading-dot-slash-reversed]
FAILED ...::test_spelling_of_the_same_path_does_not_hide_a_conflict[doubled-slash]
FAILED ...::test_spelling_of_the_same_path_does_not_hide_a_conflict[two-noncanonical-spellings]
FAILED ...::test_synthesis_reports_each_conflicting_pair_once_in_row_order
11 failed, 5 passed, 1 warning in 1.33s
=== cmp output: restored shadow vs .mine pristine ===
IDENTICAL - restore proven
=== cmp output: snapshot original vs .mine pristine ===
IDENTICAL - snapshot original never changed
```
The 11 failures are exactly the rows that expect a conflict; the 5 that still pass are the rows whose meaning is "no conflict" (3 unrelated + 2 empty-path). The mutation was made in a `.mine`-derived copy under `/private/tmp/hy4-b8/mutsrc`, never in the snapshot original or the main checkout; it was reversed with the edit tool and proven with two `cmp` commands.

Note (not an xfail): the "child with NO paths" rows must be built by direct DB insert because `delegate` refuses an empty path scope with 422 `SCOPE_UNBINDABLE` (`delegation.py:143-145`). The new file therefore uses a row-insert helper rather than the delegate API.

## Failures and NEEDS-REVIEWER-RUN

Two negative-control attempts did not produce the required failure and are reported here:
- Attempt A (shadow package prepended to PYTHONPATH): `16 passed, 1 warning in 1.07s`.
- Attempt B (`-p no:pythonpath`): `16 passed, 1 warning in 1.13s`.
Cause: `section4/pyproject.toml:46` sets `pythonpath = ["src", "tests", "../contracts/src"]`; pytest's pythonpath plugin inserts `src` ahead of PYTHONPATH, so the mutated shadow module was never imported. Fixed in attempt C with `-o pythonpath=/private/tmp/hy4-b8/mutsrc` while keeping the rest on PYTHONPATH; that run failed the 11 conflict rows as required. No production file was edited to make a test pass.

NEEDS-REVIEWER-RUN: none. Every check in the task has a saved run. The one reviewer decision left open is whether the `.mine` mechanism (shadow package + `-o pythonpath`) is the intended interpretation of "never the original"; the original `delegation.py` was never touched and the main checkout was never changed.

## Security findings

One observation, not a ruling: `/synthesis` reports a child whose stored path scope is empty as conflicting with nothing, while `delegation.py:143-145` treats an intent without paths as unconfined (which is why the API refuses to mint one). Such a row can only arise from a direct DB insert in this test, so I did not declare an xfail (the task's stated meaning is "conflicts with nothing") and made no code change. Otherwise none.

## Least sure of

I am least sure of the negative-control mechanism: it depends on pytest's `pythonpath` ini option inserting `src` first, so a different pytest version or a reviewer running from another directory could import a different `delegation.py` than the one I mutated.
