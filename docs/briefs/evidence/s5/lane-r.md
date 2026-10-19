# Session 5, lane R: real-time delivery (part 46)

Contract: `docs/briefs/path-to-9-session5-prompt.md`. Behaviour and the client contract: `docs/realtime.md`.
Not committed. No docker, make, smoke or Playwright run.

## Files

Changed: `section1/src/harness_identity/eventbus.py`, `section1/src/harness_identity/app.py` (WebSocket region only: `project_ws`, `run_collab_ws`, new
helpers `_still_allowed`, `_revoked`, `_refuse_over_cap`, `_lag_seconds`; `_ws_authenticate` and the ticket code are untouched),
`section1/tests/test_audit_export_and_collab_push.py` (one line: the pinned frame field set now includes `prevSeq`).
New: `section1/src/harness_identity/realtime_metrics.py`, `section4/src/harness_orchestrator/delivery.py`, `section1/tests/test_realtime.py`,
`section4/tests/unit/test_delivery.py`, `section4/tests/unit/test_delivery_reducer.py`, `docs/realtime.md`, this file.
No migration (0047 not needed: the existing tables and `schema_version` column are enough). No new public route.

## Per-directive audit

| # | Already existed (HEAD) | Missing | Closed by |
|---|---|---|---|
| 001 transport and guarantees | WS push + NOTIFY wake-up + poll tick described in `docs/audit-and-events.md` section 4; `eventbus.py` `_listen` (HEAD :307), `collab_waker` (:499), `notify_collab` (:513) | no recorded decision, no stated guarantee | `docs/realtime.md` section 1 (at-least-once, ordered per run, no broker) |
| 002 ordered, durable cursors, replay | `run_collab_events.seq` + per-run advisory lock (`s4/collab.py:156-165`); `collab_replay_page` (:529) and `collab_since` (:563) with `afterSeq`, cap `REPLAY_MAX_EVENTS` and truncation marker; HTTP `collab.history` (`collab.py:1008`) | `seq` is table-global, so gaps are invisible; HTTP page has no `hasMore`/`nextCursor`/head, is not team-scoped in SQL; no "cursor ahead of the log" signal | `prevSeq` chain on every frame and HTTP event (`_collab_frame`, `_chain_frames`); `ready` control frame with `headSeq`, `cursorAhead` (opt-in `control=1`); `delivery.history_page`. Tests `test_realtime.py::test_every_frame_names_the_previous_visible_event...`, `::test_a_resuming_client_gets_a_chain...`, `::test_ready_frame_is_opt_in...`; `test_delivery.py::test_paging_by_cursor_neither_skips_nor_repeats...` |
| 003 duplicate, delayed, missing, out of order | server skips `seq <= cursor` and counts `duplicate` (HEAD `app.py` drain loop); truncation marker; client `mergeEvents` sorts by seq | no way to prove a missing event; no executable client rules | `prevSeq`; truncated-replay chain test; `delivery.Reducer` (cursor, dedupe, 256-frame / 3 s out-of-order buffer, gap, backfill) + 37 tests incl. 30 randomized loss/duplicate/reorder runs |
| 004 reconnect, backoff, resume, visible status | socket takes `afterSeq`; the panel reconnects with a fixed 5 s, no status beyond live/not live (`collabApi.ts openCollabSocket`) | backoff, jitter, status states, close-code policy undefined | `docs/realtime.md` 5.3 (states, backoff 1..30 s equal jitter, close-code table); server side: stable close codes, `ready` frame, resume metrics. Client implementation is lane X |
| 005 polling fallback | 2 s drain on the socket; `GET collab-history?after=` (UI polls 3 s / 20 s with socket) | server contract of the fallback untested, no paging flags | `history_page` (`nextCursor`, `hasMore`, `headSeq`, `prevSeq`); tests for limits 1/4/7/23/200 over interleaved runs, exact `hasMore`, empty page, clamps; intervals in `docs/realtime.md` 5.3 |
| 006 backpressure and rate limits | project queue 1000 + `overflowed` -> close 1013; `REPLAY_MAX_EVENTS`; payload bound 8192; HTTP limiter (`ratelimit.py`) for auth edge, ws-ticket 60/min, run start | collab socket: unbounded awaited sends, no slow-consumer policy, no per-connection limits, no collab mutation route limits; limiter ignores WebSocket | `ConnectionGuard` (send timeout -> 4408, frame rate cap, inbound allowance -> `RATE_LIMITED` frame + 4429), per-user socket cap (4429), applied to both sockets; `delivery.MUTATION_ROUTE_LIMITS` (lead wires). Tests: stuck peer, fake-clock pacing, flood, cap and release, 429 envelope after the real redirect budget |
| 007 critical vs ephemeral | presence is polled (`presence.py` heartbeat writes `presence_records` only); `guidance.delivered` filtered | the separation was by habit, not enforced or tested | `presence.`/`typing.`/`cursor.` kinds filtered from replay, live, history and the chain (`COLLAB_EPHEMERAL_PREFIXES`, `EPHEMERAL_PREFIXES`); tests incl. heartbeat storm writes zero events |
| 008 schema evolution | `schema_version` column (0035) and `schemaVersion` on frames, always 1 | no policy, no client declaration, nothing enforced | policy in `docs/realtime.md` 5.5; `?schemaVersion=` on the socket, newer rows keep seq/prevSeq/kind but payload `schema_unsupported` (socket and HTTP); tests both |
| 009 stale client mutations | intent edits: `STALE_INTENT_VERSION`/`INTENT_CONFLICT` with `expectedVersion` (`control.py:782,848,953`); approvals `DIGEST_MISMATCH` (`collab.py:913`); handoff `HANDOFF_DECIDED` (:672); lease has a `version` column but no client-carried version | generic 409 `STALE_CLIENT_STATE`; forbidden to edit control/collab | `delivery.StaleClientState`, `require_current_version` (409 behind, 422 `BAD_VERSION` ahead, nothing when no version carried), `STALE_CODES` (checked against source). No existing route emits `STALE_CLIENT_STATE` yet: honest gap, see below |
| 010 metrics | `harness_event_delivery_total{outcome}` (`domain_metrics.py`) | lag, reconnects, recovered events | `realtime_metrics.py`: connections{connect,resume,refused}, close{...5}, recovered{resumed_events,truncated_replay,poll_recovered}, lag histogram; closed labels, zero-rendered; tests |
| 011 authenticated, scoped subscriptions | ticket (single use, 30 s, project + fingerprint bound), team-scoped SQL, 4404 no oracle; documented gap "sockets are not re-validated" (`audit-and-events.md` limits, T10b) | revoked access kept receiving | `principal_still_valid` every `WS_REVALIDATE_SECONDS`: member removed, project/run moved, no unexpired session -> close 4410; store error keeps socket. Tests: member removal (other user unaffected), logout everywhere, expired session, run moved, store error, project socket |

## Verification (exact commands)

* `cd section1 && .venv/bin/python -m pytest tests/test_realtime.py -q` -> 19 passed.
* `cd section4 && .venv/bin/python -m pytest tests/unit/test_delivery.py tests/unit/test_delivery_reducer.py -q` -> 52 passed (15 + 37).
* Existing WS suites: `section1 .venv/bin/python -m pytest tests/test_audit_export_and_collab_push.py tests/test_ws_auth.py tests/test_c5_ws_hygiene.py tests/test_ws_replay_cap.py tests/test_ws_fingerprint.py tests/test_slim_bus.py -q` -> 79 passed, 1 skipped.
* Invariant checks: emptying `COLLAB_EPHEMERAL_PREFIXES` fails the ephemeral test; forcing `prevSeq` to null fails three chain tests (reverted).
* Full: `section1`: `.venv/bin/python -m pytest -q` -> 935 passed, 28 skipped. `section4`: `.venv/bin/python -m pytest -v -p no:cacheprovider` -> 1142 passed, 37 skipped, 1 failed. The failure is `tests/unit/test_spec_drift.py::test_repo_specs_and_ui_calls_match_router`: lane D's new `/comments` and `/discussion-settings` routes are not in `openapi.json` yet (lead regenerates; not caused by lane R, which adds no route). An earlier full run was killed after it stalled with no output while other lanes were running; the rerun finished in 48 s.
* `ruff check src tests` (no `--fix`) in section1 and section4: All checks passed.

## What the lead must wire (shared files, not touched)

1. `deploy/runners/services.py` `app_identity`: the scrape extra must also render `realtime_metrics`, e.g.
   `_wire_observability(app, lambda: identity_domain_metrics.render() + realtime_metrics.render())`.
2. `section4/api.py` `collab_history`: `return await _collab(lambda f: delivery.history_page(f, actor.teamId, runId, after, limit))` (adds `prevSeq`, `schemaVersion`,
   `nextCursor`, `hasMore`, `headSeq`; `events[]` keeps `seq/actor/kind/payload/ts`). Optional `schemaVersion` query -> `client_schema`.
3. `section4/ratelimit_hooks.py`: `routes=(*DEFAULT_PROTECTED_ROUTES, *delivery.MUTATION_ROUTE_LIMITS)`, `route_limits={**ORCHESTRATOR_ROUTE_LIMITS, **delivery.MUTATION_ROUTE_LIMITS}`.
4. `docs/audit-and-events.md`: frame field set now has `prevSeq`; the "sockets are not re-validated (T10b)" limit is closed (15 s re-check, 4410); counters section should
   point to `docs/realtime.md` section 6.
5. `deploy/observability/alerts.yml`: optional alerts on p95 `harness_event_delivery_lag_seconds`, `poll_recovered`, `slow_consumer` (suggestions in `docs/realtime.md` section 6).
6. Decide whether `STALE_INTENT_VERSION` stays (clients accept the whole `STALE_CODES` set) or control.py moves to `STALE_CLIENT_STATE`; openapi/sdk regeneration if the history response is
   documented there.
7. Lane X implements `docs/realtime.md` section 5 (today's client treats frames as wake-ups and polls 3 s / 20 s).

## Honest gaps

* No existing mutation route emits `STALE_CLIENT_STATE` (control/collab are off limits); 009 is met by helper + tests + documented coverage of the older codes.
* `POST /runs/{runId}/approvals` has no route limit: the limiter keys on path and the same path is the 3 s list poll. Needs method-aware limiting.
* Revocation is polled (about 17 s), not pushed, and logout of one device does not close another device's socket. Socket caps are per process.
* The project stream (`agent_events`) has no `prevSeq`; late-commit window remains (client overlap rule documented).
* `section4/delivery.py` and `eventbus.py` duplicate the chain/visibility rules (sections may not import each other); parity is by tests on each side, not a shared test.
* Delivery lag includes cross-host clock skew; `server_error` close also counts shutdown cancellations.
* The browser client is not changed (lane X).
