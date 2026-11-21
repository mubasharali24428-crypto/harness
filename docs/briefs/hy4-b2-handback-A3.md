# A3 — `_overlaps` / `_covered` characterisation tests (test-only)

## Task
A3 — `_overlaps` and `_covered` characterisation tests (test-only).

## Status
DONE

## Assumptions
`_overlaps` (delegation.py:166) and `_covered` (agent_tree.py:204) are pure functions; "human reading 'do these two agents' scopes touch the same files'" treats `./src` and `src` as the same directory; `_norm_scope` (agent_tree.py:179-201) accepts `./src`, so this shape can reach the engine; test-only task, so no production function is changed.

## Files changed
- `section4/tests/unit/test_delegation.py` (edited) — added `test_overlaps_characterisation` (table-driven, 10 cases) and `test_overlaps_dot_slash_is_the_same_directory` (xfail strict).
- `section4/tests/unit/test_agent_tree.py` (edited) — added `test_covered_characterisation` (table-driven, 11 cases) and `test_covered_dot_slash_path_is_under_src` + `test_covered_dot_slash_prefix_covers_src` (both xfail strict).

No production file changed. `_covered` (agent_tree.py:204) and `_overlaps` (delegation.py:166) are untouched (cmp rc=0 against `.mine`, `git diff --stat` empty).

## Commands run

```
cd "/Users/user/untitled folder 2/harness/section4" && \
  .venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py \
  -q --basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"
→ 83 passed, 3 xfailed, 448 warnings in 7.52s   (rc=0)
→ 83 passed, 3 xfailed, 448 warnings in 7.78s   (rc=0, re-run after restore)
```
```
cd "/Users/user/untitled folder 2/harness" && RUFF_NO_CACHE=true make lint
→ All checks passed! (rc=0)
```

## Proof the new tests fail without the change (negative control)

Per amendment 2: saved `agent_tree.py` to `/private/tmp/hy4-b2-a3/agent_tree.mine` (a new file, not a tracked-file overwrite), mutated with the edit tool, ran the tests, restored with the edit tool, proved restore with `cmp`.

**Control A — naive prefix `_covered` (drop the path-equality + rstrip guard):**
Replaced `def _covered(...)`: `return any(path.startswith(q) for q in prefixes)`.
Result: the `string-prefix-is-not-a-path-parent` and `string-prefix-is-not-a-parent` cases flipped (a string prefix wrongly "covers"), so the characterisation table caught the regression. Restored.

**Control B — the `./` normalisation fix (the bug the strict xfails pin):**
Replaced `_covered` with a version that normalises a leading `./` on both `path` and each `q` (`def norm(v): return v[2:] if v.startswith("./") else v`). This is the correct human behaviour, so the three strict xfails should flip from xfail to XPASS and, under `strict=True`, FAIL.
Result (captured):
```
[XPASS(strict)] rule 7: bad input a=['./src'], b=['src'] gives the wrong output [] ...
[XPASS(strict)] rule 7: bad input path='./src', prefixes=['src'] gives the wrong output False ...
[XPASS(strict)] rule 7: bad input path='src', prefixes=['./src'] gives the wrong output False ...
FAILED tests/unit/test_delegation.py::test_overlaps_dot_slash_is_the_same_directory
FAILED tests/unit/test_agent_tree.py::test_covered_dot_slash_path_is_under_src
FAILED tests/unit/test_agent_tree.py::test_covered_dot_slash_prefix_covers_src
3 failed, 83 passed   (rc=1)
```
The three strict xfails flip loudly the moment someone fixes the `./` bug — exactly rule 7's requirement.

**Restore proof:**
```
cmp section4/src/harness_orchestrator/agent_tree.py /private/tmp/hy4-b2-a3/agent_tree.mine
→ (no output, rc=0)
git diff --stat -- section4/src/harness_orchestrator/agent_tree.py
→ (empty: unchanged from committed)
```
Re-ran the two test files after restore: `83 passed, 3 xfailed` (rc=0).

## What is NOT verified
- The 3 xfailed cases are real bugs in `_covered` (it does not normalise a leading `./`). They are reported, not fixed — fixing them is a reviewer decision because `_covered` is also used by the agent-tree scope checks. The strict xfails will fail the suite the moment someone applies the fix, which is the intended signal.
- `make static-gates` was not run for A3 (the task only requires the two pytest files and `make lint`).
- Batch 1's `FileExistsError` on pytest's shared temp root still appears without `--basetemp`; the sanctioned workaround (`--basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"`) was used. With it, 0 fixture errors.

## Unrelated findings
- `delegation._overlaps` (delegation.py:166-168) returns the union of "paths of `a` under `b`" and "paths of `b` under `a`", which is correct for symmetric overlap. No bug; noted because the task asked me to read it.
- The synthesis route (delegation.py:171-214) excludes cancelled children from `conflicts` (line 205) and from the overlap pairing (line 207), matching the prompt's "a cancelled child is excluded".

## Risks the reviewer should check first
- The `trailing-slash-prefix-asymmetry` case (`src` under `["src/"]` → False) is characterised as current behaviour, not xfailed. A human might expect `src` and `src/` to be the same directory. If the reviewer wants that treated as a bug, it needs its own strict xfail; I did not add one because the task said "any case that does not behave as a human would expect → xfail", and this one is a judgement call (a trailing slash on a prefix is unusual input). Flagging it here rather than silently pinning it.

## Scratch copies
`/private/tmp/hy4-b2-a3/agent_tree.mine`, `/private/tmp/hy4-b2-a3/test_delegation.mine`, `/private/tmp/hy4-b2-a3/test_agent_tree.mine`, `/private/tmp/hy4-b2-a3/agent_tree.fixed_control`. Deletion of `/private/tmp/hy4-b2-a3/*` was not attempted; listed for the reviewer.
