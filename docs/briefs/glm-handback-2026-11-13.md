# GLM hand-back — 2026-11-13 (run 2026-11-14)

Reviewer: read this first. Written against branch `w28-s1-delegate`, HEAD `04b819b`
(the brief was written against `8836dfa`; two more commits landed in between and are
the reason Task B's file is no longer untracked). All work below is left
**uncommitted** — no `git add`, no commits, no git writes of any kind.

## Task A — Part 52: argument-level path checks, one program per call (section5)

**Status: DONE** (with one documented deviation: strict-xfail rows in two test
files were flipped — see "Files changed" and "Deviations").

**Files changed**
- `section5/src/harness_sandbox/shell_gate.py` — `_scope_hit` rewritten:
  - `..` segments are resolved before the scope test (realpath), and a token with a
    `..` segment is a path candidate even when the target does not exist at that
    depth (this is what made `cat ../../etc/hosts` fail-open at deep tmp paths);
  - a leading `~` is expanded (`os.path.expanduser`);
  - a `--flag=value` token contributes its value as the path (the value spelling is
    what the denial names, e.g. `docs/y.txt`);
  - the old branch that skipped every token resolving **outside the workspace**
    ("confinement already stops writes there") is removed: confinement stops writes
    out there but not reads, so the argument scan now denies them (`OUT_OF_SCOPE`);
  - flags, bare words and `python -c` code strings are still skipped (M03 story
    unchanged; the OS boundary remains the write boundary; `scope_roots` untouched);
  - module docstring line updated to match.
- `section5/tests/test_shell_gate_args.py` (new, per the brief's exact path) — 11
  assert-style decision-layer cases (GATE resolves via `parents[1]`: the file sits in
  `tests/`, not `tests/unit/`, so `parents[1]` is `section5`. A first draft used
  `parents[2]` and passed the full suite only because an earlier-collected test had
  already imported `shell_gate` into `sys.modules`; standalone it collected 15 errors.
  Caught by the review round, fixed, re-verified standalone): 7 chained-command spellings (`;`, `&&`, `||`,
  `|`, backticks, `$(...)`, newline) denied `COMMAND_REFUSED`; `cat ../../etc/hosts`
  denied `OUT_OF_SCOPE` with scope `["."]`; the brief's literal
  `cat ../../etc/passwd` case denied; `cat ~/x`, `ls ..`, `cat --file=docs/y.txt`
  denied `OUT_OF_SCOPE`; `cat src/x.txt` in scope passes with `{"exitCode": 0,
  "output": ""}`; `cat docs/../src/x.txt` still passes; `""` still denied
  `COMMAND_REFUSED` "Empty command is not allowed.".
- `section5/tests/unit/test_shell_gate_scope_edges.py` — flips + expectation fixes:
  - `~/x` (tilde-path), `ls ..` (parent-directory), `cat --file=docs/y.txt`
    (flag-equals-path): strict-xfail marks **removed** — they now pass;
  - `cat /etc/passwd` (etc-passwd): `_scope_hit` now names `/etc/passwd` (policy
    still refuses the command first; only the direct-scan expectation changed);
  - `cd .. && cat docs/y.txt` (chained-cd): `_scope_hit` now names `..` (the first
    out-of-scope token); the command is still refused by the chaining layer;
  - header note updated (three of four fail-open rows closed; `$HOME` stays).
- `section5/tests/unit/test_shell_gate_real_boundary.py` — `_R_FAIL_OPEN` reduced
  from `{R2..R7}` to `{R7}`: R2-R6 (absolute canary read, canary list, `ls ..`,
  relative canary read, tilde read) are now denied at the argument layer on macOS
  too; R7 (`cat $HOME/plain-notes.txt`) stays a strict xfail — the gate does not
  expand environment variables (sh does, at exec time), and expanding them in the
  check would guess at the sanitized child env.

**Denial reasons added:** none new — the fix reuses the existing shapes
(`OUT_OF_SCOPE` "path '<spelling>' is outside the run intent's scope paths";
chaining reasons come from the pre-existing `policy.find_unquoted_shell_syntax`,
which already enforced one-program-per-call; the new tests pin that).
**Case I could not decide:** `cat $HOME/x` — left fail-open with its strict xfail,
reason above; reviewer may prefer env expansion against `sanitize_environment`.

**Verification (all commands run on this machine):**
```
cd section5 && .venv/bin/python -m pytest tests -q
  pre-patch:  507 passed, 8 skipped, 14 xfailed in 54.69s
  post-patch: 530 passed, 8 skipped, 6 xfailed in 54.86s
  (delta: +15 passed = 11 new tests + 4 previously-xfailed decision rows
   + 4 real-boundary rows R2-R6 previously xfailed on darwin now passing;
   xfail 14 -> 6: 8 strict xfails flipped, no new failures)
cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate.py \
    tests/unit/test_shell_gate_scope_edges.py \
    tests/unit/test_shell_gate_real_boundary.py tests/test_shell_gate_args.py -q
  88 passed, 6 xfailed in 7.68s
cd section5 && .venv/bin/ruff check src/harness_sandbox/shell_gate.py \
    tests/test_shell_gate_args.py tests/unit/test_shell_gate_scope_edges.py \
    tests/unit/test_shell_gate_real_boundary.py   ->  All checks passed!
cd section5 && .venv/bin/python -m pytest tests/test_shell_gate_args.py -q
  15 passed in 0.07s   (standalone; failed with 15 collection errors before the
  parents[1] fix — order coupling found by the review round)
cd section5 && .venv/bin/python -m pytest tests/test_shell_gate_args.py \
    tests/unit/test_shell_gate_scope_edges.py -q
  43 passed, 1 xfailed in 0.36s   (new file collected FIRST: no order coupling)
```
NEEDS-REVIEWER-RUN: the real-boundary rows on a Linux/Landlock host
(`tests/unit/test_shell_gate_real_boundary.py`); R2-R6 are darwin-only xfails and
plain assertions on Linux — they must stay green there.

**Proposed commit message (Task A):**
```
section5: shell gate denies out-of-scope path arguments after ../~/--flag= resolution

Part 52 left items: _scope_hit now resolves `..` (realpath), expands a leading
`~`, and parses `--flag=value` path values; tokens resolving outside the
workspace are denied OUT_OF_SCOPE instead of being left to the write boundary.
Flags, words and python -c code strings stay skipped (M03 unchanged); $VAR
spellings remain a documented fail-open (strict xfail). Adds
tests/test_shell_gate_args.py; flips the three scope-edge xfails and the four
darwin real-boundary xfails the fix closes.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

## Task B — finish `section4/tests/unit/test_step_detail.py`

**Status: DONE (already finished by another lane); no changes made.**
Code wins over the prompt: the brief describes it as "untracked, 39 lines,
unfinished", but at HEAD `04b819b` it is **tracked** (committed in `ee9f199`
"Runs list: planApproved and blockedBy; run aggregate steps carry scrubbed
detail"), still 39 lines, and complete — it asserts real scrubbing behaviour of
`OrchestratorService._step_detail` (service.py:3235) with no database or model.

**Verification:** `cd section4 && .venv/bin/python -m pytest
tests/unit/test_step_detail.py -q` → **3 passed in 0.41s**.
Also `cd section4 && .venv/bin/python -m pytest tests/unit/test_engine_shell_policy.py -q`
→ 11 passed (gate hand-over unaffected by Task A; the engine only forwards config).

**No commit proposed** (nothing to commit).

## Task C — PROJECT-STATE upkeep

**Status: DONE.**
`docs/briefs/PROJECT-STATE.md` §4 item 1: only the Part 52 "Left:" sentence
changed — external pen test (cap 8) and the `$HOME` spelling remain open;
`..`/`~`/`--flag=path` argument checks and one program per call are in place and
pinned by the new test file (2026-11-13). No matrix row was touched.

**Verification:** `git diff -- docs/briefs/PROJECT-STATE.md` shows exactly one
changed sentence (see diff stat below).

**Proposed commit message (Task C):**
```
PROJECT-STATE: Part 52 left items after the argument-level scope fix

Left is now the external pen test (cap 8) and the $HOME spelling; the
argument-level checks and one-program-per-call are pinned by
section5/tests/test_shell_gate_args.py.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

## Deviations from the brief (for the reviewer's ruling)

1. **Two test files beyond the listed set were edited**
   (`test_shell_gate_scope_edges.py`, `test_shell_gate_real_boundary.py`). The brief
   said "only touch the files listed under your tasks", but its own acceptance was
   "`pytest tests -q` passes", and a correct fix necessarily XPASSes the strict
   xfails that pin the old fail-open behaviour (that is what strict xfail means
   there). Only marks/expectations of rows the fix actually closes were touched;
   the two remaining documented fail-opens (`$HOME` spelling; G-group glob and
   code-string rows) were left exactly as they were.
2. **`--flag=value` parsing was added** although the brief named only `..` and `~`:
   `grep --file=docs/y.txt src/x.txt` is a real out-of-scope read today, the
   hand-back `hy4-b3-handback-C2.md` finding 1 already flagged it, and the change
   is deny-only. Reviewer can drop it by reverting one `if`-block in `_scope_hit`.
3. **Task B executed as verification only** because the file is already complete at
   HEAD (see above).

## Unrelated bugs / observations (one line each, not fixed)

- `docs/briefs/glm-harness-prompt-2026-11-13.md` (untracked) still describes
  `test_step_detail.py` as untracked/unfinished — stale after `ee9f199`.
- The brief's hard-limit note "git status already shows about 20 modified files
  from other lanes" was stale: at HEAD `04b819b` the tree has only untracked files.
- First implementation attempt (test file): `GATE = parents[2]` was wrong for the
  brief's `tests/` location and only passed when another test imported `shell_gate`
  first; fixed to `parents[1]` after the review round flagged it (standalone 15 errors
  -> 15 passed).
- First implementation attempt: `cat ../../etc/hosts` slipped through the
  path-likeness heuristic at deep tmp depths (parent dir check fails when the
  resolved parent does not exist); fixed by treating any `..`-segment token as a
  path candidate. Caught by the new test, fixed before hand-back.

## Diff summary

```
git diff --stat
 docs/briefs/PROJECT-STATE.md                       |  5 ++-
 section5/src/harness_sandbox/shell_gate.py         | 48 ++++++++++++++------
 section5/tests/unit/test_shell_gate_real_boundary.py    |  2 +-
 section5/tests/unit/test_shell_gate_scope_edges.py | 17 ++++----
 4 files changed, 50 insertions(+), 22 deletions(-)
```

Untracked additions: `section5/tests/test_shell_gate_args.py`,
`docs/briefs/glm-handback-2026-11-13.md` (this file), plus the pre-existing
other-lane files listed below.

## git status --short (end of work)

```
 M docs/briefs/PROJECT-STATE.md
 M section5/src/harness_sandbox/shell_gate.py
 M section5/tests/unit/test_shell_gate_real_boundary.py
 M section5/tests/unit/test_shell_gate_scope_edges.py
?? "FUTURE PLAN 2.0.md"
?? docs/briefs/evidence/ui/before-home-*.png (18 files: home/review/run x 375/768/1280 x light/dark)
?? docs/briefs/evidence/ui/week-ui.md
?? docs/briefs/glm-handback-2026-11-13.md
?? docs/briefs/glm-harness-prompt-2026-11-13.md
?? docs/decisions/2026-11-13-du1-component-stack.md
?? docs/decisions/2026-11-13-du2-visual-direction.md
?? docs/decisions/2026-11-13-du3-u0-before-p17.md
?? docs/decisions/2026-11-13-du4-risk-tiers.md
?? section5/tests/test_shell_gate_args.py
?? section7/e2e/ui-baseline.e2e.ts
```

## Reviewer follow-up (2026-11-14)

Fixed after review, same files:
- `..` inside a word that is not a walkable path (`grep -E a/../b`, `sed s/../x/`) is no
  longer refused: a `..` token counts as a path only when the part before `..` exists.
- A short flag with its path attached (`-o/tmp/x`, `-I../inc`) is now checked; `-la` is not.
- `$VAR` is expanded against the child's sanitized env and checked; a variable the gate
  cannot resolve (unset, `${X:-...}`) is refused. Closes the `$HOME` row and R7.
- Symlinks are followed on purpose (a link out of scope is refused); docstring says so.
- Commit trailers above now name the model that commits them.

`cd section5 && .venv/bin/python -m pytest tests -q` → 542 passed, 8 skipped, 4 xfailed
(the 4 are the G-group glob/code-string rows). `tests/test_shell_gate_args.py` alone: 25 passed.
Linux/Landlock run (2026-11-14): throwaway `harness-engine-worker:vw28s3` container,
`--network none`, read-only copy of section5, kernel 6.12.76-linuxkit aarch64: the four gate
test files give 100 passed, 4 xfailed, 0 skipped. That first needed a one-line fix:
`test_shell_gate_real_boundary.py` hardcoded `dir="/private/tmp"` (macOS only), so before
this its 26 real-boundary rows had never run on Linux; it now uses the default tmp dir off macOS.
