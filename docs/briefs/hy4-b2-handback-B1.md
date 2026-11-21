# B1 — Design note: delegated scope needs paths that exist (decision input only)

## Task
B1 — Design note: delegated scope needs paths that exist (decision input only).

## Status
DONE

## Assumptions
The live W28 finding (drill FAIL) is that the engine binds only scope paths that already exist in the workspace, so a delegated child whose scope paths do not exist at launch can write nothing (fail-closed). The task is a decision input only — no code changed, no drill run.

## Files changed
- `docs/decisions/2026-11-05-delegated-scope-existing-paths.md` (new) — the design note.

No code changed. No production file touched.

## Commands run

```
cd "/Users/user/untitled folder 2/harness/section4" && \
  .venv/bin/python -m pytest tests/unit/test_program_docs.py -q \
  --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"
→ 22 passed in 8.86s   (rc=0)
```

## Proof the new tests fail without the change
N/A — B1 is a design note, not a code change. The docs test (`test_program_docs.py`) validates that markdown links in `docs/briefs` resolve, the capability matrix is honest, and `PROJECT-STATE.md` stays short; it does not assert the presence of any specific decision note. The note's internal links (`roadmap-3.0.md`, `2026-11-05-intent-permissions.md`, `2026-11-05-w28-child-run-approval.md`) all resolve (the test passed).

## What is NOT verified
- No drill was run (out of scope). The drill evidence cited (`scope-enforcement-drill-empty-workspace-FAIL.json`) is the reviewer's live run, read as-is.
- The recommendation is Proposed; no owner has ruled. The decision owner line is blank on purpose.
- Whether the existence check belongs in `_norm_scope` (shared with spawn) or only in the delegation endpoint is left open; both are cited.

## Unrelated findings
- `_norm_scope` (`agent_tree.py:179-201`) accepts `./src` (the `paths` clean branch uses `rx=None`, and `:185-186` does not reject a leading `./`). This is the same shape the A3 characterisation xfailed (`_covered` does not normalise `./`). Noted here because the design note's "what happens today" cites `_norm_scope` and the A3 finding is consistent with it.

## Risks the reviewer should check first
- The note's recommendation (Option A: refuse with 422) is a product call, not an engineer's call. If the owner wants a child to scope a not-yet-existing path (e.g. `src/new_module/`), Option A blocks that and the note says so — flagging it because it is the most likely objection.
- The note cites `service.py:1617-1624` and `service.py:1620` for the `shell_policy` construction; the line numbers are read at HEAD `f5b298a` and may shift if the file is edited before the owner rules.

## Scratch copies
None. No file was mutated for B1.
