# Hand-back: H3
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: a049897
Status: DONE
Token use (estimate): 150000

## Pre-edit snapshot
- git status --short (first 80 lines): captured this session. 13 modified tracked files (other
  agents' uncommitted work, including `reviewer.py`, `service.py`, `signals.py` — all kept
  untouched) plus untracked briefs/plans.
- sha256 before edit:
  - `section4/src/harness_orchestrator/reviewer.py` = `2a646aa510209d4bdb5a427ee0e71327840f0ff60edb2283f9404d02d5a003b6`
  - `section4/tests/unit/test_reviewer_summary_integrity.py` = new file (did not exist)
  - sha256 of test files before my touch:
    - `section4/tests/unit/test_model_client.py` = `22cedd3ea399d9d6473d5d3f736c3d3c3ceda8f6f3921442b8709293029a438b`
    - `section4/tests/unit/test_budget.py` = `eb5da70798a3b17c73ec7bf131bc3a9e8b475c62df17e8578cb40ed09e1a04e4`

## Plan
1. Snapshot: git status + rev-parse + shasum -> verify: outputs pasted here
2. Verify premise: sed -n on reviewer.py evaluate_changes -> verify: reviewer.py:104 (pre-edit) shows `summary = result.text.strip()` in the else: of the model-router branch
3. Map callers: grep evaluate_changes and .summary -> verify: lists pasted below
4. Search for existing clamp/scrub helper -> verify: grep output pasted; choice stated
5. Write section4/tests/unit/test_reviewer_summary_integrity.py (cases a-i) -> verify: run file, red on a-f
6. Minimal change in reviewer.py else: block + module-level constant/helper -> verify: run new file green
7. Update only tests that asserted the old overwrite (summary == model text) -> verify: run those files
8. ruff check on the two files -> verify: clean, exit 0
9. Full section4 suite once -> verify: 2391 passed = H0 baseline 2379 + 12 new; 60 skipped unchanged
10. Shasum after + git diff pasted

## Premise check
- reviewer.py:60 (pre-edit) `summary = "Review passed cleanly." if passed else f"Review failed: {', '.join(issues)}"`
- reviewer.py:104-105 (pre-edit, else: after the try) `summary = result.text.strip()` / `model_used = result.model`
- CONFIRMED: a model reply such as "All tests passed, LGTM" replaced "Review failed: ..." while
  `passed` was unaffected. Premise holds.

## Step 3 — caller map (grep run this session)
`evaluate_changes`:
- section4/src/harness_orchestrator/service.py:3058 (`await asyncio.to_thread(reviewer.evaluate_changes, **review_kwargs)`)
- section4/src/harness_orchestrator/demo_replay.py:12, :94 (comment references only)
- section4/src/harness_orchestrator/reviewer.py:28 (definition)
- section4/tests/unit/test_demo_seed.py:353 (no router; deterministic path)
- section4/tests/unit/test_model_client.py:91, :103
- section4/tests/unit/test_s1_r04_prompt_framing.py:40
- section4/tests/unit/test_reviewer.py:8, :20
- section4/tests/stress/test_section4_stress.py:222-246 (no router)

`.summary` in section4/src: reads of unrelated DB/other summaries (exports.py:365/387,
service.py:1728/1733/1775/2981, execution.py:1403/1433, api.py:1281/1299, collab.py:830/1433,
verdict_post.py:76, review_summary.py:76, search.py:518/539, artifacts.py:1324,
agent_tree.py:872). No src consumer asserts the exact model text; only two tests did
(listed under "Tests that asserted the old behaviour").

## Step 4 — existing helper search
`grep -rEn "def .*(scrub|clamp|one_line|truncate)" section4/src` returned:
- collab.py:136 `_scrub(data: dict)` — dict-level secret scrub, not text-line cleanup
- runs_index.py:343 `_clamp_limit(limit: int)` — pagination clamp, not text
- redact.py:12/:39 — secret-pattern scrubbing, no whitespace/control handling
- search.py:143 `scrub_text(value, limit)` — secret redaction + hard slice; no control-char
  removal, no whitespace collapse, no "..." suffix
- confine.py:101 `scrubbed_env` — env dict, unrelated
CHOICE: none fits cases c/d/f, so I wrote a small module-level helper `_reviewer_note_suffix`
in reviewer.py instead of reusing.

## Files changed (uncommitted)
- section4/src/harness_orchestrator/reviewer.py — module-level `_REVIEWER_NOTE_MAX_CHARS`,
  `_REVIEWER_NOTE_PREFIX`, two regexes and `_reviewer_note_suffix()` helper (lines 1-27); the
  else: block after the model call now does `summary += _reviewer_note_suffix(result.text)`
  instead of overwriting. The pre-existing uncommitted I-09 strict rule hunks in the diff
  (elif unknown-tests arrow and `== "passed"`) were already in the tree before I started — NOT
  my edit, kept untouched.
- section4/tests/unit/test_reviewer_summary_integrity.py — NEW, 12 tests covering cases a-i
- section4/tests/unit/test_model_client.py — one assertion updated (old overwrite assert)
- section4/tests/unit/test_budget.py — one assertion updated (old overwrite assert)

## Tests that asserted the old behaviour (reason)
- section4/tests/unit/test_model_client.py:94 `assert findings.summary == "Looks fine."` —
  asserted summary equals the raw model text; updated to
  `"Review passed cleanly. Reviewer note: Looks fine."` (case b behaviour).
- section4/tests/unit/test_budget.py:523 `assert findings.summary == "model summary"` — same
  old overwrite assert; updated to `"Review passed cleanly. Reviewer note: model summary"`.

## Checks run
- `git rev-parse --short HEAD` - 0 - `a049897`
- `git status --short | head -80` - 0 - 13 modified tracked files listed in Pre-edit snapshot
- `shasum -a 256 <editable files>` - 0 - full hashes pasted above
- `grep -rn "evaluate_changes" section4/src section4/tests | cut -c1-160` - 0 - list above
- `grep -rn "\.summary" section4/src | cut -c1-160` - 0 - list above
- `grep -rEn "def .*(scrub|clamp|one_line|truncate)" section4/src | head` - 0 - list above
- `cd section4 && .venv/bin/python -m pytest tests/unit/test_reviewer_summary_integrity.py -q -p no:cacheprovider` - 0 - `12 passed in 0.18s` (after the change)
- `cd section4 && .venv/bin/python -m pytest tests/unit/test_model_client.py tests/unit/test_budget.py tests/unit/test_reviewer.py tests/unit/test_s1_r04_prompt_framing.py tests/unit/test_demo_seed.py -q -p no:cacheprovider` - 0 - `57 passed, 129 warnings in 3.47s`
- `cd section4 && .venv/bin/ruff check src/harness_orchestrator/reviewer.py tests/unit/test_reviewer_summary_integrity.py` - 0 - `All checks passed!`
- `cd section4 && .venv/bin/python -m pytest tests -q -p no:cacheprovider` - 0 - `2391 passed, 60 skipped, 26233 warnings in 129.15s (0:02:09)`
- shasum after:
  - `reviewer.py` = `75b12bb81c43cd557de24f82282cca6d67e680d47ff1c059425321e5ef11a56f`
  - `test_reviewer_summary_integrity.py` = `9222778d75eec5ac29bdc034f4a6508900293e6c066f0c99fa74461b45913e75`
  - `test_model_client.py` = `ab95dd06e616ef7ed13d333a5801f625e40ac06312ec7071d2962d893f8f7a61`
  - `test_budget.py` = `6b93abb9d3f5308ac67a8b1373cf157a5ef9cef2b3abcbba7c75d5e07fd53007`

### Baseline comparison
- H0 ledger (`H0-ledger-2026-11-14.md:12`): section4 = `2379 passed, 60 skipped` at `a049897`.
- Now: `2391 passed, 60 skipped`. 2379 + 12 new tests = 2391. Skips unchanged. 0 failed.

## Red runs (failures before the change)
Command: `cd section4 && .venv/bin/python -m pytest tests/unit/test_reviewer_summary_integrity.py -q -p no:cacheprovider`
Result: `6 failed, 6 passed in 0.31s` (cases a-f red; g, h and the 4 parametrized i runs already
passed, as expected — they cover behaviour that was already correct).
- test_a_failed_verdict_is_not_overwritten_by_a_sunny_model_reply - as above - `assert False = <built-in method startswith ...>('Review failed') ... where 'All tests passed, LGTM' = ...summary='All tests passed, LGTM', issues=['Automated test suite failed.']`
- test_b_passing_verdict_stays_first_sentence_with_the_note_appended - as above - `assert False where 'Nice work'.startswith('Review passed cleanly.') ... summary='Nice work'`
- test_c_note_is_bounded_at_500_chars_and_truncated_with_ellipsis - as above - `assert False ... summary='xxxxx...'` (600 x's, note = whole model text)
- test_d_note_is_one_line_with_whitespace_collapsed - as above - `assert '\n' not in 'line one\nl...bed   spaces'` (containment shown)
- test_e_empty_model_text_leaves_the_pure_deterministic_sentence - as above - `AssertionError: assert '' == 'Review passed cleanly.'`
- test_f_control_characters_are_removed_from_the_note - as above - `assert '\x00' not in 'fine\x00but...text\x7fhere'` (control chars kept)

## git diff section4/src/harness_orchestrator/reviewer.py
Full diff captured in this session (pasted in transcript and summarized here — reviewer cannot
see my transcript, so the authoritative artifact is the working-tree diff itself, rerunnable
with `git diff section4/src/harness_orchestrator/reviewer.py`). My hunks:
- `+import re` and module level: `_REVIEWER_NOTE_MAX_CHARS = 500`, `_REVIEWER_NOTE_PREFIX = " Reviewer note: "`,
  `_TABS_AND_NEWLINES = re.compile(r"[\t\n\r\v\f]+")`, `_OTHER_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")`,
  and `_reviewer_note_suffix(raw)` which collapses whitespace, rewrites control chars to spaces,
  bounds the whole suffix at 500 chars and truncates with `...`.
- else: block: `summary = result.text.strip()` -> `summary += _reviewer_note_suffix(result.text)`
The diff also shows the I-09 strict-rule hunks (`elif test_results_summary != "passed"` arrow and
`passed = ... and (test_results_summary == "passed")`) — pre-existing uncommitted work by another
agent, untouched by me.

## Checks not run
- section1 / section5 / section7 suites - not in this packet's scope (packet touches section4 only) - NEEDS-REVIEWER-RUN only if the wave master requires them; H0 baseline for those sections unchanged by this packet
- a second full section4 run at the end - not needed; Flash discipline 7 allows at most two full runs and the one full run after edits suffices - environmental
- service.py integration path of the note (signal_done -> ReviewFindings over the API) - packet restricts scope to reviewer.py + listed tests; service.py itself untouched - defect (none) but path unverified end-to-end

## Decisions I made (reviewer may overrule)
- Built a new helper in reviewer.py instead of reusing `search.scrub_text` (none of the existing
  helpers collapses whitespace/removes control chars/adds "..." truncation).
- Control characters are rewritten to a space, then whitespace collapsed, rather than deleted —
  deleting outright glues neighbouring words (`fine\x00but` -> `finebut`) and can forge
  misleading prose. The characters themselves are still gone from `summary`.
- The 500-char bound applies to the whole appended suffix (" Reviewer note: " prefix included),
  so case c's "the note is at most 500 characters" holds exactly; the clean text is therefore
  bounded at 484 chars before "..." is appended.
- `model_used = result.model` is kept exactly as before (moved above the append, no semantic change).

## Open questions
- Should the "Model call withheld" budget prefix also bound its total length? Left untouched per
  packet (case g asserts it is unchanged).

## Unrelated issues seen
- `tests/unit/test_workspace_lifecycle_s2.py` emits `PytestUnraisableExceptionWarning` from a
  `BrokenPipeError` in `/Users/user/1/lib/python3.13/concurrent/futures/thread.py` (odd path out
  of my scope) during the full suite — environmental/quarrel of the shared tree, not mine.

## Known limits of this change
- Control chars are neutralized via space-substitution + collapse, not literal deletion; a
  reviewer wanting literal deletion can flip one `sub(" ", ...)` to `sub("", ...)`.
- The note is capped at 500 chars including the prefix; step 5's "at most 500 characters" for the
  clean text is thus always satisfied but with ~16 chars of headroom unused.
- Case i proves `passed` parity for four representative inputs, not an exhaustive input space.
