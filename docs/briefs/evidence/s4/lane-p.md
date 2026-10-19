# Lane P evidence (part 41 presence and participation)

Files: section1/migrations/0042_presence.sql, section4/src/harness_orchestrator/presence.py,
section4/src/harness_orchestrator/participation.py, section4/tests/unit/test_presence.py,
test_participation.py, test_presence_support.py (shared fixture), docs/presence.md.

Commands (section4, venv):
- `pytest -q -p no:cacheprovider tests/unit/test_presence.py tests/unit/test_participation.py` -> 33 passed (18 + 15).
- Whole section4 suite: 880 passed, 32 skipped (one earlier run failed in test_collab_backend while lane C was
  editing collab.py; the rerun was green).
- `ruff check src tests` (section4): All checks passed.
- Mutation checks: ignoring expiry (6 tests fail), ignoring share_presence (2), cap off-by-one (4) are all caught.
  Removing the leader pre-check in `leave` is not caught because the `DELETE ... role <> 'leader'` rowcount guard enforces it too.

Directives: 001 statuses + buckets + share_presence. 002 buckets only, notice. 003 role/isLeader/preferences in roster. 004 observe/participate/leave/rejoin/mute.
005 SESSION_FULL cap. 006 run guard 404 + hidden. 007 device merge. 008 expiry, 24h purge. 009 announcement fields. 010 team mode full/limited/off.

Honest gaps:
- Sqlite only; migration 0042 and the PostgreSQL advisory lock in `capacity_ok` and the `ON CONFLICT` upserts were not run on Postgres (lead).
- Capacity race is not proven concurrent (sequential and insert-race-fallback tests only).
- No org-level setting; team admins set it per team. Directive 010 is met at team level.
- Control status is only `isLeader`; lease holder comes from lane C's routes.
- Mode off: lane C sees no presence rows, so a "disconnected lease holder" can never be detected for off teams.
- `collab.join` is not yet gated by `capacity_ok` (lane C must add the call). Routers and configure_* are not registered in app.py (lead).
- Routes import `_authz_dep` from api.py and so authorize against `api._service`; production wiring must use the same service for `configure_presence/participation`.
- No heartbeat rate limit; scope `session` has no routes; no metrics.
- Observer -> participant is audited as `participant.joined` (no reserved name).
