# S1-R05 — Audit integrity, safe cancellation, interrupted-run recovery

**Status: COMPLETE.**
Risk: R-05. Fix scope: S1 audit allowlist + audit read surface, S4 authz
denial audit FK, S4 stop durability (replay tombstone), S4 queue-mode resume,
run.started/stopped audit emission.

## Findings (as registered, re-verified)

- **R-05a (authz.denied audit silently lost):** `_emit_authz_denied` bound
  `principal.userId` (users.id) into `audit_log.actor_member_id`, which FKs to
  `team_members(id)` (`0001_initial.sql:99`) → every denial-audit insert
  FK-fails on Postgres and is swallowed (`authz.py:135-138`). Test DDL omitted
  the FK so SQLite tests could never catch it. `authz.denied` also missing
  from `APPEND_ONLY_ACTIONS`.
- **R-05b (append-only is ORM-only):** ORM update/delete blocked by event
  guard; no DB trigger/revoke/hash-chain; raw-SQL writers bypass the guard by
  design (they only INSERT). Retention deletes partitions by design.
- **R-05c (stop not durable):** stop_agent reaches DONE in RAM + emits
  PAUSED(reason=user_requested_stop), but replay maps that event back to
  PAUSED — post-restart a stopped run is non-terminal again and a later
  done-signal can drive it forward. RAM-path eviction pin is conservative
  (kept while unproven).
- **R-05d (queue-mode resume is a silent no-op):** `resume_agent` skips
  `sm.resume()` when `_execution_already_started` is True ("already started
  durably; resume re-queues instead"). For a run whose durable head is PAUSED
  or BLOCKED that skip means NO RESUMED event ever lands, so the enqueued
  claim hits `_dispatch_claim`, replays PAUSED, fails the EXECUTING check and
  the claim is dropped. Bare-mode resume works only because an unwired store
  answers False. No queue-mode resume test existed.
- **R-05e (no audit read surface / emitters unwired):** no `/audit` route in
  any app; `emit_run_started/stopped` have zero production callers.

## Threat model

Audit integrity: an operator investigating "who stopped/started what, and who
was denied" finds an incomplete record — denial rows silently missing
(FK), stop intent discoverable only by parsing an event reason string that
replay ignores. Cancellation: a stopped run can be resumed/completed after a
restart (stop not terminal durably). Recovery: pause→resume does not actually
restart execution under the production queue wiring (silent loss of operator
intent). These are integrity/operability failures, not external attack
surface; the fix restores operator-verifiable truth.

## Fix design (as implemented)

1. **R-05a:** `_resolve_member_id(team, user)` looks up `team_members.id`;
   no row (the common denial case — non-member) binds NULL, which the column
   permits, with userId/teamId already in `detail`. `authz.denied` added to
   `APPEND_ONLY_ACTIONS` (canonical allowlist). Test DDL now declares the real
   FK + `PRAGMA foreign_keys=ON`, plus a seeded-member test that FAILS under
   the old binding and passes under the new one.
2. **R-05b:** documented residual — append-only guard remains ORM-scope by
   design; raw writers only INSERT; retention is operator policy. No pretend
   fix (a DB trigger would need a migration + still cannot stop the DB owner).
3. **R-05c:** replay honors the stop tombstone: a PAUSED event with
   `reason == "user_requested_stop"` replays as terminal DONE in `_rehydrate`
   (events after the tombstone are inert). Post-restart stopped runs read
   DONE; done-signals on them raise (DONE sealed). Deliberate scope decision:
   the eviction-witness walk (`_replayed_durable_state`) was NOT changed —
   eviction stays conservative (requires the witnessed terminal verdict), so
   stopped runs remain cached until a later wave proves otherwise; the
   pinned eviction test is preserved. The witness/dispatch mapping of
   stop-PAUSED stays PAUSED, which only ever causes a stale claim to be
   finished (correct) and never a resurrection (rehydration reports DONE).
4. **R-05d:** regression test FIRST (queue-mode: sqlite store + run_claims +
   worker dispatch): pause→resume must actually resume. Fix: `resume_agent`
   drives `sm.resume()` (durably emitting RESUMED) whenever the durable head
   shows a resumable state (PAUSED/BLOCKED), instead of only when the store
   has no opinion. Non-resumable heads keep the re-queue-only behavior.
5. **R-05e:** S4 writes `run.started` / `run.stopped` audit rows through a
   small raw-SQL helper (same discipline as `_emit_authz_denied`; S4 may not
   import S1 code) at `start_execution` and `stop_agent`. S1 gains
   `GET /api/v1/audit` — admin-only, newest-first, bounded page — as the
   operator read surface over `audit_log`.

## Rollback

Per-fix revert: allowlist entry, member-id resolution (falls back to old
binding), tombstone branch, resume condition, audit writes/route. No schema
changes, no migration, no wire changes.

## Residuals

- R-05b as above (ORM-scope guard, no tamper-evidence at DB level; retention
  deletes partitions by design). Hash-chain/trigger = post-pilot candidate.
- Audit emission from S4 is best-effort (never breaks run lifecycle),
  same posture as all audit emits.
- Queue-mode resume fix covers PAUSED/BLOCKED heads; resume of
  ENGINE_FAILED-head runs (no reaper-BLOCKED event) is not auto-resumed —
  operators re-start via plan flow. Documented, not silent.

## Verification results

Commands run from the repo root (per-section venvs; all green):

1. **S4 full suite:** `cd section4 && ./.venv/bin/python -m pytest tests/ -q`
   → **480 passed, 5 skipped** (was 470 pre-R-05; +10 new tests: 2 R-05a,
   2 R-05c, 2 R-05d, 4 R-05e). Ruff: `./.venv/bin/ruff check src tests` →
   clean (fixed 2× E501 + import sort in new test code).
2. **S1 full suite:** `cd section1 && ./.venv/bin/python -m pytest tests/ -q`
   → **633 passed, 10 skipped** (+4 new R-05e route tests). Ruff clean.
3. **R-05a non-vacuousness:** the FK-enforcing test DDL + `PRAGMA
   foreign_keys=ON` was validated independently (raw-insert probe → sqlite
   `IntegrityError`), so the seeded-member test fails under the old
   users.id binding (row vanishes into the swallowed except) and passes
   under the member-id resolution.
4. **R-05c:** with the R-05c code active but the bus double still persisting
   `{}` payloads, the new restart test read PAUSED (the tombstone needs the
   real reason); after fixing `_PersistingBus` to persist real envelopes,
   `test_stopped_run_reads_done_after_restart` → DONE and
   `test_replayed_stop_rejects_later_done_signal` →
   `InvalidStateTransitionError`. Pinned conservative-eviction test
   (`test_stateless_orchestrator_n1.py` stop-run-kept) unchanged and green.
5. **R-05d RED→GREEN:** `tests/unit/test_s1_r05_resume_queue.py` was run
   against the unfixed `resume_agent`: failed exactly at
   `assert "resumed" in await _event_types(...)` (durable log:
   plan_proposed, plan_approved, execution_started, paused — no resumed;
   pre-fix `_execution_already_started(PAUSED)=True` skipped `sm.resume()`).
   After the fix: RESUMED persisted, re-queued claim dispatched
   (`_dispatch_claim → True`), state EXECUTING, engine invoked twice
   (original leg + resumed continuation). Resume-on-DONE regression test
   stays a no-op under the same queue wiring.
6. **R-05c/R-05d/R-05e interplay:** `tests/unit/test_resume.py` (bare-mode
   pins), `tests/test_heartbeat_claims.py`, and
   `tests/test_stateless_orchestrator_n1.py` all green together (28 passed)
   before the full-suite runs.
7. **R-05e:** S4 route tests assert `run.started`/`run.stopped` rows with
   `actor_member_id == team_members.id` (FK-enforced DDL), NULL-actor rows
   for non-members with identity in `detail`, and bare-mode emission is a
   silent no-op. S1 route tests assert team scoping (foreign member rows and
   foreign NULL-actor rows excluded; unbound denial rows visible via
   `detail.teamId`), newest-first order, limit clamp (1..200), admin-only
   (engineer/viewer 403, anonymous 401), and JSON-text detail from a
   simulated S4 raw insert renders parsed.
8. **Cross-checks:** no deploy, contracts, or mcp changes in R-05 — S10/mcp
   suites not re-run by design (resource review: no wire/schema/env change).
