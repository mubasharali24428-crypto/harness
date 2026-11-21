# Master prompt for hy4, batch 3: scope-path correctness and shell-gate edge tests

Copy everything below the line into hy4 as its first message. Written 2026-11-05 (night) against
branch `w28-s1-delegate`, HEAD `f5b298a`. Batches 1 and 2 (including 2b and 2c) are finished and
reviewer-verified. Nothing is committed unless the reviewer says so in the first line of this message;
run `git status --short` and `git log --oneline -3` first and save both in your first hand-back.

---

# ROLE AND RULES

Same role, hard limits, repo facts and working rules as
`docs/briefs/hy4-master-prompt-batch2-2026-11-05.md`, as amended by
`docs/briefs/hy4-master-prompt-batch2b-resume-2026-11-05.md`. Read both in full first. In short:
$0 spend, no deploys, no `docker run`, no git writes, no secrets, no rulings that belong to a person;
`Assumptions:` line and a numbered plan before code; smallest change; every new behaviour gets a test
with a real negative control; a rejected permission blocks only that step (mark it
`NEEDS-REVIEWER-RUN`, carry on); negative controls never overwrite an existing file with `cp`, `mv` or
a redirect (save a `.mine` copy to a new path, mutate and restore with your edit tool, prove with `cmp`).

Environment facts the last batch found:

* Your default `python3` may have no pytest. Run Python tests with the section venv:
  `cd "<repo>/section4" && .venv/bin/python -m pytest <file> -q` (and `section5/.venv/bin/python` for
  section 5), or put the venv's `bin` first on `PATH`. Add `--basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"`
  only if pytest complains about its temp root.
* `make lint` needs `RUFF_NO_CACHE=true` if ruff's cache rename is blocked.
* Leave no stray files: list the `/private/tmp/hy4-*` copies you made in your hand-back (the reviewer
  will remove the earlier ones, `hy4-b2-a1` and `hy4-b2-a2`).
* Hand-backs go to `docs/briefs/hy4-b3-handback-<id>.md` in the standard batch 2 format, plus
  `docs/briefs/hy4-b3-handback-SUMMARY.md` at the end.

# WHAT THIS BATCH IS FOR

Batch 2 characterised path matching and found three real defects (strict `xfail`s). Batch 3 decides
whether they are reachable, fixes them if so, and then applies the same test-first scrutiny to the
shell gate, which is the actual enforcement point for a run's `scopePaths`.

# TASKS

## C1. `./src` vs `src`: reachability, then a narrow fix

* **Background (verified):** `agent_tree._covered('./src', ['src'])`, `_covered('src', ['./src'])`
  and `delegation._overlaps(['./src'], ['src'])` all give the wrong answer today; they are the three
  strict `xfail` tests from batch 2 A3 (`test_delegation.py::test_overlaps_dot_slash_is_the_same_directory`,
  `test_agent_tree.py::test_covered_dot_slash_path_is_under_src`,
  `test_agent_tree.py::test_covered_dot_slash_prefix_covers_src`). A trailing-slash case
  (`src` under `["src/"]` is not covered) is characterised but not xfailed.
* **Step 1, reachability (no code change yet):** find every writer of scope paths that reach
  `_covered` / `_overlaps`: the intent PUT (`control.py`), delegation scope binding (`delegation.py`),
  the agent-tree scope checks (`agent_tree.py`), and anything that reads `scope_paths` in `confine.py` /
  `engine.py`. For each, say whether it normalises or rejects `./src`, `src/`, `a//b` and `..`
  before storing, citing file and line. Write the answer as a table in the hand-back.
* **Step 2, decide by the table:**
  * If every writer already normalises (so these inputs can never reach the matchers), change no
    production code: leave the xfails, add a comment above each pointing at the normalising line, and
    report "unreachable".
  * If any writer can store such a path, fix the matcher with the **narrowest** change: treat a
    leading `./`, a trailing `/` and doubled `/` as noise, nothing else. It must still treat `src2` as
    not under `src`, must not collapse `..` or follow symlinks, and must never make a path covered by a
    prefix it was not covered by before except in the three equivalent-path cases above. Then flip
    the three xfails into plain passing tests (remove the marker), turn the trailing-slash
    characterisation into an asserted rule, and add tests for `..`, an absolute path, `src2` vs `src`,
    and empty strings (each stays uncovered or unchanged).
* **Accept:** either "unreachable, no production change" with the table, or the narrow fix with the
  full agent-tree and delegation suites green and every changed assertion explained in the hand-back.
* **Check:** `section4/.venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py tests/unit/test_loop_guard.py -q`
  from `section4`, then `make lint` and `make static-gates`.
* **Out of scope:** the B1 delegated-scope recommendation (a product decision, not built here); any
  change to which paths a run is allowed to have.

## C2. Shell gate: scope-bypass edge tests (test-only)

* **Goal:** the shell gate (`section5/src/harness_sandbox/shell_gate.py`, `_scope_hit` and
  `scope_violation`, `scope_roots`) is what stops a run writing outside `scopePaths`. Pin how it treats
  the awkward spellings of a path, so a bypass is found by a test and not by an attacker.
* **Look at:** `shell_gate.py` (lines around 46, 60 to 120), `section5/tests/unit/test_shell_gate.py`
  and its install / byo_runner siblings for fixture style, and `docs/briefs/evidence/s12/part52.md`
  (lines about argument-level checks: it says a path built inside `python -c` is stopped only by
  confinement; do not try to change that).
* **Do:** with scope `["src"]` and a workspace cwd, add table-driven tests, one case per line, for
  both the allow and deny side: `cat docs/y.txt`, `cat ./docs/y.txt`, `cat src/../docs/y.txt`,
  `cat src/./x.txt`, `cat "docs/y.txt"`, `cat 'docs/y.txt'`, `cat docs/y.txt src/x.txt`, `echo hi > docs/y.txt`,
  `echo hi >> docs/y.txt`, `tee docs/y.txt`, `cp src/x.txt docs/`, `mv docs/y.txt src/`, `sed -i s/a/b/ docs/y.txt`,
  `cat --file=docs/y.txt`, `cat -- docs/y.txt`, `cat $HOME/x`, `cat ~/x`, `cat /etc/passwd`,
  `ls ..`, `cd .. && cat docs/y.txt`, an absolute path inside the workspace but outside `src`, an
  absolute path inside `src`, a relative path that resolves into `src` via `..`, `src2/x.txt` (must
  be denied), and an empty command. For each, assert the gate's current answer (`denied` true/false,
  and `code` when denied) after reading what the code does, not what you expect.
* **Rule 7 applies:** any case where a human reading "may this command touch a file outside `src`?"
  would say deny but the gate allows it (a fail-open) is kept as `xfail(strict=True)` with the exact
  command and the wrong output, and is listed under "Security findings" in the hand-back. Do not fix
  the gate. Do not put the findings anywhere public: only in the hand-back and the summary. A case the
  gate denies although it is harmless is a fail-closed note, not a finding.
* **Accept:** tests pass (xfails count as pass); each xfail has a one-line reason naming the input.
* **Check:** `cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate.py -q` plus the
  new file, `make lint`.
* **Out of scope:** changing `shell_gate.py`; the JS plugin; Landlock.

## C3. Say the denial-knob footgun out loud (comment only)

* **Goal:** batch 1 A4 made `HARNESS_DENIAL_RUN_LIMIT` and `HARNESS_DENIAL_WINDOW_LIMIT` follow the
  repeat-limit rule where `0` or empty or `off` **disables** the stop. A reader may take `0` to mean
  "stop at once". The reviewer has not yet ruled on the semantics.
* **Do:** edit only the comment block for those two knobs in `.env.example` so it says, in one plain
  sentence each, that `0`, empty and `off` turn the stop **off**, a positive number is the limit, and a
  non-number falls back to the default. Change no code and no value.
* **Accept:** `make static-gates` (the env-knob gate) rc=0; `git diff .env.example` shows only
  comment lines.
* **Out of scope:** changing the semantics (that stays a reviewer ruling).

# WHAT THE REVIEWER KEEPS

Same list as batch 2: live model runs and spend, deploys, commits, `STATUS.md`, the W33 documents,
and the rulings (B1 recommendation, the `permissions` note, the `process.reallyExit` question, the
denial-knob semantics, `stack_tags` exit code). Also the decision to change `shell_gate.py` after you
report any fail-open.

# FINISH

`docs/briefs/hy4-b3-handback-SUMMARY.md`: task table with status and the one thing you are least sure
of; a "Security findings" section listing every strict xfail from C2 (command, wrong output) or
"none"; every `NEEDS-REVIEWER-RUN` item; every file changed outside the tasks; the `/private/tmp/hy4-*`
files you left. End your final message with the decisions now waiting for the reviewer. Do not start new
work.
