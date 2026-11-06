# hy4 hand-back — B1

Task: B1 — `permissions`: design note only
Status: DONE
Assumptions: `docs/decisions/` uses the `# Decision:` / `**Date:**` / `**Status:**`
  / `**Decision owner:**` header format; `test_program_docs.py` may require new
  decision docs to be registered; no implementation is allowed in this task.

## Files changed

* NEW `docs/decisions/2026-11-05-intent-permissions.md` — PROPOSED, blank
  `**Decision owner:**` line, three options (tool allow-list / network-off /
  retire the column), each with what it would change and what it costs to test,
  plus one recommendation (retire) and an explicit list of what is not decided.
* No code changed. No `docs/reviews/w33-*` file touched, no readiness row ticked,
  no matrix/state file edited.

## Every current-behaviour claim has a file:line

Verified by reading at HEAD `f5b298a` (line numbers re-checked with `nl -ba`
before writing the note):

* `control.py:40` `FIELDS = ("goal", "scopePaths", "permissions")`; `:38`
  `MAX_PERMISSIONS = 50`; `:41` `_PERM` regex; `:42` `_EMPTY`;
  `:661-666` `_norm_patch` validation/sort/dedupe + 422 `BAD_PERMISSIONS`;
  `:665` the `fs.delete` example; `:673-679` `_snap`; `:682` `_VCOLS`;
  `:712-714` `_public`; `:717` `get_intent`; `:772-778` the INSERT;
  `:784` the approval-void rule; `:786-788` `collab._invalidate`;
  `:807-809` `invalidatedApprovals` / `reviewRequired`;
  `:1037-1042` `IntentBody`; `:1117-1132` GET/PUT routes with `:1126` the patch.
* `collab.py:1178-1195` `intent_scope_paths` SELECTs only `scope_paths`.
* `service.py:1617-1624` `shell_policy` = `forbiddenPaths` / `scopePaths` /
  `maxToolCalls`; `:1620` calls `intent_scope_paths`.
* `engine.py:185-215` `_execution_policy_patch` and its `shell` dict;
  `:198-200` `harness-web.js`; `:218-220` native tools confined only by mounts.
* `section5/src/harness_sandbox/shell_gate.py:144`, `:155` reads `scopePaths`.
* `section7/app/projects/[projectId]/runs/[runId]/intentSection.tsx:24`,
  `:34`, `:39-40` — the editable "Permissions (one per line)" textarea.
* `agent_tree.py:58` `AGENT_PERMISSIONS = ("read","comment","steer")`,
  `:170-176` `ScopeBody`, `:191-194` closed-vocabulary check, `:441`/`:773`
  enforcement — a **separate** permissions concept, called out so the two are
  not merged.
* `delegation.py:104-111` child runs insert the literal `'[]'` for permissions.
* A grep for `permissions` across `section4/src/**/*.py` found no enforcement
  read of the intent column outside `control.py` (other hits are the agent-tree
  scope, comments, readiness/artifacts routes).

## Commands run (verbatim results)

```
$ cd "/Users/user/untitled folder 2/harness" && rm -rf /tmp/b1-bt && section4/.venv/bin/python -m pytest section4/tests/unit/test_program_docs.py -q --basetemp=/tmp/b1-bt
B1_TEST_RC=0
22 passed in 12.54s
```

```
$ section4/.venv/bin/python -m pytest section4/tests/unit/test_program_docs.py -q
EXACT_RC=0
22 passed in 11.47s
```
The exact check command passes without the `--basetemp` workaround because this
test file has no `tmp_path` fixtures.

```
$ git status --short (filtered to decisions + briefs)
?? docs/briefs/evidence/pilot-prep/m01-native-edit.json
?? docs/briefs/hy4-handback-A1.md
?? docs/briefs/hy4-handback-A2.md
?? docs/briefs/hy4-handback-A3.md
?? docs/briefs/hy4-handback-A4.md
?? docs/briefs/hy4-handback-PROGRESS.md
?? docs/decisions/2026-11-05-intent-permissions.md
```
`test_program_docs.py` needs no registration for a new `docs/decisions/` file:
its link check scans `docs/briefs/**/*.md` only, and its artifact checks name
three fixed briefs. No registration was added.

## Proof the new tests fail without the change

Not applicable — B1 is a design note, no code changed and no new tests were
written. `test_program_docs.py` is a link/matrix/short-brief gate, not a test of
this note; it passed before and after. The note's own falsifiability is the
file:line citation list above, all re-checked with `nl -ba`.

## What is NOT verified

* No owner has ruled on the recommendation; the note is explicitly PROPOSED and
  the owner line is blank, as required.
* The test-cost estimates for Options A/B are engineering estimates from reading
  the code, not measured.
* No implementation, migration, or API change was made or tested (out of scope).

## Unrelated findings (one line each)

* `STATUS.md:974`/`:978` claim no intent editor exists in section7, but `intentSection.tsx` + `controlApi.ts` prove otherwise at HEAD (also noted in A3).
* `agent_tree.py`'s `AGENT_PERMISSIONS` vocabulary and the run intent's `permissions` column share a name but are unrelated — a real naming hazard worth a future cleanup.

## Risks the reviewer should check first

* The recommendation (retire) implies stopping the approval-void effect at `control.py:784` for a field that can no longer change; that is an approval-policy change and must be a person's ruling, not merged from this note.
* Option A's cost depends on the W21/M01 fact that native file tools bypass the shell gate (`engine.py:218-220`); if that ever changes, the estimate changes.
* The note cites `delegation.py:104-111` writing `'[]'`; if delegation starts writing real tokens, retirement needs a compat decision for those rows.
