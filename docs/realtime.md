# Real-time delivery (master prompt part 46)

Written for two readers: operators (what the servers guarantee, limit and measure) and the UI lane (section 5 is the
client contract, normative: MUST / SHOULD). Everything below was checked against the code and tests named next to it
(session 5, lane R). `s1` = `section1/src/harness_identity`, `s4` = `section4/src/harness_orchestrator`.

## 1. Decision (046.001)

| Question | Decision | Why it follows from what exists |
|---|---|---|
| Source of truth | the database table: `run_collab_events` (per run) and `agent_events` (per project) | rows already carry a monotonic `seq`, survive restarts, and are what history, export and retention operate on |
| Push | one WebSocket per open run view (`WS /api/v1/ws/{projectId}/runs/{runId}/collab`) and one per project (`WS /api/v1/ws/{projectId}`) | the identity service already owns tickets, fingerprints and team scoping for sockets (`s1/app.py` `_ws_authenticate`) |
| Wake-up | Postgres `LISTEN/NOTIFY` carries `(runId, seq, kind)` only (migration 0037); the socket always re-reads rows | a lost or duplicated NOTIFY can only delay delivery, never lose or repeat an event |
| Fallback | the socket also drains every `COLLAB_WS_POLL_SECONDS` (2 s); clients without a socket poll `GET /api/v1/orchestrator/runs/{runId}/collab-history?after=` | no component is a single point of failure; SQLite has no listener at all and works on the poll tick |
| Not chosen | a message broker, SSE, long-polling | no broker exists in the deployment; the table already is the durable, ordered log |

**Delivery guarantee: at-least-once, ordered per run by `seq`, never exactly-once, no ordering across runs.**
A frame can arrive twice (reconnect overlap, wake-up plus poll); it never arrives out of `seq` order on one connection;
an event is never skipped while the client keeps its cursor. Ordering holds because `collab._event` takes a per-run advisory lock
until commit on Postgres (`s4/collab.py:156`), so `seq` order is commit order for a run; `presence.event` takes the same lock.

## 2. The streams

| | Collaboration stream | Project stream |
|---|---|---|
| Table | `run_collab_events` | `agent_events` |
| `seq` | table-global `BIGSERIAL` | table-global |
| Cursor param | `afterSeq` (socket), `after` (HTTP) | `afterSeq` (socket), `after_seq` (HTTP replay) |
| Missing-event proof | `prevSeq` chain (this part) | none: per-project `seq` gaps are normal. Clients keep a set of seen `seq` and resume with a 64-seq overlap |
| Critical vs ephemeral | durable kinds only; `guidance.delivered` markers and any kind starting `presence.`, `typing.`, `cursor.` are never in the stream | n/a |

`seq` is global to the table, so a jump in `seq` proves nothing (other runs inserted rows). That is why each collaboration frame
carries `prevSeq`: the `seq` of the previous visible event of the same run (null for the first). A client whose cursor is `c`
may apply a frame iff `prevSeq` is null or `prevSeq <= c`; if `prevSeq > c` an event between them exists and is missing.

### Critical versus ephemeral (046.007)

* Critical, durable, replayable, on the socket and in HTTP history: participant, leader, handoff, redirect, approval, control and
  intent events (`participant.joined`, `leader.takeover`, `handoff.requested`, `approval.approved`, ...).
* Ephemeral, never durable: presence (online, idle, disconnected, last-seen bucket). `POST .../presence/heartbeat` writes only
  `presence_records` and no event; clients read presence with `GET .../presence` on their normal poll. Presence **changes of role**
  (`participant.observing|participating|left`) are durable events by design: they are roster facts, not signals.
* As an invariant, kinds starting `presence.`, `typing.` and `cursor.` are filtered out of replay, live push, history paging and the
  `prevSeq` chain (`s1/eventbus.py` `COLLAB_EPHEMERAL_PREFIXES`, `s4/delivery.py` `EPHEMERAL_PREFIXES`). Nothing writes such a kind today.
* Tests: `section1/tests/test_realtime.py::test_ephemeral_kinds_are_never_part_of_the_durable_stream`,
  `section4/tests/unit/test_delivery.py::test_heartbeats_write_nothing_to_the_durable_stream`,
  `::test_markers_and_ephemeral_kinds_are_not_in_the_durable_page_or_its_chain`.

## 3. Wire formats

### Admission (046.011)

1. `POST /api/v1/ws-ticket {projectId}` (bearer session) returns `{ticket, expiresInSeconds: 30, fingerprint}`. One use, bound to the project and
   the client fingerprint. 60 requests per minute per IP (shared limiter).
2. Open the socket with `?ticket=<ticket>&fingerprint=<fingerprint>&afterSeq=<cursor>` plus, for the collaboration socket,
   `&schemaVersion=<newest frame schema the client understands>&control=1`.
   The deprecated `?token=` still works and logs a warning; clients MUST NOT use it.
3. Scope: the principal must belong to the project's team (foreign or unknown project and a run not in the project both close 4404, no oracle);
   every row is read with `team_id = principal team` in SQL, so another team's rows are never sent even for a guessed run id.
4. While open, access is re-checked every `WS_REVALIDATE_SECONDS` (15 s): the user must still be a team member, the project still the team's,
   the run still in the project, and the user must still hold at least one unexpired session for the team. Otherwise the server closes **4410**.
   A store error keeps the socket open and retries at the next tick (availability over strictness). Worst-case revocation latency is the interval
   plus one poll tick (about 17 s).

### Frames on the collaboration socket (all JSON text)

```
event      {channel:"collab", seq, prevSeq, kind, actor, payload, ts, runId, projectId, teamId, schemaVersion}
truncated  {channel:"collab", replayTruncated:true, oldestSeqSent, afterSeq}              sent after the catch-up frames
ready      {channel:"collab", control:"ready", afterSeq, headSeq, cursorAhead, schemaVersion, serverTime}   only with ?control=1
error      {channel:"collab", error:{code:"RATE_LIMITED", message}, retryAfterSeconds}    last frame before close 4429
```

* Catch-up first (events with `seq > afterSeq`, ascending), then `truncated` if the gap exceeded `REPLAY_MAX_EVENTS` (5000; the newest 5000 are sent,
  so the first frame's `prevSeq` points at an event the client does not have), then `ready`, then live frames.
* `payload` over `BUS_PAYLOAD_MAX_BYTES` (8192) is replaced by `{"omitted":true,"reason":"payload exceeds the event bound"}`: refetch the body from HTTP history.
* `ready.cursorAhead` is true when `afterSeq > headSeq`: the client's cursor is from a log that no longer has that event (retention purge, restore).
  `headSeq` is the highest visible `seq` of the run (0 when none).
* Old clients that do not send `control=1` never see `ready` or any other control frame.
* Frames from the client are ignored (the socket is read-only) but counted: see limits.

### Project socket

`{type, payload, ts, seq, projectId, runId}` envelopes (`EventEnvelope`), then optionally `{replayTruncated:true, oldestSeqSent, afterSeq}`.
Overflow of the live queue closes **1013**; admission, limits and revocation behave exactly as above.

### HTTP polling fallback (046.005)

`GET /api/v1/orchestrator/runs/{runId}/collab-history?after=<cursor>&limit=<1..500>` returns events with `seq > after`, ascending.
Paging by the last `seq` neither skips nor repeats (commit order = `seq` order; proven by
`test_delivery.py::test_paging_by_cursor_neither_skips_nor_repeats_and_keeps_the_chain` for limits 1, 4, 7, 23, 200 across interleaved runs).
The enriched shape comes from `s4/delivery.py` `history_page`, which the lead wires into the route (evidence file, "Wiring"):

```
{events:[{seq, prevSeq, actor, kind, payload, ts, schemaVersion, runId}], nextCursor, hasMore, headSeq, schemaVersion}
```

A client MUST treat every field except `events[].seq|actor|kind|payload|ts` as optional (the route answers the older shape until wired):
absent `hasMore` means "more may exist when `events.length >= limit`", absent `prevSeq` means "ordered by cursor only", absent `schemaVersion` means 1.
History payloads are never shortened, so this is where an omitted socket payload is refetched.

## 4. Server behaviour: backpressure, limits, close codes

**Backpressure rule.** The log is durable, so a socket never buffers unbounded data and never drops: a slow consumer is slowed, a stuck one is closed, and
both resume from their cursor with nothing lost. (`s1/eventbus.py` `ConnectionGuard`.)

| Mechanism | Value | Env | Effect |
|---|---|---|---|
| Send timeout | 10 s per frame | `WS_SEND_TIMEOUT_SECONDS` | frame not written in time: close **4408** `SLOW_CONSUMER` |
| Live frame rate cap | 500 frames/s, burst 500 | `WS_MAX_FRAMES_PER_SECOND` | the socket waits for a send slot; later events simply wait in the table (lag grows, visible in the metric). Replay is exempt: it is one bounded answer |
| Replay bound | 5000 newest events | `REPLAY_MAX_EVENTS` | `replayTruncated` marker, client pages the rest over HTTP |
| Project queue | 1000 frames per subscriber | constructor | overflow: close **1013**, client resumes |
| Client messages | 120 per minute | `WS_INBOUND_PER_MINUTE` | over: last frame `error.code = RATE_LIMITED`, close **4429** |
| Sockets per user | 20 per process | `WS_MAX_CONNECTIONS_PER_USER` | over: close **4429** `RATE_LIMITED` before accept (the slot is released on close); process-local, so the cap is per replica |
| Access re-check | every 15 s | `WS_REVALIDATE_SECONDS` | close **4410** `ACCESS_REVOKED` |

**Stable close codes**

| Code | Name | Meaning | Client action |
|---|---|---|---|
| 1000/1001/1006 | normal, going away, abnormal | peer or network ended it | reconnect with backoff |
| 1013 | overflow | project socket queue overflowed | reconnect, backoff from 5 s |
| 4401 | unauthenticated | no or bad ticket/session | mint a new ticket once; if the mint answers 401, sign in again |
| 4403 | `TICKET_FINGERPRINT_MISMATCH` | ticket replayed from another client | mint a new ticket once; do not loop |
| 4404 | not found | unknown/foreign project or run | stop; show "not available" |
| 4408 | `SLOW_CONSUMER` | could not take a frame in time | reconnect with cursor, backoff from 5 s; poll meanwhile |
| 4410 | `ACCESS_REVOKED` | membership, project or every session is gone | stop sockets and polling; show "access lost" |
| 4429 | `RATE_LIMITED` | too many sockets or messages | wait `retryAfterSeconds` (30 s when absent), then reconnect |

**HTTP mutation rate limits.** The shared fixed-window limiter (Postgres `rate_buckets`, per IP and route template, fails open) answers
`429 {"error":{"code":"RATE_LIMITED","message":"too many attempts; slow down"}}` with `Retry-After`. `s4/delivery.py` `MUTATION_ROUTE_LIMITS`
lists the budgets for the collaboration mutations, installed on the deployed orchestrator together with the run-start budget (redirect 120/min; handoff, handoff decision, approval amend/decision/claim/complete/apply and leader 60/min each).
`POST /runs/{runId}/approvals` is deliberately absent: the limiter keys on the path, not the method, and that path is the 3-second list poll
(`test_limits_cover_real_mutation_routes_and_never_a_polled_path` fails if a polled path is added). WebSocket upgrades bypass the HTTP limiter, which is why the socket has its own limits.

## 5. Client contract (normative)

### 5.1 State per run

* `cursor`: integer, the highest `seq` applied in order; starts at 0. Kept **in memory**. Do not persist it across page loads unless the event
  list is persisted with it, otherwise a reload would resume past events it no longer shows. Durability of the stream is server-side: any cursor
  can be replayed from the table until retention purges the run (30 days after it finishes quiet, `s4/retention.py`).
* `seen`: not needed for the collaboration stream; "dedupe by `(runId, seq)`" is `seq <= cursor` (one cursor per run, so the pair is implicit),
  plus the set of buffered seqs. For the project stream keep a set of seen `seq` (see 2).
* `held`: the out-of-order buffer, at most **256 frames**, keyed by `seq`, with the time each arrived.

### 5.2 Applying a frame (reference implementation: `s4/delivery.py` `Reducer`, specification: `test_delivery_reducer.py`)

```
push(frame, now):
  if frame.seq <= cursor or frame.seq in held:  duplicate += 1; return []      # dedupe
  if frame.prevSeq is null/absent or frame.prevSeq <= cursor: apply(frame)       # in order
  else: held[frame.seq] = (frame, now)                                           # a predecessor is missing
  repeat: apply any held frame that became ready, lowest seq first; drop held frames <= cursor
gap(now) = held is not empty and (len(held) >= 256 or now - oldest_held >= 3 s)
apply(frame): cursor = frame.seq; deliver frame to the UI
```

* A `gap` (or a `replayTruncated` marker, or `ready.cursorAhead`) triggers **backfill**: page `collab-history?after=cursor&limit=200`
  while `hasMore` (or `events.length == limit` when `hasMore` is absent), pushing every event through the same `push`. Held frames then release in order.
  The socket stays open during a backfill.
* `ready.cursorAhead == true`: the log under the cursor is gone. Reset `cursor = 0`, clear the list, load history from 0.
* Unknown `kind`: apply (advance the cursor), render generically or not at all, never throw. Unknown fields and unknown `control` values: ignore.
* Repeats are normal (reconnect overlap, wake-up plus poll). Count them; they are not an error.
* Proven by `test_loss_duplication_and_reordering_always_converge_to_the_log` (30 seeds: 15% lost, 20% duplicated, 20% swapped, then backfill =
  exactly the log, in order, once) and `test_a_truncated_replay_is_repaired_by_paging_from_the_cursor`.

### 5.3 Connection lifecycle and visible status

Status values the UI MUST be able to show (text, not colour alone):

| Status | Entered when | Shown as |
|---|---|---|
| `connecting` | minting a ticket or opening the socket | "Connecting" |
| `catching_up` | socket open, `ready` not yet received | "Catching up" |
| `live` | `ready` received (or, against a server without `control`, after the first quiet moment of 1 s) | "Live" |
| `reconnecting` | socket closed, waiting to retry; polling is running | "Reconnecting, refreshing every 3 s" |
| `polling` | socket given up for now (4408 repeated, 4429, offline) | "Live updates paused, refreshing every 3 s" |
| `offline` | `navigator.onLine === false` | "You are offline"; retry on the `online` event |
| `access_lost` | 4404 or 4410, or a mint that answers 401/404 | "You no longer have access to this run" |
| `update_required` | a frame with `schemaVersion` greater than the client's | "This page is out of date, reload to see all activity" |

Accessibility: announce a status change politely and at most once per change, only for `reconnecting` after 5 s without a socket, `live` after a
`reconnecting`/`polling`, `access_lost`, `update_required`. Never announce each retry attempt.

**Backoff.** Attempt n (0-based) waits `d = min(30 s, 1 s * 2^n)` then jitters: `delay = d/2 + random(0, d/2)`. Nominal schedule 1, 2, 4, 8, 16, 30, 30... seconds.
Reset n to 0 only after a socket has been `live` for 30 s. A close code's own floor (table in section 4: 5 s for 1013/4408, `retryAfterSeconds` for 4429) wins over a shorter delay.
Do not retry while `offline`. Every attempt mints a fresh ticket (tickets are single use) and sends the current `cursor` as `afterSeq`.
Repeated 4408 (3 within 5 minutes): stay `polling` for 5 minutes before trying the socket again.

**Polling fallback.**

| Situation | Interval | Notes |
|---|---|---|
| socket not `live` | **3 s** +-20% jitter, starting immediately at the first close or failed connect | the same `push` pipeline; loop without waiting while `hasMore` |
| socket `live` | **20 s** safety poll | catches a lost wake-up of other resources (roster, approvals); events themselves arrive on the socket |
| tab hidden | may stretch to 30 s | resume at once on `visibilitychange` |
| HTTP 429 `RATE_LIMITED` | wait `Retry-After` | never retry faster |
| HTTP 404/403 on history | stop | `access_lost` |

Switching back: when `ready` arrives, stop the 3 s poll and keep only the 20 s safety poll.

### 5.4 Mutations and stale state (046.009)

* Every mutation that can be retried carries a `requestId` (idempotent: same id from the same user = one event, `duplicate:true`; ids are per actor).
* A mutation on an object that has a server version carries `expectedVersion`. The server refuses a version behind its own with 409 and re-read data.
  Treat **any** of these 409 codes as "my view is stale": `STALE_CLIENT_STATE` (generic, `s4/delivery.py`), `STALE_INTENT_VERSION` and `INTENT_CONFLICT`
  (intent edits, `s4/control.py`), `DIGEST_MISMATCH` (approvals: the approver echoes the digest they reviewed). Response data (`currentVersion`, `current`,
  `conflictFields`) is merged into the body where the route provides it. On any of them: re-read the object, show what changed, let the user decide; never
  auto-resubmit. A version **ahead** of the server answers 422 `BAD_VERSION` (a client bug).
* Where no server version exists (guidance, `redirect`, join, heartbeat) there is nothing to be stale against: guidance is append-only and idempotent by `requestId`.
* 429 `RATE_LIMITED` on a mutation: disable the control until `Retry-After` has passed and say so; do not auto-retry.
* **Emitter rule for `STALE_CLIENT_STATE` (session 12, part 14).** The generic code is for a mutation that names the state it read (an `expectedVersion`, or
  an `expectedOwnerUserId` where the object has no version) and finds that state changed. It is answered 409 with the current value merged into the body
  (`currentVersion` or `currentOwnerUserId`); the client re-reads and decides. An object that already has a specific stale code keeps it (the table above),
  so no existing response changes. The first emitter is `POST /api/v1/sessions/{id}/owner` (ownership transfer, `identity` `sessions.py`): a stale expected
  owner, and a lost transfer race, are both refused with `STALE_CLIENT_STATE` and `currentOwnerUserId`. The identity envelope now passes route keys beside
  `error` through to the body and to the legacy `detail` mirror, so these values reach the client.

### 5.5 Schema versions (046.008)

* Today `schemaVersion` is 1 on every collaboration frame (`run_collab_events.schema_version`, `s1/eventbus.py` `COLLAB_SCHEMA_VERSION`) and on every
  project-stream envelope (`EventEnvelope.schemaVersion` in `contracts/events.py`, added in session 12; no row column, the value is the writer's constant).
* Client MUST send `schemaVersion=<highest it understands>` on the socket (and treat a missing field on any frame as 1).
* **Compatible, no bump**: new `kind`, new optional payload key, new optional frame field, new `control` value. Clients ignore what they do not know.
* **Breaking, bump**: removing or renaming a field or kind, changing a field's type or meaning, changing the ordering/chain rule. A bump needs a release in which
  the server still writes the old version, and a migration note here.
* **Server rule**: a row whose `schema_version` is greater than the client declared is still sent (same `seq`, `prevSeq`, `kind`, `schemaVersion`) but with
  `payload = {"omitted":true,"reason":"schema_unsupported"}`; the chain stays intact. The client keeps its cursor, shows `update_required`, and renders
  the frame as unknown. HTTP history follows the same rule (`history_page(client_schema=)`).
* Tests: `test_a_newer_schema_row_keeps_its_place_but_not_its_body_for_old_clients` (socket), `test_newer_schema_rows_keep_their_seat_but_not_their_body` (HTTP).

## 6. Metrics (046.010)

Identity service, `harness_identity/realtime_metrics.py` (closed labels, every series rendered at 0, in memory). The lead appends its `render()` to the identity
`/metrics` next to `domain_metrics.render` (evidence file, "Wiring"). The pre-existing `harness_event_delivery_total{outcome}` is unchanged.

| Metric | Labels | Reading |
|---|---|---|
| `harness_realtime_connections_total` | `event`: `connect`, `resume`, `refused` | `resume` is a socket opened with `afterSeq > 0` (a reconnect or reload): **reconnect frequency** = `rate(...{event="resume"})` over live sockets |
| `harness_realtime_close_total` | `reason`: `client`, `slow_consumer`, `rate_limited`, `access_revoked`, `server_error` | why sockets end. `server_error` also counts a task cancelled at shutdown |
| `harness_realtime_recovered_total` | `kind`: `resumed_events`, `truncated_replay`, `poll_recovered` | **dropped-event recovery**: events a resuming client got back, resumes that exceeded the replay cap, and events the poll tick delivered with no wake-up |
| `harness_event_delivery_lag_seconds` | histogram, buckets 0.05 .. 60 s | **live freshness (the SLO signal)**: event `ts` (written inside the publishing transaction, immediately before commit) to the moment the frame was written to a live socket. Feeds `HarnessEventFreshnessSlo` (`le="5.0"` bucket ≥ 99% for 10m). Includes clock skew between hosts |
| `harness_event_replay_age_seconds` | histogram, buckets 1 .. 3600 s | **catch-up age (diagnostic, not the SLO)**: commit time to the write of a replayed frame. A client that was closed for minutes legitimately replays old events, so a large value is not a freshness violation |

Suggested reading: p95 lag `histogram_quantile(0.95, sum by (le) (rate(harness_event_delivery_lag_seconds_bucket[5m])))`; the SLO ratio
`sum(rate(harness_event_delivery_lag_seconds_bucket{le="5.0"}[5m])) / sum(rate(harness_event_delivery_lag_seconds_count[5m]))`; sustained `poll_recovered` on Postgres
means NOTIFY is not reaching the listener; any `access_revoked` is a person losing access while a page was open; `slow_consumer` bursts point at a network or client problem.
The deployed end-to-end path is exercised by `scripts/e2e/w17_event_freshness_drill.py`. Alert rules live in `deploy/observability/alerts.yml`.

## 7. Configuration

| Env | Default | Where |
|---|---|---|
| `COLLAB_WS_POLL_SECONDS` | 2.0 | collab socket safety drain |
| `REPLAY_MAX_EVENTS` | 5000 | catch-up cap (both sockets) |
| `BUS_PAYLOAD_MAX_BYTES` | 8192 | frame payload bound |
| `WS_SEND_TIMEOUT_SECONDS` | 10 | slow-consumer close |
| `WS_MAX_FRAMES_PER_SECOND` | 500 | live frame rate cap |
| `WS_INBOUND_PER_MINUTE` | 120 | client message allowance |
| `WS_MAX_CONNECTIONS_PER_USER` | 20 | sockets per user per process |
| `WS_REVALIDATE_SECONDS` | 15 | access re-check |

A non-numeric or non-positive value falls back to the default.

## 8. Tests

* `section1/tests/test_realtime.py` (19): chain across runs, noise, forged team and resume; truncated chain; ephemeral kinds; schema compatibility; `ready`
  frame; send timeout with resume; rate cap pacing (fake clock); stuck-peer close; inbound flood; connection cap; revocation by member removal, logout everywhere,
  expired session, run moved, store error; metrics and closed labels; project socket.
* `section4/tests/unit/test_delivery.py` (15): polling pages, scope, ephemeral, schema, stale-client refusal, mutation limits and the 429 envelope.
* `section4/tests/unit/test_delivery_reducer.py` (37): the client rules, including 30 randomized loss/duplicate/reorder runs.
* Existing and still green: `test_audit_export_and_collab_push.py` (one line changed: the pinned field set now includes `prevSeq`), `test_ws_auth.py`,
  `test_c5_ws_hygiene.py`, `test_ws_replay_cap.py`, `test_ws_fingerprint.py`, `test_slim_bus.py`.

## 9. Limits (honest)

* **Revocation is polled, not pushed.** Up to about 17 s after membership removal or logout everywhere. Logging out one device does not close sockets
  opened by another device of the same user while any session is live. There is no hook from the member-removal route into open sockets.
* **Socket caps are per process**, not per cluster. The rate limiter for HTTP is per IP and route (NAT shares a budget).
* **Project stream** (`agent_events`) has no `prevSeq` chain; its ordering under concurrent publishers is by `seq` only and a resume can in principle skip a
  lower `seq` that commits after a higher one was delivered (hence the 64-seq overlap rule). The collaboration stream does not have this window.
* **`mark_guidance_delivered`** inserts a marker row without the advisory lock; it is a filtered kind and never part of the visible chain.
* **Delivery lag** uses two clocks (orchestrator writes `ts`, identity reads it).
* **Retention** (`s4/retention.py`) deletes whole finished runs; a cursor into a purged run shows as `cursorAhead`.
* **Stale-client protection** exists only where the server has a version or an expected owner: intent edits, intent conflict resolution, approval digests,
  handoff state, session ownership transfer. The generic 409 `STALE_CLIENT_STATE` has one emitter (session ownership transfer, 5.4); the older codes stay for
  compatibility. Clients in section7 do not read `currentOwnerUserId` yet (session B).
* **HTTP history**: `GET /runs/{runId}/collab-history` returns `delivery.history_page` (the socket's frame shape with `prevSeq`, `schemaVersion`, `nextCursor`, `hasMore`, `headSeq`).
* The browser client implements the rules above (`realtimeCore.ts`, `collabStream.ts`; see `docs/accessibility.md` and `evidence/s5/lane-x.md`). Hooked containers (panel, stream hook) are covered by tsc only, not by a browser run.
