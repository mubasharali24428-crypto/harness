# Lane N evidence (part 47 notifications and user control)

Files: section1/migrations/0046_notifications.sql, section1/src/harness_identity/notifications.py,
section4/src/harness_orchestrator/notify.py, tests section1/tests/test_notifications_{support,inbox,prefs,delivery}.py
(66 tests), section4/tests/unit/test_notify.py + test_notify_sources.py (58 tests), docs/notifications.md.
Edits to existing files (additions only): section4 collab.py (import, `_notify` helper, 3 calls: approval
requested, handoff requested, handoff decided), section4 service.py (`_RUN_PROBLEMS`, a task in `_emit`,
`_notify_run_problem`), section1 notifier.py (9 lines in `NotificationWorker._loop`).

Commands (venv python, sequential):
- section1 `python -m pytest -q -p no:cacheprovider`: 944 passed, 28 skipped (72 s). (A first run with `-x` also
  passed; it was a second full run, not a separate result.)
- section4 `python -m pytest -q -p no:cacheprovider`: 1143 passed, 35 skipped (50 s).
- `ruff check src tests` (no --fix) in section1 and section4: all checks passed.
- PostgreSQL 18.4 (local Homebrew cluster in the scratchpad, no docker, removed afterwards): all migrations 0001-0046
  applied through `deploy/runners/migrate.py`; ad hoc smoke of emit (created, grouped, duplicate, rejected), every
  inbox route, mute, snooze, deliver_due, run_digests and a forced failure inside `emit` (caller's transaction
  stayed usable). The smoke found one real bug (an untyped parameter in a CASE, AmbiguousParameterError on asyncpg),
  fixed. This smoke is not a committed test.

Directive map: 001 inbox + kinds (approval, mention, handoff, failure, blocked, control_request, suggestion);
002 email only with opt-in, default off for every kind, re-checked at send time; 003 global/workspace/session rows
with per-event entries, precedence table pinned in both sections; 004 quiet hours (user zone, fake clock), digests,
urgent bypass; 005 dedupe_key (atomic) and burst grouping with count; 006 ids + closed code only, fixed-text
previews and mail; 007 `GET /link/{id}` re-checks run/session/workspace each call, 404 after access is lost;
008 `delivery` JSON with email state, attempts, reason; 009 mute ended not deleted, snooze, dismiss, token
unsubscribe (single use, idempotent), all audited with the reserved actions; 010 no sound/popup anywhere, badge
count route, documented UI rules.

Needs from the lead:
- `from .notifications import router as notifications_router` and `app.include_router(...)` in section1 app.py.
  `/unsubscribe` has no auth dependency by design (policy gate skips requests with no principal).
- Env `HARNESS_APP_URL` for mail links (empty means relative links); optional `HARNESS_NOTIFY_GROUP_SECONDS`.
- Add `tzdata` to slim images; without it saved zones fall back to UTC in emit.
- Optional wiring not done (files not mine): `kind='control_request'` / `'suggestion'` calls in control.py;
  lane D already calls `kind='mention'`. No gate or path outside the contract list was needed.

Honest gaps: no pruning of inbox/dedupe rows; no metrics (domain_metrics not mine); simultaneous first events of
a burst can create two rows; emit does not verify the recipient can see the scope (link re-check does); run
failure/block recipients are leader + participants only (a run with no participants notifies nobody); English-only
preview; email is the only extra channel; ruff-clean but the pre-existing `test_real_smtp_path...` thread warning
can appear in combined runs (GC-timing, not mine).
