# Master prompt for Qwen, batch 8: coding, bug-hunt and reasoning tasks (graded by the reviewer; no production change)

Paste everything below the line into the Qwen worker (label `qwen-b8`) as its first message. Written 2026-11-07. This batch is
harder than batches 2 to 7: it asks for working code and for judgement, and the reviewer will run the results against checks you
cannot see. Do not look for those checks. Do not read anything under `/private/tmp/reviewer-only/`.

**Time:** expect 90 to 120 minutes. Do not stop a task early. If you finish a task and think it is perfect, spend the remaining
effort trying to break your own work (more edge cases, a second reading of the spec), not adding scope.

---

# FIRST LINE OF YOUR FIRST REPLY

State which model you are, the version, and your context window, as you know it. If you are not sure, say "not sure". The reviewer
treats this as unverified either way.

# ROLE AND RULES

$0 spend. No deploys, no docker, no git writes (no commit, push, reset, stash, branch, checkout, `worktree add`). Read-only git is
fine. No secrets, no `.env` file, no real home paths, no real private files. No edits to any file inside the repository or the
snapshot: your code goes only under `/private/tmp/qwen-b8/out/<task>/`. Hand-back files go to
`/Users/user/untitled folder 2/harness-wb-qwen/docs/briefs/` named `day1-qwen-b8-<task>.md` (check `test -e` first; never overwrite).

Every claim you make comes from a command you ran, with its output pasted. A claim you did not run is written `NOT VERIFIED`.
Each task starts with an `Assumptions:` line and a numbered plan (`1. step -> verify: check`). Hand-backs are at most 150 lines.

You may use only the Python standard library plus pytest for tests. Python 3.13 (`python3`). No network.

# START STATE

```
REPO="/Users/user/untitled folder 2/harness"
SNAP="/private/tmp/qwen-b8/snap"
IN="/private/tmp/qwen-b8/inputs"
OUT="/private/tmp/qwen-b8/out"
rm -rf "$SNAP" && mkdir -p "$SNAP" "$OUT" && git -C "$REPO" archive 7c1acf4 | tar -x -C "$SNAP"
git -C "$REPO" cat-file -t 7c1acf4          # must print: commit
ls "$IN"                                     # must list: c1 f1 f2
```

If `cat-file` does not print `commit`, or `$IN` is missing, write `BLOCKED:` and stop.

# TASKS (order is by difficulty; do them in this order)

## F2. Calibrated quiz (about 15 min)

`$IN/f2/quiz.md` has 10 questions. Answer each using the snapshot only. For each: your answer, a confidence from 0 to 100, and the
command you ran. Some questions cannot be answered from the snapshot; if so, say "cannot be determined from the snapshot" and why.
Being confidently wrong is worse than saying you cannot tell. Write `day1-qwen-b8-F2.md`.

## C1. Find and fix bugs in a script (about 30 min)

`$IN/c1/check_doc_paths.py` is a copy of the doc-path checker (its docstring is its specification) with **at least three** bugs
planted in it. Find as many as you can. Do not edit the file in place.

1. Copy it to `$OUT/c1/check_doc_paths.py` and fix the bugs in the copy.
2. For every bug: the line, what is wrong, what the docstring says it should do, and a **failing test written first** that proves
   the bug on the original file and passes on your fixed copy. Put the tests in `$OUT/c1/test_regressions.py`. The test file must
   run against whichever script path is in the environment variable `CDP_PATH` (load it with `importlib`), so it can be pointed at
   the original and at your fix.
3. Prove both runs: `CDP_PATH=$IN/c1/check_doc_paths.py python3 -m pytest $OUT/c1/test_regressions.py -q` (must fail),
   and with `CDP_PATH=$OUT/c1/check_doc_paths.py` (must pass). Paste both summary lines.
4. Do not change behaviour the docstring does not mention. A fix that changes anything else counts against you.

`day1-qwen-b8-C1.md`: bugs found (line, wrong behaviour, fix), tests per bug, and any behaviour you think is a bug but the docstring
does not settle (list it; do not fix it).

## C2. Implement a citation resolver from a spec (about 30 min)

Write `$OUT/c2/resolve_cites.py` with one public function, and tests in `$OUT/c2/test_resolve_cites.py` (at least 12 tests; the
test file must run against the path in the environment variable `RC_PATH`).

```
resolve_citation(cite: str, tracked: list[str], line_counts: dict[str, int]) -> dict
```

Returns a dict with exactly the keys `status`, `path`, `candidates`, `line`.

* `cite` has the form `path:line`. Split at the LAST colon. If there is no colon, or the part after it is not all digits, the status is
  `"malformed"` (`path` None, `candidates` [], `line` None).
* If the path part contains `...` or `…` (one character), the status is `"skipped"` (`path` None, `candidates` [], `line` = the integer).
* A line below 1 is `"malformed"` (`line` None). This check comes after the `...` check.
* A leading `./` on the path part is removed before matching.
* Matching: if the path part is exactly an entry of `tracked`, that is the only candidate (an exact match wins even when other tracked
  paths end with it). Otherwise the candidates are the tracked entries that end with `"/" + path` (a match must start at a path-component
  boundary: `b/y.py` matches `a/b/y.py`; `ab/y.py` does not match it; a substring that is not a whole component never matches).
  Candidates are sorted alphabetically.
* No candidates: `"missing"`, `path` None. More than one: `"ambiguous"`, `path` None, `candidates` sorted. Exactly one: `path` is that
  entry, `candidates` is the one-element list, and the status is `"past_eof"` if `line > line_counts[path]`, otherwise `"ok"`.
  (`line_counts` has every tracked path. The last line itself is `"ok"`.)
* In every case except `"malformed"`, `line` is the integer from the citation. `candidates` is always a list.

Read the spec twice. List in your hand-back every place the spec is ambiguous and the choice you made. `day1-qwen-b8-C2.md`.

## C3. Implement a git numstat parser from a spec (about 40 min)

Write `$OUT/c3/numstat.py` with one public function, and tests in `$OUT/c3/test_numstat.py` (at least 15 tests; the test file must
run against the path in the environment variable `NS_PATH`).

```
parse_numstat(text: str) -> dict
```

`text` is the output of `git log --numstat --format='COMMIT %H'`: a line `COMMIT <sha>` starts a commit; each following line is
`ADDED<TAB>DELETED<TAB>PATH`; blank lines are ignored.

The result is a dict with exactly these keys:
* `commits`: the number of `COMMIT` lines.
* `files`: a dict keyed by final path, each value `{"added": int, "deleted": int, "commits": int}`. `added` and `deleted` are summed
  over every line for that path; `commits` is the number of distinct commits whose lines touch that path (several lines for the same
  path in one commit count once).
* `binary`: a sorted list of final paths that appeared with `-` for both counts (a binary file counts 0 added, 0 deleted, but still
  counts toward `commits` for that path).
* `renames`: a list of `(old, new)` tuples, in the order first seen, with no duplicates.
* `total_added`, `total_deleted`: sums of all numeric counts.

Rename forms in the PATH column, with the statistics attached to the NEW path:
* `old => new` (the whole path).
* `pre{OLD => NEW}post`: old is `pre+OLD+post`, new is `pre+NEW+post`. If a side is empty, the doubled slash collapses to one, and a
  leading slash is removed: `{ => sub}/x.py` is old `x.py`, new `sub/x.py`; `a/{b => }/x.py` is old `a/b/x.py`, new `a/x.py`.

Errors: raise `ValueError` for a numstat line that does not have exactly three tab-separated fields, or whose counts are neither two
integers nor two `-`, or that appears before any `COMMIT` line. The message includes the 1-based line number. List every ambiguity
in the spec and your choice. `day1-qwen-b8-C3.md`.

## F1. Find the planted errors in a copy of a state document (about 30 min)

`$IN/f1/PROJECT-STATE.planted.md` is a copy of `docs/briefs/PROJECT-STATE.md` that has had some facts changed. The repository
snapshot at `$SNAP` is the truth, together with `$IN/f1/roadmap-3.0.md` (an unchanged copy). Find every statement in the planted copy that is
false or contradicted by the snapshot or by another part of the same document.

For each finding: the line number in the planted file, the claim, the truth, and the command that shows the truth. Do not report a
statement as wrong unless a command or another part of the documents shows it. Also list statements you checked and found true, by
line number (at least 10). End with a count of findings and a count of the true statements you checked. `day1-qwen-b8-F1.md`.

## F3. Adversarial review of one function (about 30 min)

In `$SNAP/section4/src/harness_orchestrator/agent_tree.py`, read `_covered` and `_exceeds` (about lines 204 to 240) and anything they
call. Their job: decide whether a path a child run asks for is covered by the prefixes its parent allows. Find **at least three
distinct ways** a path that is NOT inside any allowed prefix is reported covered, or a path that IS inside one is reported
not covered. For every case, write a runnable Python snippet that imports `_covered` and prints the wrong answer, run it, and paste the
output. Run it like this (the main checkout's interpreter, with the snapshot's code on the path):
`PYTHONPATH="$SNAP/contracts/src:$SNAP/section1/src:$SNAP/section4/src" "$REPO/section4/.venv/bin/python" snippet.py`.
Confirm the import came from `$SNAP` (print `agent_tree.__file__`). Rank the cases by how much each could matter, and give a one-sentence reason for each rank.
Do not change the code. A case you cannot reproduce by running it does not count. `day1-qwen-b8-F3.md`.

# HAND-BACK FORMAT (each task)

1. `## Assumptions`
2. `## Times`: start, finish (from `date`, to the second).
3. `## Commands and exit codes`
4. `## Results`: what the task requires, with pasted output.
5. `## Not verified`
6. `## Least sure of`: one sentence.

Header: `Worker: qwen-b8 · Date: 2026-11-07 · Pinned: 7c1acf4`. Then `day1-qwen-b8-SUMMARY.md`: a table with task, status
(DONE / PARTIAL / BLOCKED), minutes, and the one thing you are least sure of.

# STOP CONDITIONS

Write `BLOCKED:` at the top of the affected hand-back and move to the next task if a step needs a paid call, a deploy, a docker
command, a secret, a real private file, a person's decision, or an edit inside the repository or snapshot. If the snapshot itself
fails, stop and write the SUMMARY with `BLOCKED`.
