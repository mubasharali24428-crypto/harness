# Master prompt for hy4, batch 4: workspace-root scope, a real-boundary test for the shell gate, one decision note

Copy everything below the line into hy4 as its first message. Written 2026-11-06 against branch
`w28-s1-delegate`, HEAD `f5b298a`. Batches 1, 2 (2b, 2c) and 3 are finished and reviewer-verified.
Nothing is committed unless the reviewer says so in the first line of this message; run
`git status --short` and `git log --oneline -3` first and save both in your first hand-back.

---

# ROLE AND RULES

Same role, hard limits, repo facts and working rules as
`docs/briefs/hy4-master-prompt-batch2-2026-11-05.md`, as amended by
`docs/briefs/hy4-master-prompt-batch2b-resume-2026-11-05.md`, plus the environment facts in
`docs/briefs/hy4-master-prompt-batch3-2026-11-05.md`. Read all three in full first. In short: $0 spend, no
deploys, no `docker run`, no git writes, no secrets, no rulings that belong to a person; an `Assumptions:`
line and a numbered plan before code; smallest change; every new behaviour gets a test with a real negative
control; a rejected permission blocks only that step (mark it `NEEDS-REVIEWER-RUN`, carry on); negative
controls never overwrite an existing file with `cp`, `mv` or a redirect (save a `.mine` copy to a NEW path,
mutate and restore with your edit tool, prove with `cmp`).

Environment facts that still hold:

* Your default `python3` may have no pytest. Use the section venv: `cd "<repo>/section4" && .venv/bin/python -m pytest ...`
  and `section5/.venv/bin/python` for section 5. Add `--basetemp="$(mktemp -d /private/tmp/hy4.XXXX)"` if pytest
  complains about its temp root.
* `make lint` needs `RUFF_NO_CACHE=true` only if ruff's cache rename is blocked.
* zsh: no `timeout`; `sed -i ''`; unquoted `$var` is not word-split.
* Leave no stray files. List every `/private/tmp/hy4*` path you create in your hand-back. Do not delete
  anything you did not create. A blocked delete is not a failure: list the path.

## New rules for this batch (these are about safety, read them twice)

1. **Never touch real private files.** Do not read, list, open or print anything under the real home
   directory (`~`, `$HOME`, `/Users/<name>`), and do not point any command at it, even to "see whether it is
   blocked". All read tests use a canary: a directory you create with `mktemp -d /private/tmp/hy4-canary.XXXX`
   (outside the test workspace) holding `plain-notes.txt` whose content is a random marker such as
   `CANARY-<uuid4>`. If a test needs `HOME`, set `HOME` to the canary directory for that test only, and
   restore it afterwards. If you ever find you need a real home path, stop that step and write `BLOCKED:`.
   **Name the canary file `plain-notes.txt`, not `secret.txt`.** The policy refuses any command token whose
   name matches `SECRET_PATTERNS` (`policy.py:210`, applied at `policy.py:374` through `is_secret_file`:
   `.env*`, `*.pem`, `*.key`, `*id_rsa*`, `*secret*`, `*token*`, `*credential*`, ...). A canary called `secret.txt`
   would be refused by its NAME before confinement is ever reached, and the test would prove nothing about reads.
   The canary directory name must not match those patterns either.
2. **Findings stay private.** Security findings go only into your hand-backs, `hy4-b4-handback-SUMMARY.md` and the
   one decision note named in D3. Not into `STATUS.md`, `PROJECT-STATE.md`, commit text, PR text, test
   docstrings beyond the one-line xfail reason, or any public-facing file. The harness is a private product.
3. **Tests and docs only, except one narrow production edit.** The only production file you may change is
   `section4/src/harness_orchestrator/agent_tree.py`, function `_covered`, and only in D1. Do not change
   anything under `section5/src/`, `section5/**/confinement*`, `policy.py`, `shell_gate.py`, the JS plugin or
   Landlock/Seatbelt profiles, even temporarily, except for the `.mine`/mutate/restore negative controls that
   are named below (each proven restored with `cmp`).
4. **No fixing what you are asked to report.** A case where a human reading "may this command touch a file it
   should not?" would say deny but the gate allows it is a fail-open. Keep it as `xfail(strict=True)` with
   the exact command and the wrong output, list it under "Security findings", and move on.
5. **Real execution only inside a throw-away workspace.** The new D2 tests run real commands under the real
   gate, but only inside a workspace you create with `tmp_path` or `mktemp -d`, with the canary beside it.
   If the confinement backend is not available in your environment (the existing `requires_confinement` skip
   fires), the tests will skip: say so, mark them `NEEDS-REVIEWER-RUN` with the exact command and expected
   result, and do not weaken the skip or try to work around it.

# WHAT THE REVIEWER FOUND IN BATCH 3 (so you do not redo or contradict it)

The reviewer re-ran everything on a real checkout. Results, so you can cite them as "reviewer re-ran":

| Item | Reviewer result |
|---|---|
| C1 suites (`test_delegation.py`, `test_agent_tree.py`, `test_loop_guard.py`) | 159 passed |
| C1 own differential, 1,600 pairs, 40 spellings | 0 lost coverage; 99 gained pairs, all equivalent spellings; multi-prefix lists consistent |
| C1 mutation A (leading `./` stripping disabled) | 5 failed, 112 passed; restored, `cmp` clean |
| C1 mutation B (the "batch 2 answer is never taken away" branch deleted) | **0 failed, 117 passed. This is a test gap**, see D1 step 1 |
| C2 (`test_shell_gate_scope_edges.py` + `test_shell_gate.py`) | 44 passed, 4 xfailed; `shell_gate.py` has no tracked diff |
| `make lint`, `make static-gates` | rc=0 both |
| `test_program_docs.py` | 22 passed |
| `.env.example` diff | comment lines only (0 non-comment changed lines) |
| `STATUS.md`, `PROJECT-STATE.md` | untouched |

Three things the reviewer found that change the plan:

* **A new defect in `_covered` (D1).** The workspace-root scope `.` (also `./`) is accepted by every validator
  (`collab.py:115-130` rejects only empty, over-long, NUL, backslash, leading `/` and `..`). The shell gate
  treats `.` as the whole workspace (`shell_gate.py` `_scope_hit` resolves `os.path.join(root, ".")` to the root;
  `scope_roots` returns `None` when `real == root`). But `_covered('src', ['.'])` is `False` while
  `_covered('./src', ['.'])` is `True`, and the consequences are:
  `_overlaps(['.'], ['src'])` returns `[]` (the synthesis conflict list misses a whole-workspace child vs a `src` child) and
  `_exceeds(child {'paths': ['src']}, ceiling {'paths': ['.']})` returns `['paths']` (a legitimate narrower
  child of a whole-workspace parent is refused). The reviewer's probe:
  `cd section4 && PYTHONPATH=src .venv/bin/python -c "from harness_orchestrator.agent_tree import _covered; print(_covered('src', ['.']), _covered('./src', ['.']))"`
  prints `False True`.
* **C2's four "findings" are mis-explained, and the real picture is different (D2, D3).** The gate runs the
  command as `/bin/sh -c <command>` (`shell_gate.py` `run`, the `confinement.confine_argv(["/bin/sh", "-c", command], ...)`
  call). So `~`, `$HOME`, globs and `--flag=path` ARE interpreted at run time; the argument scan merely does
  not see them. The reviewer then ran the real gate, unstubbed, in a throw-away workspace with scope `["src"]`:
  * a Python one-liner writing to `docs/new.txt` (inside the workspace, outside scope): exit 1, file NOT created;
  * a Python one-liner writing to `../escape.txt`: exit 1, file NOT created;
  * `sort --output=docs/out.txt ...` and `sort -o ...`: refused by policy, `COMMAND_REFUSED`;
  * `ls ..`: **allowed, exit 0, and it listed the parent directory of the workspace**;
  * `cat ~/<a name that does not exist>`: **allowed to reach the real home path** (the error was "No such file
    or directory", not "Operation not permitted"); a listing of a real subdirectory of the home directory also
    succeeded. Secret-looking NAMES are refused by design (`SECRET_PATTERNS`, `policy.py:210`, applied to command
    tokens at `policy.py:374`; and the word `aws` is caught by `BLOCKED_PATTERNS`, `policy.py:154`), but that is
    a filename filter, not a read boundary: a file whose name matches no pattern is readable wherever it is.
  So: **the OS write boundary holds (this is the M03 design and it works), but reads outside the workspace are
  not confined on macOS.** The Seatbelt profile contains a global `(allow file-read*)`
  (`confinement.py:172`), while the Linux Landlock path is a deny-by-default read allow-list (`_LINUX_READ_ROOTS`,
  `confinement.py:256`, with the S-01 comment above it). The deployed worker is Linux (see
  `docs/briefs/evidence/s12/part52.md` live-run section), so the exposure is the macOS native / local runner path,
  not the deployed container. That is a hypothesis you will confirm in D3 by reading `local_runner.py` and
  `byo_runner.py`; do not take it from here as fact.
* **Two small inaccuracies in your batch 3 hand-backs.** C1 says "0 absolute paths newly covered", but
  `_covered('/src', ['//src'])` is newly `True` (two spellings of the same absolute path; harmless because
  absolute scope paths are rejected at write time, but the claim should be exact). C2 findings 2 and 3 say the
  token is "not expanded"; it is not expanded by the argument scan but it IS expanded by `sh -c`. D5 corrects both.

# TASKS, IN THIS ORDER

## D1. Workspace-root scope `.`: pin the guard, characterise, then a narrow fix

Production edit allowed: `_covered` in `section4/src/harness_orchestrator/agent_tree.py` (about line 204), nothing else.

**Step 1: pin the "never lose coverage" guard before changing anything.**
* Add tests to `section4/tests/unit/test_agent_tree.py` asserting `_covered(path, [prefix])` is `True` for these
  eight pairs (`path`, `prefix`): (`./src`, `.`), (`./src`, `./`), (`./src/`, `.`), (`./src/`, `./`),
  (`./src/x`, `.`), (`./src/x`, `./`), (`./x`, `.`), (`./x`, `./`). These are the pairs where the old
  one-liner answered `True` and a noise-only comparison would answer `False`; the reviewer found these eight
  among 18 spellings. Add any further pair you can find with a quick search over a wider set of spellings.
* Prove the tests have teeth against **today's code**: save `agent_tree.py` to a new `.mine` path, delete the
  early-return block (the `if path == q or path.startswith(q.rstrip("/") + "/"): return True` lines inside the
  `for q in prefixes` loop) with your edit tool, run the new tests, capture the failures, restore with the edit
  tool, `cmp` clean. At least those eight pairs must fail. If they do not, your tests are wrong; fix the
  tests, not the control.

**Step 2: characterise `.`-style scopes (no production change yet).** One table-driven test in
`test_agent_tree.py`, one row per case, `_covered(path, [prefix])`, with prefixes `.`, `./`, `.//`, `./.` crossed with
paths `src`, `./src`, `src/x`, `docs`, `src2`, `.`, `./`, the empty string, `/etc/passwd`, `..`, `src/..`, `../src`.
Expected values must come from the gate's meaning, not from today's output:
* any non-empty relative path with no `..` component is inside the whole workspace, so it must be covered;
* the path `.` or `./` itself is covered by a `.`-style prefix;
* the empty string, an absolute path and any path with a `..` component are **not** covered by a `.`-style prefix;
* the path `.` under prefix `src` stays `False` (a whole-workspace child is broader than a `src` parent).
Rows where today's answer disagrees with that meaning are `xfail(strict=True)` with a one-line reason naming
the pair. Also add `_overlaps` and `_exceeds` rows: `_overlaps(['.'], ['src'])`, `_overlaps(['src'], ['.'])`,
`_overlaps(['./'], ['src/x'])`, and `_exceeds` for a `src` child under a `.` ceiling and under a `./` ceiling. Read
`_overlaps` (`delegation.py` about line 166) first so the expected return value follows its existing rule
(which side's paths it returns); state that rule in the hand-back.

**Step 3: the narrow fix.** In `_covered`, treat a prefix whose noise-cleaned form is exactly `.` as covering
every path whose noise-cleaned form is non-empty, not absolute, and has no `..` component. Nothing else
changes: `src2` still not under `src`, `..` and absolute paths never covered by a prefix they were not
covered by before, no `realpath`/`normpath`/`resolve`, no symlink handling. Flip the xfails you wrote into
plain passing tests (remove the marker).

**Step 4: report whether the early-return branch is now redundant.** Re-run mutation B from step 1 against the
fixed code. If the suite still passes, say so in the hand-back as a reviewer decision ("the early branch no longer
changes any answer in tests; delete?"). **Do not delete it.** If something fails, say which test.

**Step 5: differential.** Run an old-vs-new differential over at least 40 spellings squared (include `.`, `./`,
`.//`, `./.`, the empty string, absolute paths, `..` forms, `src`, `./src`, `src/`, `src2`, `a//b`, `~`) and list **every**
gained pair in the hand-back, grouped by reason. Lost coverage must be 0. Every gained pair must be either an
equivalent-spelling pair (batch 3) or "a `.`-style prefix covering a relative path without `..`". Anything else is a
bug: fix the code, not the report.

* **Accept:** the full agent-tree, delegation and loop-guard suites green
  (`cd section4 && .venv/bin/python -m pytest tests/unit/test_delegation.py tests/unit/test_agent_tree.py tests/unit/test_loop_guard.py -q`),
  `make lint`, `make static-gates` rc=0, the step 1 control failing before the fix, a second negative control
  against the **fix** (remove only the new `.` rule; the flipped tests must fail again), and every changed assertion
  explained.
* **Out of scope:** validators, `_overlaps` return format, the B1 delegated-scope recommendation, any change to
  which paths a run may have.

## D2. Real-boundary tests for the shell gate (test-only)

New file: `section5/tests/unit/test_shell_gate_real_boundary.py`. It runs `shell_gate.run(...)` for real (no
stubbed `Popen`, `confine_argv` or `signal`) in a throw-away workspace. Read first: `shell_gate.py` (`_scope_hit`,
`scope_roots`, `run`), `confinement.py` (the Seatbelt profile builder around lines 126-180 and the Landlock read
allow-list around lines 226-362), `policy.py` (`ALLOWLIST_BINARIES` line 87, `GIT_ALLOWED_SUBCOMMANDS` line 122,
`BLOCKED_PATTERNS` line 142, `validate_command` about 312-380, `sanitize_environment`), and the existing
`requires_confinement` skip in `section5/tests/unit/test_shell_gate.py:14`; reuse that skip, do not invent a new one.
Also read `section5/tests/unit/test_shell_gate_scope_edges.py` (your C2 file) so the new file does not duplicate it.

**Fixture.** Workspace: `src/x.txt`, `docs/y.txt`, `src2/x.txt`. Canary directory beside it (never inside),
`plain-notes.txt` containing the random marker (see new rule 1 for why not `secret.txt`). Scope `["src"]` unless a
row says otherwise. For each row assert what
was observed **by looking at the filesystem and the output**, not only the gate's reply: was a file created, did
the marker appear in the output, what were `denied`, `code` and `exitCode`.

**Group W (writes).** Every one of these should leave no file behind outside `src`:
* W1 write inside scope is allowed: `python3 -c "open('src/new.txt','w').write('ok')"` creates `src/new.txt`.
* W2 write to `docs/new.txt` through code: not created.
* W3 write to `../escape.txt` through code: not created.
* W4 write to a file in the canary directory through code (absolute path): not created.
* W5 shell redirect to `docs/y.txt`: refused by policy (assert the code).
* W6 an allow-listed git subcommand with an output option, for example `git diff --output=docs/out.txt` if the
  subcommand is in `GIT_ALLOWED_SUBCOMMANDS` (initialise a throw-away repo inside the workspace with
  `git init` in the fixture; if `git` is not allowed or not present say so): not created, or refused.
* W7 scope path that does not exist yet: `scopePaths=["newdir"]`, a one-liner that creates `newdir/f.txt`.
  Characterise what really happens (this is the real-behaviour half of
  `docs/decisions/2026-11-05-delegated-scope-existing-paths.md`; cite it in the reason).

**Group R (reads).** Human expectation: a scoped run should not read outside the workspace, nor in-workspace
files outside scope. Assert the marker is **not** in the output and `denied` is true; where today's answer
differs, `xfail(strict=True)` with the exact command and the wrong output:
* R1 `cat docs/y.txt` denied with `OUT_OF_SCOPE` (should already pass; it pins the arg-level scan).
* R2 `cat <CANARY>/plain-notes.txt` by absolute path.
* R3 `ls <CANARY>`.
* R4 `ls ..`.
* R5 `cat ../<canary directory name>/plain-notes.txt` by relative path.
* R6 `cat ~/plain-notes.txt` with `HOME` set to the canary directory for this test (first confirm in
  `policy.sanitize_environment` whether `HOME` survives into the child; if it does not, say so and drop this row
  rather than touching the real home).
* R7 `cat $HOME/plain-notes.txt`, same `HOME` rule.
* R8 the same marker in a second canary file whose NAME matches `SECRET_PATTERNS` (for example `id_rsa_canary`):
  `cat <CANARY>/id_rsa_canary` should be refused by the name filter. This row passes today and exists to show which
  layer stops what: R2 vs R8 differ only in the file name.
* Platform: make the read rows `pytest.param(..., marks=pytest.mark.xfail(sys.platform == "darwin", strict=True, reason=...))`
  so a Linux run (Landlock) asserts the denial for real and a macOS run records the gap. If the Landlock backend
  is not usable in your environment the rows skip: `NEEDS-REVIEWER-RUN`.

**Group G (argument and expansion forms inside the workspace).** The argument scan looks at tokens that exist as
paths; the shell then expands the rest. Measure, for real, with scope `["src"]`, whether the content of
`docs/y.txt` (put a marker in it) leaks:
* G1 `cat *` (glob, no slash)
* G2 `cat d?cs/y.txt` (glob in a directory component)
* G3 `cat docs/y.tx[t]`
* G4 `cat ./*`
* G5 `cat docs/*`
* G6 `grep -r <marker>` with no path operand, and `grep -rn <marker> *`
* G7 every allow-listed binary in `ALLOWLIST_BINARIES` that has a documented option taking a file path to read
  (for example `grep --file=docs/y.txt src/x.txt`, `grep -f docs/y.txt src/x.txt`, `sed -f`, `awk -f`,
  `--exclude-from=`). Read the allow-list first and keep the table to at most 15 rows; each row cites the
  `policy.py` line where the binary is allow-listed. A row is a fail-open if the marker from `docs/y.txt` shows
  up in the output.

For every row also record whether the answer is a policy refusal, an arg-level denial, an OS-level failure, or an allow,
so the hand-back table can say *which layer* stopped it.

**Rule 4 applies**: do not fix the gate. Any fail-open is a strict xfail plus a "Security findings" entry with
the exact command, the wrong output and the layer that should have stopped it. A harmless over-denial is a
fail-closed note, not a finding.

* **Accept:** the new file passes (xfails count as pass) with `cd section5 && .venv/bin/python -m pytest tests/unit/test_shell_gate_real_boundary.py tests/unit/test_shell_gate_scope_edges.py tests/unit/test_shell_gate.py -q`;
  every xfail has a one-line reason naming the input; `make lint` rc=0. **Negative control:** save
  `shell_gate.py` to a new `.mine` path, with your edit tool change `write_roots=scope_roots(cwd, req.get("scopePaths") or [])`
  in `run` to `write_roots=None`, run the Group W rows (W2, W3, W4 must now fail because the files get created),
  capture the failures, restore with the edit tool, `cmp` clean and `git status --short -- section5/src` empty.
  If sandbox execution is refused in your environment, record the control as `NEEDS-REVIEWER-RUN`.
* **Out of scope:** changing any file under `section5/src/`; the JS plugin; Landlock/Seatbelt profiles; Docker.

## D3. Decision note: read confinement outside the workspace (docs only)

Write `docs/decisions/2026-11-06-read-confinement-outside-workspace.md`. Status **Proposed**, blank `Owner:` line.
This is a design note, no code. Every claim about current behaviour is cited to a file and line.

* **Context.** What D2 measured (cite your own D2 results, and the reviewer's table above as "reviewer measurement"),
  the Seatbelt `(allow file-read*)` line, the Linux `_LINUX_READ_ROOTS` allow-list and its S-01 comment, the
  `shell_gate.py` comment that says confinement "already stops writes" outside the workspace (true for writes only),
  where `forbiddenPaths` comes from and who sets it (`section4/src/harness_orchestrator/confine.py` about line 120 and its
  callers), and which runners use which backend: read `section5/src/harness_sandbox/local_runner.py`,
  `byo_runner.py`, `docker_runner.py` and `confinement.py` (backend selection) and state, with citations,
  **where the macOS Seatbelt profile is actually used** and where the Linux read allow-list is. If the hypothesis
  "the deployed worker is Linux, so this is a native-runner exposure" turns out false, say so loudly.
* **Options**, each with what it costs and which D2 rows it would turn green:
  A. a default `forbiddenPaths` list of well-known secret locations for the native runner, supplied by the caller;
  B. a Seatbelt read allow-list that mirrors the Landlock one (system roots plus the workspace and the declared
     tool paths), deny-by-default for everything else;
  C. accept and document: the native runner is a "trusted workspace" mode, untrusted runs must use the container,
     and the UI/docs say so;
  D. extend the argument scan to `~`, `$VAR` and globs (name it and explain why it is weak: `sh -c` has more
     expansion forms than any scan will list).
* **Recommendation (hy4)**, one paragraph, clearly marked as yours, not a ruling. The decision is the owner's.
* **What would change**: the exact D2 xfails that would flip, and which existing docs would need a sentence.
* **Do not** put this note's content anywhere else (see new rule 2).

## D4. Draft STATUS entries for batches 2 and 3 (draft only, nothing applied)

Write `docs/briefs/hy4-draft-status-batch2-3.md` in the same shape as `docs/briefs/hy4-draft-status-batch1.md`: one
STATUS-style entry per task of batch 2 (A1 to B3) and batch 3 (C1 to C3), plus a diff-style proposal for
`PROJECT-STATE.md` sections 2 and 4. **Do not edit `STATUS.md` or `PROJECT-STATE.md`.** Use only facts from the
hand-backs, files you read, and the reviewer re-run results above and in the earlier prompts, each marked
"reviewer re-ran". Keep "uncommitted in the working tree" and "deployed" apart in every entry (nothing from
batches 1 to 3 is deployed). For C2 write exactly one vague line ("shell-gate scope edge tests added; four edge
cases recorded as strict expected failures; details in the private hand-back") and nothing about what the edge
cases are. Check with `cd section4 && .venv/bin/python -m pytest tests/unit/test_program_docs.py -q`.

## D5. Corrections to your batch 3 hand-backs (append only)

* Append a section "Superseded" at the end of `docs/briefs/hy4-b3-handback-C2.md`: state that findings 2 and 3
  (`$HOME`, `~`) were explained wrongly (the argument scan skips them, `sh -c` expands them), that the four cases
  are better described as "reads outside the workspace are not confined on macOS" plus "the argument scan sees only
  tokens that exist as paths", that the write boundary was shown to hold (reviewer measurement above), and point to D2/D3.
* Append a section "Superseded" at the end of `docs/briefs/hy4-b3-handback-C1.md`: (a) the exact statement about
  newly covered absolute paths (`/src` under `//src` is newly covered and harmless because absolute scope paths are
  rejected at write time); (b) the early-return branch was not pinned by any test (reviewer mutation B: 117 passed)
  and D1 step 1 fixes that; (c) the `.` defect, pointing to D1.
* Do not edit anything else in those two files.

## D6. Summary and stop

`docs/briefs/hy4-b4-handback-SUMMARY.md`: task table (D1 to D5) with status and the one thing you are least sure
of; a "Security findings" section listing every strict xfail from D2 (command, wrong output, layer that should have
stopped it) or "none"; every `NEEDS-REVIEWER-RUN` item; every file changed outside the tasks; the `/private/tmp/hy4*`
paths you left. End your final message with the decisions now waiting for the reviewer. Do not start new work.

Hand-backs go to `docs/briefs/hy4-b4-handback-<id>.md` (D1, D2, D3, D4, D5 each get one; D3's hand-back may be
three lines pointing at the note) in the standard batch 2 format: Assumptions, files changed, commands run with
exit codes, negative control with `cmp` proof, **what is NOT verified**, unrelated findings (one line each), risks the
reviewer should check first, scratch files.

# WHAT THE REVIEWER KEEPS

Live model runs and spend, deploys, commits, applying any draft to `STATUS.md` or `PROJECT-STATE.md`, the W33
documents, and these rulings: the B1 delegated-scope recommendation, the `permissions` note, the
`process.reallyExit` question, the denial-knob `0`/empty/`off` semantics, the `stack_tags` exit code, whether to
delete the early-return branch in `_covered`, whether and how to change anything under `section5/src/` after you
report fail-opens, and which D3 option to choose. Also removing the old `/private/tmp/hy4*` scratch directories.

# STOP CONDITIONS

A rejected permission blocks only that step: mark it `NEEDS-REVIEWER-RUN` and carry on. Stop and write `BLOCKED:`
if a step needs a paid call, a deploy, a secret, a real private file, or a person's decision, or if you find
changes in files you did not touch that are not listed in the `git status` from your first hand-back.
