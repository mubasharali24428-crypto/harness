# hy4-b4-handback-D3

Task: D3 — Decision note: read confinement outside the workspace (docs only)
Status: NOT STARTED — batch BLOCKED at D2 (see `hy4-b4-handback-D2.md`).

Assumptions: (as printed at the start of the batch; D3 was never reached).

Files changed: none. `docs/decisions/2026-11-06-read-confinement-outside-workspace.md` was NOT
created, so no decision-note content exists anywhere — complying with new rule 2 (findings stay
private).

Commands run: none for D3. No file under `section5/src/` was read for D3, no claim about the
Seatbelt profile, `_LINUX_READ_ROOTS`, `forbiddenPaths` or runner backends was made. In particular
the hypothesis "the deployed worker is Linux, so this is a native-runner exposure" was **neither
confirmed nor falsified** — the batch 4 prompt says to confirm it in D3 by reading
`local_runner.py` and `byo_runner.py`, and that did not happen.

Proof the new tests fail without the change: N/A — no change, docs-only task, no test.

What is NOT verified: everything. NEEDS-REVIEWER-RUN: the entire D3 (write the note with Status
Proposed, blank Owner, file:line citations for every behaviour claim, options A/B/C/D with costs
and which D2 rows each turns green, one clearly-marked hy4 recommendation, and "What would
change"). D3 also depends on D2's measurements, which do not exist yet.

Unrelated findings (one line each): none from D3; the tree drift is recorded in the D2 hand-back.

Risks the reviewer should check first: the batch 4 prompt requires D3 to read
`local_runner.py`, `byo_runner.py`, `docker_runner.py` and `confinement.py` (backend selection)
before stating where the macOS Seatbelt profile is actually used; do not let the note inherit the
prompt's hypothesis as fact. Also: `STATUS.md` / `PROJECT-STATE.md` changed mid-batch, so the
"which existing docs would need a sentence" section must be re-checked against the new HEAD.

Scratch files: none created by D3. See the D1 hand-back for this session's `/private/tmp/hy4*`
inventory.
