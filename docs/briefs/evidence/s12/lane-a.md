# Session 12 lane A: enforcement, state and audit anchor (parts 38, 40/42, 14, 11/29)

Date: 2026-10-20. Built in worktree `harness-s12` (branch `s12/enforcement-state`), verified and
committed by the follow-up session, fast-forwarded into `master` (21ae180, 9f769a7, 13652f2, b12d839).

**Verification note.** The lane's venvs were symlinks to the master checkout's venvs, which import
master's sources (editable installs). Its own tests had therefore exercised master, not the lane's
code. Every result below was re-run with `PYTHONPATH` pointing at the lane's `contracts/src`,
`section1/src` and `section4/src` (confirmed by printing each package's `__file__`).

## What changed

| Part | Change | Tests |
|---|---|---|
| 38 | Realtime sockets (`/api/v1/ws/{project}`, `.../runs/{run}/collab`) are evaluated as a policy `read` at upgrade (4403 `POLICY_DENIED`) and on every recheck (closes 4410), after the project/run is verified to be the caller's team. `/api/v1/orgs/*` routes are classified, not exempt; the gate evaluates only the caller's own org. Policy reads are cached 5 s per process and cleared on any policy/org write by this process. | `test_policy_sockets.py` (sqlite), `test_policy_sockets_pg.py` (PG 18), `test_policy_gate.py` |
| 40 | `POST /api/v1/sessions/{id}/owner` transfers ownership to an active member, step-up for org teams, audited `session.owner_transferred`; stale expected owner or a lost race answers 409 `STALE_CLIENT_STATE` with `currentOwnerUserId`. Scheduled `session-purge` (hourly). | `test_session_owner_transfer.py`, `..._pg.py`, `test_session_purge_lease_sweep_live_pg.py` |
| 42 | Scheduled `lease-sweep` (30 s) releases lapsed control leases with the read path's compare-and-swap. | `test_session_purge_lease_sweep_live_pg.py`, `test_schedule.py` |
| 14 | `EventEnvelope.schemaVersion = 1` on the project stream (Python and TS contracts); the identity envelope passes route keys beside `error` (first emitter of `STALE_CLIENT_STATE`); `HANDOFF_DECIDED`/`LEADER_CHANGED` provoked on PG with a held row lock. | `test_conformance.py`, `integration/test_checkpoint1.py`, `test_collab_race_codes_live_pg.py` |
| 11/29 | `audit_anchor`: a scheduled job (`HARNESS_AUDIT_ANCHOR_PATH`) appends the chain head to an append-only file the database role cannot write; `verify_audit_chain.py --anchors` fails when an anchored row is gone or re-sealed (a privileged rebuild of the chain is detected). | `test_audit_chain_pg.py` (anchor cases) |

The follow-up session fixed one gap of its own: the lane's `schemaVersion` broke the envelope key
pins in `test_conformance.py` and `integration/test_checkpoint1.py`, and the TS envelope types
lacked the field.

## Results (lane sources, 2026-10-20)

| Command | Result |
|---|---|
| `section1` full suite (sqlite) | 981 passed, then the envelope pin fixed |
| `section1` lane modules on PG 18 (`test_conformance`, `test_policy_sockets_pg`, `test_session_owner_transfer_pg`, `test_audit_chain_pg`, `test_policy_gate`) | 37 passed |
| `section4` full suite (sqlite) | 1581 passed |
| `section4` lane live-PG modules + `test_spec_drift` + `test_program_docs` | 31 passed |
| `integration/test_checkpoint1.py` | 3 passed, 1 skipped (PG-only) |

## Limits

- HTTP routes still take project/workspace ids from the path; the gate does not pre-verify team
  ownership (the routes' own checks do). Sockets do verify.
- The policy cache is per process: another replica's write is seen within 5 s.
- The audit anchor file can be rewritten by the OS user running the app; production should point it
  at object-locked storage. Rows written and deleted between two anchors are not covered.
- Section 7 does not read `currentOwnerUserId` yet; no UI for ownership transfer.
