# B3 — Draft the STATUS and PROJECT-STATE text for batch 1

## Task
B3 — draft `docs/briefs/hy4-draft-status-batch1.md` from the batch 1 hand-backs, as batch 2 task B3 describes (batch 2c close-out).

## Status
DONE

## Assumptions
B2's only defect is the missing `Path` import; the compose-alignment test is otherwise correct; no paid call, deploy, secret or git write is needed. (This is the one `Assumptions:` line printed before any code was written in this batch; B3 adds only that the batch 1 hand-backs, `STATUS.md`, `docs/briefs/PROJECT-STATE.md`, `docs/roadmap-3.0.md` and the batch 2c reviewer re-runs are the complete allowed fact set.)

Shorter alternative: **no**. The draft is 296 lines because acceptance asks for eight separate STATUS-style entries (A1–A4, B1–B4), each with what was built, what was checked and its result, what is not verified, rollback, and an explicit separation of "uncommitted in the working tree" from "deployed", plus a diff-style proposal for two PROJECT-STATE sections. Dropping an entry or the per-entry state lines would violate the acceptance criterion. The `-` lines in the diff are copied verbatim from the current `PROJECT-STATE.md` §4, which accounts for much of the length.

## Files changed
- `docs/briefs/hy4-draft-status-batch1.md` (new, 296 lines) — the draft. It contains: the two-state rule; a "Reviewer re-runs" section marking every listed reviewer re-run as "reviewer re-ran" (six batch 1 checks and three batch 2 checks); eight STATUS-style entries; and two diff-style PROJECT-STATE proposals (§2 one-sentence addition, §4 item-list rewrite), both labelled NOT APPLIED.
- `docs/briefs/hy4-b2-handback-B3.md` (new) — this hand-back, required by working rule 6 (a hand-back per task). It is a file not named in any task and is listed again in the batch SUMMARY.

**Not changed:** `STATUS.md`, `docs/briefs/PROJECT-STATE.md`, the capability matrix, any spend figure, `docs/eval/corpus.json`, the W21 evidence file, and every code file. No commit, push, image build, deploy, drill or paid call.

## Commands run

```
cd "/Users/user/untitled folder 2/harness/section4" && PATH="/Users/user/untitled folder 2/harness/section4/.venv/bin:$PATH" python3 -m pytest tests/unit/test_program_docs.py -q
→ ......................                                                   [100%]
→ 22 passed in 8.12s
→ DOCS_TEST_RC=0
```
```
cd "/Users/user/untitled folder 2/harness" && git diff --stat -- STATUS.md docs/briefs/PROJECT-STATE.md
→ (no output, rc=0 — neither document was edited)
```
```
cd "/Users/user/untitled folder 2/harness" && wc -l docs/briefs/PROJECT-STATE.md docs/briefs/hy4-draft-status-batch1.md
→  112 docs/briefs/PROJECT-STATE.md
→  296 docs/briefs/hy4-draft-status-batch1.md
→  408 total
```
```
cd "/Users/user/untitled folder 2/harness" && grep -c '\[[^]]*\]([^)]*)' docs/briefs/hy4-draft-status-batch1.md
→ 0
→ GREP_COUNT_RC=1 (no matches — the draft introduces no markdown link for the docs gate to resolve)
```
```
cd "/Users/user/untitled folder 2/harness/section4" && PATH="/Users/user/untitled folder 2/harness/section4/.venv/bin:$PATH" python3 -m pytest tests/unit/test_program_docs.py -q   (re-run after this hand-back landed)
→ ......................                                                   [100%]
→ 22 passed in 7.29s
→ DOCS_TEST_RC=0
```
```
cd "/Users/user/untitled folder 2/harness" && git status --short > /private/tmp/hy4-b2c/git-status-after-b3.txt; diff /private/tmp/hy4-b2c/git-status-baseline.txt /private/tmp/hy4-b2c/git-status-after-b3.txt
→ 28a29
→ > ?? docs/briefs/hy4-b2-handback-B2.md
→ 29a31
→ > ?? docs/briefs/hy4-draft-status-batch1.md
```
Only my two new files appeared relative to the start-state baseline; no change in a file I did not touch. (The B3 hand-back itself appeared after this snapshot.)

As in B2, the literal `python3 -m pytest ...` on this host resolves to the WorkBuddy-managed 3.13.12 interpreter with no pytest; the check was run with `section4/.venv/bin` on PATH so that `python3` is the repo's own interpreter. `NEEDS-REVIEWER-RUN` on the reviewer's own shell: `cd "/Users/user/untitled folder 2/harness/section4" && python3 -m pytest tests/unit/test_program_docs.py -q`, expected `22 passed`, rc=0.

## Proof the new tests fail without the change
N/A — B3 is a document, no code changed and no new tests were written. `test_program_docs.py` is a link / short-brief / matrix gate and passed both before (B1, B4 hand-backs: 22 passed) and after this draft. The draft's own falsifiability is its sourcing: every number and command is traceable to a batch 1 hand-back, `STATUS.md`, `PROJECT-STATE.md`, the repository, or the batch 2c prompt's reviewer re-run list.

## What is NOT verified
- **Nothing in the draft is applied.** The eight STATUS entries and both PROJECT-STATE diff blocks are proposals for the reviewer to check and paste; `STATUS.md` and `PROJECT-STATE.md` have empty diffs.
- The proposed §4 rewrite grows `PROJECT-STATE.md` by about twelve lines (112 → about 124), which stays under the docs gate's 130-line limit, but that is arithmetic on the proposed diff, not a run of the gate against a modified file. I did not edit the file to test it.
- The reviewer re-run numbers (A2 30/30, A4 23 failed / 28 passed, batch 2 1083 tests, etc.) are quoted from the batch 2c prompt and marked "reviewer re-ran"; I did not re-run them and I did not reconcile them against the different numbers in my own batch 1 hand-backs (60/60 loaded runs, 5 failed / 46 passed). Both sets are left visible on purpose.
- No `make frontend`, `npm run build`, `make static-gates` or full suite was run for B3; the task's only check is `test_program_docs.py`.
- No browser/visual check, no live drill, no deploy. Nothing from batches 1 or 2 is deployed.

## Unrelated findings
- `STATUS.md:972` and `:977` contradict each other about the drills' `--image` defaults; the code agrees with `:977` (batch 1 B4 hand-back). Recorded in the B4 STATUS entry, not resolved in STATUS itself.
- `STATUS.md:974`/`:978` say no intent editor exists in section7, but `intentSection.tsx` plus `controlApi.ts` prove otherwise at HEAD (batch 1 A3 and B1 hand-backs). Recorded in the A3 entry.

## Risks the reviewer should check first
- Check each of the eight entries against its hand-back before pasting, especially the "reviewer re-ran" lines versus my own run numbers; they are deliberately separate.
- Check the §4 diff hunks against the live `PROJECT-STATE.md` before applying: the `-` lines were copied verbatim at 112 lines and will not apply cleanly if that file changes first.
- The §2 addition is one long sentence by design (the task asks for a sentence, not a paragraph); if the reviewer prefers brevity, shorten it when pasting, not by editing the draft's sources.
- The draft states plainly that nothing from batches 1 or 2 is deployed, and separately names the existing deployed image tags from `STATUS.md`. Confirm those tags are still current before pasting.

## Scratch files
None created for B3 beyond the log `/private/tmp/hy4-b2c/git-status-after-b3.txt` (listed in the batch SUMMARY).
