# hy4-b4-handback-D5

Task: D5 — Corrections to the batch 3 hand-backs (append only)
Status: NOT STARTED — batch BLOCKED at D2 (see `hy4-b4-handback-D2.md`).

Assumptions: (as printed at the start of the batch; D5 was never reached).

Files changed: none. No `Superseded` section was appended to
`docs/briefs/hy4-b3-handback-C1.md` or `docs/briefs/hy4-b3-handback-C2.md`; those files are byte
-for-byte as batch 3 left them (they were subsequently committed by someone else in `7cdd365`, but
not edited by me).

Commands run: none for D5.

Proof the new tests fail without the change: N/A — no change, append-only docs task.

What is NOT verified: everything. NEEDS-REVIEWER-RUN: append the two `Superseded` sections exactly
as the batch 4 prompt specifies:
* C2 — findings 2 and 3 (`$HOME`, `~`) were explained wrongly (the argument scan skips them,
  `sh -c` expands them); the four cases are better described as "reads outside the workspace are
  not confined on macOS" plus "the argument scan sees only tokens that exist as paths"; the write
  boundary was shown to hold (reviewer measurement); point to D2/D3.
* C1 — (a) `/src` under `//src` is newly covered and harmless because absolute scope paths are
  rejected at write time; (b) the early-return branch was not pinned by any test (reviewer
  mutation B: 117 passed) and D1 step 1 fixes that; (c) the `.` defect, pointing to D1.

Both corrections are now verifiable against D1's results, which are complete: the D1 hand-back
records the 16 guard tests, `16 failed` mutation-B control, `34 passed / 22 xfailed` before the
fix, `231 passed` after it, the 2,304-pair differential (0 lost / 148 gained, all `.`-style), and
the multi-prefix check (13,872 combinations, 0 lost). Note the D1 finding that the early-return
branch still changes 4 answers (`./..` under dot prefixes) even though mutation B leaves the suite
green — that nuance belongs in the C1 correction.

Unrelated findings (one line each): D5's append-only instruction now targets **tracked** files
(they were untracked when batch 3 wrote them); the reviewer should confirm that an append to a
committed hand-back is acceptable.

Risks the reviewer should check first: confirm append-only (do not rewrite the C1/C2 hand-backs),
and confirm the commit `7cdd365` did not alter their content before appending.

Scratch files: none created by D5. See the D1 hand-back for this session's `/private/tmp/hy4*`
inventory.
