# Lane S evidence (part 40 sessions and run domain model)

Files: section1/migrations/0041_sessions.sql, section1/src/harness_identity/sessions.py,
section1/tests/test_sessions_support.py (+ test_sessions_core.py 13 tests, test_sessions_lifecycle.py 34),
section4/src/harness_orchestrator/session_gate.py (+ import and 2 calls in service.py:
`start_execution` after `enforce_run_start`, `_dispatch_claim` after the workspace check),
section4/tests/unit/test_session_gate.py (13 tests), docs/sessions.md.

Commands (venv python, sequential, never two at once):
- section1 `python -m pytest -q -p no:cacheprovider`: 831 passed, 27 skipped (59 s). Lane S: 47 tests.
- section4 `python -m pytest -q -p no:cacheprovider`: 971 passed, 8 failed, 32 skipped (39 s). The 8 failures
  are other lanes' files (tests/unit/test_control.py x7, test_agent_tree.py x1: lane C / lane A work in
  progress), none touch sessions. `test_session_gate.py` 13 passed; `test_workspace_gate.py` 10 passed.
- `ruff check src tests` (no --fix): all lane S files clean. Remaining findings are not mine: section1
  `src/harness_identity/agents.py:61` E501 (lane A), section4 `tests/unit/test_control.py` 11 findings (lane C).
- One ad hoc run of the two gate test files stalled over 2 minutes while another lane's full section4 suite
  was running; rerun alone took 12 s and passed. Not reproduced; suspected CPU contention, not confirmed.

Directive map: 001/002 docs "Session vs run" + session_runs; 003 member roles + leader; 004/005 `run_phase`
table, `done` only terminal, gate + 409 `SESSION_NOT_ACTIVE`; 006 versioned templates; 007 `/resume`;
008 `parent_run_id`; 009 soft member status + name snapshot + `accessLost`; 010 soft delete, `purge_after`,
admin purge, runs preserved.

Invariant tests that fail if the rule breaks: gate refuses start per state and queued-then-paused claim
(single and batch), pause-vs-start race, unlink lifts gate, missing-table tolerated but other DB error
fails closed; link refused when not active; complete/delete refused with a live run; link-vs-complete race on
a file sqlite DB with separate connections; purge before retention refused (fake clock via monkeypatched
`now_utc`); wrong team / non-member / left member all 404 with the same envelope; stale `expectedVersion`;
observer, participant and leader permission edges; workspace mismatch.

Needs from the lead:
- Register `from .sessions import router` in app.py; the route paths are exactly the contract list. `/templates`
  routes are registered before `/{sid}` ones.
- Section1 migrations must be applied before section4 serves sessions; section4 tolerates absence.
- `run_phase()` duplicates section4's event replay (`_replayed_durable_state`). If section4's rules change the
  mirror must change; moving the pure function to harness_contracts would remove the duplication.
- No new path was needed outside the contract list. Run creation with a `sessionId` (create-and-link in one
  call) would need an orchestrator route and was not invented: runs are created, then linked.

Honest gaps:
- sqlite only. Migration 0041 was applied to sqlite (partial unique indexes, CHECKs); not run on Postgres. The row-value
  cursor in the list query, `INSERT`-free link locking via `UPDATE ... WHERE state='active'`, and the complete/delete
  "flip, then check, roll back" ordering rely on Postgres row locks and READ COMMITTED behaviour that sqlite cannot show.
  The race test proves no bad outcome in 8 trials, not under real concurrency.
- No automatic purge job; `purge_after` is advisory until an admin calls purge. Audit rows are never erased.
- stopped is indistinguishable from done and failed is `blocked` (non-terminal) because the event log has no distinct
  stop or fail events. Artifacts/discussions/decisions have no session-level store (reached through runs and history).
- Running runs are not stopped when a session is paused/archived; `resume_agent` is not gated.
- Owner transfer is not implemented, so an owner can never leave. Leader/owner roles are session-local; they do not feed
  the per-run collab leader (`run_participants`), which is lane C's concern.
- Removal from the team leaves `session_members.status='active'` with `accessLost:true`; no cleanup hook.
- Agent identities are not validated against the agent registry (lane A); no spawn-tree column on session_runs.
- Resume `policy` does not include org policy-gate rules; resume writes an audit row per call (can be noisy).
- Quota: no per-team session cap; 100 active members per session (count-then-insert, can overshoot under concurrent adds).
- Gate fails closed on unexpected DB errors: in `_dispatch_claim` that raises into the poll loop and leaves the claim
  to orphan recovery instead of starting the run.
