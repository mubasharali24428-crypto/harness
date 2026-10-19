# Lane C evidence: part 42 simultaneous control and conflicts

Files: `section1/migrations/0043_control.sql`; `section4/src/harness_orchestrator/control.py` (new);
`section4/src/harness_orchestrator/collab.py` (edited: lease primitives, expiry-aware `_leader_id`, `set_lease` in
`claim_leader`/`decide_handoff`, roster sweep, queue hook in `redirect`, lazy `capacity_ok` in `join`);
`section4/tests/unit/test_control.py` (44 tests); `docs/control.md`. `staging.py` was not edited: it has no leader logic;
its apply path goes through `claim_approval` -> `_require_executor` -> `_leader_id`, now lease-aware
(`test_an_expired_holder_cannot_claim_or_decide_approvals`).

Commands run (section4, sequential):
* `./.venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_control.py` -> 44 passed (6 repeated runs, stable, ~5 s).
* `./.venv/bin/python -m pytest -q -p no:cacheprovider` -> 979 passed, 32 skipped, ~40 s, three clean runs (one earlier
  full run stalled at 0 % CPU for 10 minutes and was killed; not reproduced in three reruns or in the lane tests; suspect
  interference from other lanes running tests at the same time, not identified).
* `./.venv/bin/ruff check src tests` -> control.py, collab.py, test_control.py clean; other lanes' files had findings
  (agent_tree.py) at the time. Never used `--fix`.
* Mutation check: removing the expiry sweep from `_leader_id` fails 4 control tests.

## Directives
001 model: leader = exclusive lease holder + shared steering by queued suggestions (docs/control.md s1, s4).
002 lease state: `control_leases` (PK run_id) next to the leader role; acquire/renew/release/transfer/revoke routes.
003 view: `GET control` (holder, expiry, queue, viewer flags, `how`, announcement) + `control/request` FIFO queue.
004 duration/renewal/expiry/revocation/disconnect: 900 s default (30..3600), renew CAS, expiry at use with `control.expired`, admin revoke with reason, presence-based takeover after a 60 s grace.
005 concurrent instructions: holder/leaderless direct in seq order, non-holder guidance queued then accepted/rejected, scope/permission leader-only, stale intent rejected; documented.
006 conflict detection: stale `expectedVersion` compared field by field before anything is written; `INTENT_CONFLICT` + conflict row.
007 conflict view: `GET intent/conflicts` shows base/current/proposed per field, `currentApproved`, open approvals.
008 version checks: `expectedVersion` + `(run_id, version)` primary key CAS; race test with two simultaneous edits.
009 renewed review: resolution that changes intent calls `_invalidate` (any change for conflicts, scope/permissions for direct edits); tested including claim refusal.
010 audit: each transition = event + audit row with before/after, reserved `control.*` names (test checks the set).

## For the lead
* Wire `app.include_router(control.router)` and `control.configure_control(service)`.
* `collab.join` now calls `participation.capacity_ok` when importable (lane P's module was present and exercised in the tests); refusal is 409 `SESSION_FULL`.
* Non-reserved audit names used (section4 raw SQL, not validated by section1): `intent.updated`, `redirect.recorded`. Rename or reserve if wanted.
* Behaviour change on an existing route: `POST runs/{id}/redirect` kind `guidance` by a non-holder is queued (response has `queued: true`, `seq: null`) when a timed lease exists. Clients/UI that expect immediate delivery need the `control/decide` accept step. Runs without a lease row are unchanged.
* No extra paths were needed beyond the contract; suggestions are submitted via the existing `redirect` route and decided via `control/decide`.
* Lane P contract assumption: `presence_records` rows exist for lease holders independent of `share_presence`.
* Capability-matrix row and `docs/audit-and-events.md` section 6 are not edited here (not owned).

## Honest gaps
* Not run on Postgres/docker (forbidden); SQLite only. Event ordering lock path (`pg_advisory_xact_lock`) untouched.
* Engine does not enforce intent; no background lease sweeper; no UI.
* Simultaneity is tested with asyncio.gather on SQLite (serialised writers), which proves the CAS logic but not Postgres lock timing.
