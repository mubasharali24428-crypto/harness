# Week 13 — P-25 stabilization: the CI race fix, suite health, ledger completeness

Opened 2026-11-21 (executed ahead of the calendar week; the plan's W13 window is
Feb 3–9, 2027). Base: branch `w28-s1-delegate`.

Plan row: P-25 (W13, S/B): "Stabilization only: flake fixes, ledger
completeness, STATUS.md refresh at final revision. Acceptance: Green gates at
final revision; STATUS.md known-limits list current." P-24 (Day-90 gate review,
P-D7 multiplayer decision) is an OWNER decision due at the actual Day-90 gate —
untouched here.

## 1. Flake fix (root cause, not a retry)

CI run 44909389775 at `cbfe4f8` failed `pytest (section11)`:
`test_w19_replica_admission.py::test_reservation_released_after_index_completes`
— "a finished index must free its durable reservation" (held 1, wanted 0).

- **Root cause**: `_run_index` flipped `job.status` to `completed` in the try
  block and committed the reservation DELETE in the `finally`, so a poller that
  observed the terminal status could still see the lease held. The test did not
  assume anything the code never promised — the CODE lied.
- **Fix** (`2ddf03b`): the durable lease release now commits BEFORE the terminal
  status is visible; a release failure leaves the job honestly `indexing`
  instead of lying `completed`. W19 admission suite 5× consecutive green locally
  (plus isolation suite), full section11 suite 60/60.

## 2. Suite health at this revision (2026-11-21, per-section venvs)

| Suite | Result |
|---|---|
| section1 (identity) | **1269 passed, 63 skipped** (after the pack + classification work) |
| section11 (context) | **60 passed** |
| section4 (orchestrator) | **2398 passed, 60 skipped** (with the denial-learning wiring in) |
| contracts | lint clean; policy_enforce classification row added for the pack route |
| Frontend/TS | `make check-ts-dist` green (26 tracked files) after the identity SDK regen |

## 3. Ledger completeness (the plan's Friday rule)

Weeks 02–08 already stood; this batch adds week-09 (P-17), week-10 (P-19),
week-11 (P-21 draft), week-12 (P-23), week-13 (this file) — every plan-week's
fleet scope now has a dated evidence entry. Owner-only rows (P-16, P-18, P-20,
P-22, P-24; the P-14 pay-intent; the P-05 SOWs; the redeploy) are recorded as
owner-gated, not silently omitted.

## 4. STATUS.md

Refreshed in the same commit with the new revision, the three shipped slices
(pack, denial-learning loop, ledger set), and the known-limits list updated:
the RC still runs pre-P-17 code until the vv90r5 redeploy; the benchmark's live
run remains preregistered-but-unspent; the pack's chain verdict is honest on
non-PostgreSQL lanes by design.
