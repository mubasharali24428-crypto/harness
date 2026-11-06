# hy4-b4-handback-D4

Task: D4 — Draft STATUS entries for batches 2 and 3 (draft only, nothing applied)
Status: NOT STARTED — batch BLOCKED at D2 (see `hy4-b4-handback-D2.md`).

Assumptions: (as printed at the start of the batch; D4 was never reached).

Files changed: none. `docs/briefs/hy4-draft-status-batch2-3.md` was NOT created. `STATUS.md` and
`docs/briefs/PROJECT-STATE.md` were **not edited by me**; their content changed during the run via
someone else's commit `0f55edb`, which is one of the two independent BLOCKED triggers.

Commands run: none for D4. `cd section4 && .venv/bin/python -m pytest tests/unit/test_program_docs.py -q`
was NOT run for D4. (The docs gate was run in D1's `make static-gates`, rc=0, but that is not D4's
check.)

Proof the new tests fail without the change: N/A — no change, draft-only task.

What is NOT verified: everything. NEEDS-REVIEWER-RUN: write the draft (one STATUS-style entry per
A1–B3 and C1–C3, diff-style PROJECT-STATE §2/§4 proposals, none applied, "uncommitted in the
working tree" vs "deployed" apart in every entry, each reviewer result marked "reviewer re-ran",
and exactly one vague line for C2), then run the `test_program_docs.py` check.

Two facts the resuming run must re-baseline before writing a word of the draft:

1. The batch 4 prompt requires every entry to say nothing from batches 1–3 is deployed and to keep
   work "uncommitted in the working tree". The new `STATUS.md` entry (commit `0f55edb`) says the
   opposite: "hy4 batches 1 to 4 are committed", W27 slices 1–3 are deployed, and 41 commits were
   pushed. Both cannot appear in the same draft without a person's ruling.
2. `docs/briefs/PROJECT-STATE.md` gained 16/-5 lines in that commit; any diff-style proposal for
   §2 and §4 must be taken from the **current** file, not the baseline read.

Unrelated findings (one line each): none from D4; the tree drift is recorded in the D2 hand-back.

Risks the reviewer should check first: decide whether the draft should describe the state at
`f5b298a` (as the prompt says) or the state at `0f55edb` (what the repo now says). Do not apply
anything to `STATUS.md` or `PROJECT-STATE.md` — that stays with the reviewer.

Scratch files: none created by D4. See the D1 hand-back for this session's `/private/tmp/hy4*`
inventory.
