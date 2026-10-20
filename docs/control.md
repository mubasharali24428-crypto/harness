# Simultaneous control and conflicts (part 42)

As built. Code: `section4/src/harness_orchestrator/control.py` (routes, lease operations, intent, conflicts),
`collab.py` (lease primitives, expiry-aware `_leader_id`, queue hook in `redirect`, capacity hook in `join`).
Schema: `section1/migrations/0043_control.sql`. Tests: `section4/tests/unit/test_control.py` (44).

## 1. Control model (directive 001, 002)

A run has **one exclusive controller and shared steering**.

* The controller is the run **leader**, and the leader **is the lease holder**. `run_participants.role = 'leader'`
  (one per run, partial unique index, migration 0035) plus a `control_leases` row (primary key `run_id`, so exclusivity is
  also a key) that adds `expires_at`, `renewed_at` and a `version` counter.
* Only the holder may: change scope/permissions (`redirect` scope_change/permission_change), decide approvals, hand off,
  transfer, release, apply intent edits directly, accept or reject suggestions, resolve conflicts (an admin may also resolve).
* Everyone else steers by **suggestion**: guidance sent by a non-holder through `POST runs/{id}/redirect` (kind `guidance`)
  is stored in `control_instructions` and queued for the holder instead of reaching the agent (section 5).
* Pre-0043 leaders (no lease row, or a database without the table) keep the old **untimed** leader until they call
  `control/acquire` (turns it into a timed lease) or the role changes hands. Every path that makes a leader
  (`POST /leader`, handoff accept, admin takeover, `control/acquire|transfer|decide grant`) now creates a fresh lease.

## 2. Lease lifecycle (directive 003, 004)

| Rule | Value |
|---|---|
| Duration | `HARNESS_CONTROL_LEASE_SECONDS` (default 900); a request may pass `ttlSeconds` 30..3600 (422 outside) |
| Renewal | `control/renew` by the live holder, CAS on holder and `expires_at > now`; also what a repeated `acquire` by the holder does. No renewal cap |
| Expiry | enforced **at use**: `collab._leader_id` (called by every leader path) deletes a lapsed lease (`DELETE ... RETURNING` is the compare-and-swap), demotes the leader, cancels the pending handoff and writes `control.expired` (event + audit, actor `system:control-lease`). `GET control`, `GET participants` and join/claim do the same sweep. There is no background sweeper: the row is written by whichever request first notices |
| Release | holder only, voluntary: role back to participant, lease deleted, pending handoff cancelled. The queue head gets first right to `acquire` |
| Transfer | holder to a joined engineer/admin, immediate (use handoff for an accept step). One transaction: demote, promote, new lease, requester marked granted, handoff cancelled |
| Revoke | team **admin** only, reason required (422 `REASON_REQUIRED`), 409 `NO_HOLDER` when free. Frees control, cancels the handoff |
| Disconnect | holder has no unexpired `presence_records` row (`scope_kind='run'`) for the run. Not an automatic release: once the holder is also past a grace period since its last renewal (`HARNESS_CONTROL_DISCONNECT_SECONDS`, default 60) the lease is **takeable** and any eligible engineer/admin may `acquire` it (audited `control.acquired` with `takeover: holder_disconnected`). Presence unknown (table absent, or the team's `team_presence_settings.mode = 'off'`) is never "disconnected" |
| Who may hold | role engineer/admin (viewer/guest 403). Acquire auto-joins the run (lane P capacity applies, 409 `SESSION_FULL`) |
| Free lease + queue | only the queue head may acquire (409 `QUEUE_ORDER`, names `nextInQueue`); with an empty queue anyone eligible may |

Existing admin takeover through `POST /leader` is unchanged (admin replaces a live leader; audited `leader.takeover`).

## 3. Request queue and the "who holds control" view (directive 003)

`POST control/request` (eligible role): files a request in `control_requests`, FIFO by `created_at`, one pending per person
(retry returns the same request, `duplicate: true`), life 10 minutes (then `expired`, can be refiled), 409 `CONTROL_FREE`
when nobody holds control (acquire instead), 409 `ALREADY_HOLDER`. Ties on an identical timestamp have no defined order.
`POST control/decide`: `{requestId, decision}` with `grant` (holder: transfer to the requester), `decline` (holder) or
`cancel` (the requester). Declines and cancels are audited as `control.requested` with `after.state`.

`GET control` returns: the model (lease seconds, grace, TTLs), `holder` (user, expiry and seconds left, connected,
takeable, `untimed` for legacy), the `queue` with positions, `queuedInstructions`, the pending handoff, a `viewer` block
(`isHolder`, `canAcquire`, `canRequest`, `canSuggest`, `queuePosition`), `how` (the exact routes to request/acquire/suggest)
and an `announcement` sentence for assistive technology.

## 4. Concurrent instructions (directive 005)

| Instruction | Rule |
|---|---|
| Guidance from the holder, or when nobody holds a timed lease | recorded directly, ordered by `seq`, every row kept, same `requestId` from the same user = one row (the key is per actor) |
| Guidance from a non-holder while a timed lease is live | **queued** (`control.instruction_queued`); response `{queued: true, instructionId, seq: null}`. Queue bound 50 open (409 `QUEUE_FULL`), life 1 h. Same `requestId` returns the same instruction |
| Holder decision | `control/decide {instructionId, decision: accept}` writes it as an ordinary `redirect.guidance` event (carries `suggestedBy`) that the agent receives through `guidance_since`; `reject` (optional reason) is audited `control.instruction_rejected` |
| Scope / permission changes | leader only, as before (403 `NOT_LEADER`); expired holder included |
| Intent edits | section 6; stale ones are rejected, never merged |

Nothing is silently merged and nothing is silently dropped: suggestions that expire stay in the table as `queued` past
`expires_at` (not listed, not decidable: 409 `NOT_PENDING`).

## 5. Versioned run intent (directive 006, 008)

`run_intent_versions(run_id, version, goal, scope_paths, permissions, ...)` is append-only; the current intent is the highest
version (0 = never set: empty goal, no paths, no permissions). `PUT intent` body: `expectedVersion` (required) plus any of
`goal` (<= 4000 chars), `scopePaths` (relative, no `..`, <= 100; empty = none recorded), `permissions` (<= 50 lowercase
tokens like `fs.delete`), optional `requestId`. Omitted fields are unchanged; a value equal to the current one is a no-op
(`unchanged: true`). The intent is a **recorded approved state** that humans and approvals are checked against; the engine
does not enforce it (section 9).

| Case | Result |
|---|---|
| `expectedVersion` ahead of the intent | 422 `BAD_VERSION` |
| equals current, caller is the holder (or nobody holds control) | applied as version+1; the primary key `(run_id, version)` rejects the loser of a race with 409 `STALE_INTENT_VERSION` |
| equals current, someone else holds control | **proposal** queued for the holder, HTTP 202 `{queued, proposalId}` (`control.instruction_queued`) |
| older than current, and the editor's changed fields were not touched since their version | 409 `STALE_INTENT_VERSION` with `currentVersion`, `current`, `conflictFields: []`. Re-read and resubmit; there is no automatic merge |
| older than current, and a changed field also moved since their version | 409 `INTENT_CONFLICT` with `conflictId`, `conflictFields`, `current`; a `control_conflicts` row (kind `conflict`) and a `control.conflict_detected` audit row |
| editor not a joined non-observer participant | 403 `NOT_PARTICIPANT`; read-only roles 403 |

Applied goal/scope edits by the holder also emit a `redirect.scope_change` event so the agent is told the new intent
(permissions are not injected, same as the existing permission_change rule). Detection happens before anything is written.

## 6. Conflict view and resolution (directive 007, 009)

`GET intent/conflicts?state=open|resolved|all` returns `currentApproved` (the current intent version), every conflict or
proposal with per-field `base` / `current` / `proposed` / `conflicting`, `stale`, and `openApprovals` (the pending/approved
high-impact approvals a resolution could void) plus the options `keep_current | take_proposed | merge`.

`POST intent/conflicts/{id}/resolve` `{resolution, expectedVersion, merged?}`: holder or team admin (else 403
`NOT_LEADER`); `expectedVersion` must equal the current version so the resolver has seen the state being changed (409
`STALE_INTENT_VERSION`); a conflict resolves once (409 `ALREADY_RESOLVED`); `merge` needs `merged` (422 `EMPTY_EDIT`).

**Forced re-review.** A resolution that changes the intent writes a new version and calls `collab._invalidate`: every pending
or approved high-impact approval of the run becomes `invalidated` (event + audit, counted in `invalidatedApprovals`,
response `reviewRequired: true`), so nothing approved against the old state can be claimed (409 `NOT_APPROVED`); staged
approvals are re-filed by the staging gate on its next call, other requesters must file again.
Conflict resolutions void on **any** change; a holder's direct edit or an accepted proposal voids only when scope paths or
permissions changed (goal rewording does not), matching the existing scope_change rule. `keep_current` changes nothing.

## 7. Audit and events (directive 010)

Every transition writes a collaboration event (`GET collab-history`, WebSocket push) and an `audit_log` row in the same
transaction, with `before`/`after`, using the reserved names: `control.requested|acquired|renewed|released|transferred|
revoked|expired|conflict_detected|conflict_resolved|instruction_queued|instruction_rejected`. Intent edits and accepted
proposals use `intent.updated` and accepted suggestions `redirect.recorded` (not in the reserved list: raw-SQL section4 audit,
see evidence file). Refusals a user can hit are stable codes and leave one `authz.denied` row (no double row for
`INTENT_CONFLICT`). Free-text secrets are not scanned (key-name scrub only).

## 8. Routes (prefix `/api/v1/orchestrator`, all run-guarded: foreign or unknown run = 404, same envelope)

`GET runs/{id}/control`; `POST runs/{id}/control/(request|acquire|renew|release|transfer|revoke|decide)`;
`GET|PUT runs/{id}/intent`; `GET runs/{id}/intent/conflicts`; `POST runs/{id}/intent/conflicts/{conflictId}/resolve`.
Reads need the run's team; writes need a non-read-only role; the pilot gate (`HARNESS_PILOT_TEAMS`) applies. Wiring:
`app.include_router(control.router)` and `control.configure_control(service)`.

## 9. Limits (honest)

* The engine does not enforce the intent; it is the recorded approved state plus the injected scope guidance.
* Expiry is evaluated at use, not by a timer; `decide_approval`'s final UPDATE checks leadership (role) not lease expiry,
  so a lease that lapses in the milliseconds between the leader check and that UPDATE is caught on the next call.
* Disconnect detection depends on lane P writing `presence_records` for lease holders regardless of their
  `share_presence` preference; with presence off it is disabled (takeover only after expiry or by admin revoke).
* The roster still lists `leader` from the role row; it sweeps lapsed leases first, but a lease that lapses between two
  reads shows until the next request.
* No auto-merge of stale edits, no per-field history diff beyond `changed_fields`, no retention job for the new tables
  (they cascade with the run).
* SQLite-tested only here; the SQL uses `DELETE ... RETURNING`, partial unique indexes and `to_regclass` (Postgres) and was not run on Postgres.
* `docs/audit-and-events.md` section 6 rule 1 (guidance is never queued) now has this exception; that file was not edited by this lane.
