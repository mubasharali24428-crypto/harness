# hy4 batch 3 hand-back — C1

## Task
C1 — `./src` vs `src`: reachability, then a narrow fix.

## Status
DONE (decision: **reachable**, narrow matcher fix applied)

## Assumptions
Batches 1–2 left `f5b298a` with everything uncommitted; the three `./` defects are reachable until the writer table proves otherwise; no paid call, deploy, `docker run`, secret or git write is needed.

## Start state (saved in this first batch-3 hand-back, as required)

`git log --oneline -3`:

```
f5b298a Master prompt for hy4: zero-cost engineering tasks, reviewer keeps spend and deploys
4fd8b3a W33: same-task comparison with opencode, decision briefs for people-owned rows, SLO row corrected
9b5e353 Deploy W27 slice 3 on vw27s3; live breaker drill passes
```

Branch `w28-s1-delegate`, HEAD `f5b298a`. The first line of the batch 3 prompt does **not** say anything is committed, so nothing is.

`git status --short` before any edit (59 entries; all known batch 1 / batch 2 work, nothing reverted, reformatted or tidied):

```
 M .env.example
 M docs/eval/corpus.json
 M scripts/e2e/m07_daemon_drill.py
 M scripts/e2e/w27_denial_drill.py
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
?? docs/decisions/2026-11-05-delegated-scope-existing-paths.md
?? docs/decisions/2026-11-05-intent-permissions.md
?? docs/runbooks/redeploy-worker-and-orchestrator.md
?? docs/yc/
?? scripts/e2e/m01_native_edit_drill.py
?? scripts/ops/
?? section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.tsx
?? section7/src/components/ScopeRequestEditor.test.tsx
?? section7/src/components/ScopeRequestEditor.tsx
?? section7/src/lib/scopeRequest.ts
?? section7/tests/unit/scopeRequestEditorA11y.test.tsx
```

## Step 1 — reachability table (no code changed while building it)

Every writer of scope paths that can reach `_covered` (`agent_tree.py:204`) or
`_overlaps` (`delegation.py:166`). "verbatim" means the string is stored exactly
as supplied, with no normalisation. All line numbers are at HEAD `f5b298a`.

| Writer / reader | Where it stores or forwards | `./src` | `src/` | `a//b` | `..` |
|---|---|---|---|---|---|
| Intent PUT `_norm_patch` (`control.py:649-660`) | `collab.validate_paths(paths)` at `:659`, then `sorted(set(paths))` at `:660`; `put_intent` calls it at `:816`; INSERT `json.dumps(new["scopePaths"])` at `:770-778` | verbatim | verbatim | verbatim | rejected |
| Intent path validator (`collab.py:115-130`) | returns `paths` unchanged at `:130`; rejects empty/long/NUL/backslash at `:121-124`, leading `/` at `:125`, `".." in p.split("/")` at `:126` | accepted | accepted | accepted | 422 `BAD_PATHS` |
| Delegation scope binding (`delegation.py:138`, `:106-111`, `:161`) | `scope = _norm_scope(body.scope)` at `:138`; INSERT into `run_intent_versions` with `json.dumps(paths)` at `:109`; `paths=scope["paths"]` at `:161` — **bypasses `control.validate_paths`** | verbatim | verbatim | verbatim | rejected by `_norm_scope` |
| Agent-tree scope body `_norm_scope` (`agent_tree.py:179-201`) | `clean` at `:180-189`: `sorted({v.strip() ...})` at `:181` (strip + sort + dedupe only); rejects empty/long/NUL/backslash/leading `/` at `:185`, `".." in v.split("/")` at `:186`; `"paths": clean(b.paths, None, "paths")` at `:198` | accepted | accepted | accepted | 422 `VALIDATION_ERROR` |
| Spawn (`agent_tree.py:562`) and `_mint_child` (`:527-534`) | `scope` from `_norm_scope`; `json.dumps(scope, sort_keys=True)` at `:534` into `agent_assignments.scope`; `/synthesis` reads it back at `delegation.py:201` | verbatim | verbatim | verbatim | rejected |
| Agent-definition ceiling `allowed_paths` (`section1/src/harness_identity/agents.py:66-75`, `:139`, `:265-269`) | `_paths`: `sorted({p.strip() ...})` at `:67`, rejects empty/long/NUL/backslash/leading `/` at `:69`, `".." in p.split("/")` at `:70`; `validate_def` at `:139`; INSERT `allowed_paths` at `:265-269`; default `'[]'` in `section1/migrations/0044_agents.sql:26` | accepted | accepted | accepted | 422 `VALIDATION_ERROR` |
| Ceiling reader `_def_ceiling` (`agent_tree.py:217-220`) | `json.loads(d["allowed_paths"])` at `:219` feeds `_exceeds` `:212` → `_covered` | verbatim | verbatim | verbatim | n/a (already stored) |
| Intent reader `collab.intent_scope_paths` (`collab.py:1178-1195`) | `list(json.loads(row.scope_paths))` at `:1195`, verbatim; `service.py:1619-1621` puts it into `shell_policy["scopePaths"]` | verbatim | verbatim | verbatim | n/a |
| `engine.py` (`engine.py:165`, `:167`, `:214`, `:833`) | `:165` `scope = [str(p) ... if str(p).strip()]`; `:167` `os.path.realpath(os.path.join(root, p))` for the read-only decision; `:214` passes `list(policy["scopePaths"])` verbatim to the shell plugin; `:833` `scope_paths=policy.get("scopePaths")` | verbatim to gate; resolved for mount check | verbatim to gate | verbatim to gate | n/a |
| `confine.scope_targets` (`section4/src/harness_orchestrator/confine.py:145-172`) | reader: `rel = str(raw).strip().strip("/")` at `:162`, `os.path.realpath(os.path.join(root, rel))` at `:163`, `".." not in Path(rel).parts` at `:166`, `os.path.exists(real)` at `:167` | resolves to `root/src` | strips trailing `/` | realpath collapses `//` | rejected at `:166` |
| Section 5 gate (`section5/src/harness_sandbox/shell_gate.py:70`, `:99`) | reader: `os.path.realpath(os.path.join(root, s))` at `:70`; `str(scope).strip().strip("/")` + realpath at `:99` | resolves | strips trailing `/` | realpath collapses | n/a |

**Conclusion:** every writer rejects `..` and absolute paths, but **none normalises
or rejects `./src`, `src/` or `a//b`** — they only `strip()`, sort and dedupe.
The values are stored verbatim in `run_intent_versions.scope_paths`,
`agent_assignments.scope` and `agent_definitions.allowed_paths`, and are later
read verbatim by `intent_scope_paths`, `_def_ceiling` and `/synthesis`. The
matchers therefore see the raw spellings. **Reachable** → the fix branch applies.

## Step 2 — the narrow fix

`agent_tree._covered` now keeps the batch 2 answer (so no coverage is ever
removed) and additionally compares noise-cleaned spellings. `noise()` removes
only a leading `./` (repeated is fine), trailing `/` and doubled `/`; it keeps a
leading `/`, `..`, internal `.` and symlinks significant, and keeps a bare `./`
as its single `.` part so it cannot become the empty prefix. It never calls
`realpath`/`normpath`/`resolve`.

A differential check over 784 path/prefix pairs (28 spellings squared) found
**57 changed pairs, 0 lost coverage, 0 absolute paths newly covered, 0 `src2`
newly covered, 0 `..` newly covered**; every change is a noise-equivalent
spelling. A first draft that normalised both sides in place was **discarded**
because it lost coverage for the `./` prefix and newly covered `/etc/passwd`
under `./`; recorded here rather than hidden.

## Files changed
- `section4/src/harness_orchestrator/agent_tree.py` (edited, 20 insertions / 1 deletion) — `_covered` only.
- `section4/tests/unit/test_agent_tree.py` (edited) — one characterisation row changed, two `xfail(strict=True)` markers removed, four noise-equivalence cases, nine unchanged-edge cases, twelve differential pairs added.
- `section4/tests/unit/test_delegation.py` (edited) — one characterisation row changed, one `xfail(strict=True)` marker removed, six noise/non-noise overlap cases added.
- `docs/briefs/hy4-b3-handback-C1.md` (new) — this hand-back.

No other file changed. No new HTTP route, no change to which paths a run may have.

## Every changed assertion explained
1. `test_agent_tree.py` `trailing-slash-prefix-asymmetry` (`"src"`, `["src/"]`, expected `False`) → renamed `trailing-slash-prefix-is-equivalent`, expected `True`. A trailing `/` on a prefix is noise; `src` is covered by `src/`.
2. `test_delegation.py` `trailing-slash` (`["src/"]`, `["src"]`, expected `["src/"]`) → expected `["src", "src/"]`. Because `_covered("src", ["src/"])` is now `True`, `_overlaps` returns **both** spellings from the symmetric union; sorted order puts `src` before `src/`.
3. `test_agent_tree.py::test_covered_dot_slash_path_is_under_src` — `xfail(strict=True)` removed; `./src` under `src` is now `True`.
4. `test_agent_tree.py::test_covered_dot_slash_prefix_covers_src` — marker removed; `src` under `./src` is now `True`.
5. `test_delegation.py::test_overlaps_dot_slash_is_the_same_directory` — marker removed; `./src` vs `src` now returns a non-empty shared-path list.
6. All other batch 2 characterisation rows keep their original expected values (verified by the 159-test run). New tests assert the noise equivalences, the unchanged `..`/absolute/`src2`/empty edges, and an old-vs-new differential over twelve non-noise pairs.

## Commands run

```
cd "/Users/user/untitled folder 2/harness/section4" && .venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py tests/unit/test_loop_guard.py -q
→ 98 passed, 1 warning, 61 errors in 5.46s   (FileExistsError on the shared pytest temp root)
→ PYTEST_RC=1
```
```
cd "/Users/user/untitled folder 2/harness/section4" && BT="$(mktemp -d /private/tmp/hy4.XXXX)" && .venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py tests/unit/test_loop_guard.py -q --basetemp="$BT"
→ 159 passed, 448 warnings in 8.80s   (PYTEST_RC=0)
```
```
cd "/Users/user/untitled folder 2/harness/section4" && PYTHONPATH=src .venv/bin/python - <<'PY'   # 784-pair differential, old vs shipped _covered
→ pairs compared: 784
→ changed: 57 lost coverage: 0
→ absolute-path newly covered: []
→ src2 newly covered: []
→ .. newly covered: []
PY
```
```
cd "/Users/user/untitled folder 2/harness" && make lint
→ MAKE_LINT_RC=0  (all sections "All checks passed!"); plain form worked, no RUFF_NO_CACHE needed
```
```
cd "/Users/user/untitled folder 2/harness" && make static-gates
→ STATIC_GATES_RC=0
→ GUARD[routes] OK: 26 route module(s) checked ...
→ GUARD[isolation] OK: 282 src file(s) across 10 section(s) ...
→ docker compose -f deploy/docker-compose.yml config -q
→ TS mirror up to date.
→ orchestrator specs and UI calls match the router.
→ {"ok": true, "tasks": 41, "heldOut": 6, "implemented": 39}
→ env knob catalogue complete: 62 runtime reads, all in .env.example
```
The final post-restore re-run: `159 passed, 448 warnings in 3.60s` (rc=0), `make lint` rc=0, `make static-gates` rc=0 again.

## Proof the new tests fail without the change (real negative control)

Per the 2b amendment: the fixed file was saved to a **new** path (never an overwrite), mutated with the edit tool, restored with the edit tool, and proved with `cmp`. Two `.mine` copies exist because the first implementation was discarded and the control was re-run against the shipped one.

```
cd "/Users/user/untitled folder 2/harness" && cp section4/src/harness_orchestrator/agent_tree.py /private/tmp/hy4-b3/agent_tree.final.mine
→ (new file created); pre-control `git diff --stat` = 20 insertions / 1 deletion
```
Mutated `_covered` back to the batch 2 one-liner
`return any(path == q or path.startswith(q.rstrip("/") + "/") for q in prefixes)`, then:

```
cd section4 && .venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py -q --basetemp="$BT" -k "covered or overlaps"
→ FAILED test_delegation.py::test_overlaps_characterisation[trailing-slash]
→ FAILED test_delegation.py::test_overlaps_dot_slash_is_the_same_directory
→ FAILED test_delegation.py::test_overlaps_noise_and_non_noise_edges[doubled-slash]
→ FAILED test_agent_tree.py::test_covered_characterisation[trailing-slash-prefix-is-equivalent]
→ FAILED test_agent_tree.py::test_covered_dot_slash_path_is_under_src
→ FAILED test_agent_tree.py::test_covered_dot_slash_prefix_covers_src
→ FAILED test_agent_tree.py::test_covered_slash_noise_is_equivalent[leading-dot-slash-in-nested-path]
→ FAILED test_agent_tree.py::test_covered_slash_noise_is_equivalent[leading-dot-slash-in-nested-prefix]
→ 8 failed, 47 passed, 62 deselected, 1 warning in 1.94s   (NEG_CONTROL_RC=1)
```
Restore proof:
```
cmp section4/src/harness_orchestrator/agent_tree.py /private/tmp/hy4-b3/agent_tree.final.mine
→ (no output, CMP_RC=0)
git diff --stat -- section4/src/harness_orchestrator/agent_tree.py
→ 20 insertions(+), 1 deletion(-)   (same as before the control)
```
No mutated file remains in the tree.

## What is NOT verified
- No live stack, no delegated child, no HTTP request exercised. Reachability was established by reading the writers and their validators, not by a running request.
- The exact check command without `--basetemp` is rc=1 on this host (61 `FileExistsError` fixture errors, no assertion failures) — the documented pytest temp-root quirk. `NEEDS-REVIEWER-RUN` on a normal host: the literal command above, expected `159 passed`, rc=0.
- No full section4 suite or `make test` was run (would rewrite the tracked W21 evidence file); the permitted `git checkout` restore was not needed or used.
- The 784-pair differential is a scratch verification command, not a committed test; the committed differential test covers twelve curated non-noise pairs.

## Unrelated findings (one line each)
- `control.py._norm_patch` and `delegation._open_child_run` both write `run_intent_versions.scope_paths`, but only the former calls `collab.validate_paths`; the delegation INSERT at `delegation.py:106-111` bypasses it. Both validators accept the same noise spellings, so this is not a new hole, but it is a second writer of the same column.
- `confine.scope_targets:166` rejects `..` in `Path(rel).parts` while `engine.py:167` uses `os.path.realpath` on the raw scope path; the two readers normalise differently. Left alone (out of scope).
- `section1/.../agents.py:66-75`, `collab.py:115-130` and `agent_tree.py:180-189` are three copies of essentially the same path validator. Left alone.

## Risks the reviewer should check first
- `_covered` now returns `True` for `noise(path) == noise(q)` even when the raw strings differ; check that `/synthesis` conflict lists showing two spellings of one directory is acceptable (it lists both strings rather than collapsing them).
- The first discarded implementation is recorded above; confirm the shipped union form is the narrowest acceptable change. It never removes coverage, which is why a bare `./` prefix keeps its batch 2 behaviour.
- No decision was taken on B1's delegated-scope recommendation; that stays a product call.

## Scratch files (all under /private/tmp/hy4-*)
- `/private/tmp/hy4-b3/git-status-baseline.txt`, `git-log-baseline.txt` (start state)
- `/private/tmp/hy4-b3/agent_tree.mine` (first, discarded implementation — kept as history)
- `/private/tmp/hy4-b3/agent_tree.final.mine` (shipped implementation, used for the final `cmp`)
- `/private/tmp/hy4-b3/make-lint-c1.txt`, `static-gates-c1.txt`, `make-lint-c1-final.txt`, `static-gates-c1-final.txt`
- One `mktemp -d /private/tmp/hy4.XXXX` directory per pytest run (used as `--basetemp`), left in place.
