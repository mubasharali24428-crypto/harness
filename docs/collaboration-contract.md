# Collaboration contract (master prompt part 14)

The one place that defines participants, the leader, redirects, handoffs and high-impact approvals: their requests, responses, errors,
states and legal transitions. Source of truth is `section4/src/harness_orchestrator/collab.py`; `section4/tests/unit/test_collab_contract.py`
fails if this page and the code disagree (every error code in `collab.py` must appear in the index below; the transition table is executed).
Routes live under `/api/v1/orchestrator/runs/{runId}`. Errors are `{"detail": {"error": {"code", "message"}}}` with the status shown.
Schemas of events and audit rows: `docs/audit-and-events.md`. Wire frames, ordering, replay, reconnect: `docs/realtime.md`. Limits:
`docs/contracts-and-limits.md`. Control lease: `docs/control.md`.

## 1. Roles and who may do what (14.001)

Identity comes only from the authenticated principal; no body field names an actor (`test_forged_actor_fields_in_bodies_are_ignored`).
Team roles: `admin`, `engineer`, `viewer`, `guest` (`harness_contracts.roles`); `viewer` and `guest` are read-only.

| Run role | How you get it | May |
|---|---|---|
| `observer` | `POST /join` as a read-only team role | read only. Every write route is behind the role gate, so a viewer or guest is refused 403 `ROLE_DENIED` before `collab` runs |
| `participant` | `POST /join` as admin/engineer | guidance, propose approvals, amend and execute their own approved proposals |
| `leader` | `POST /leader` on a leaderless run; handoff accept; admin takeover | everything a participant may, plus scope/permission redirects, handoff, and deciding approvals |

* Exactly one leader per run (partial unique index). `POST /leader` on a led run is 409 `LEADER_EXISTS`; a team **admin** replaces a sitting leader
  (event `leader.takeover`, pending handoff rejected); an engineer must use handoff. Viewer/guest: 403 `ROLE_DENIED`.
* Joining a full run: 409 `SESSION_FULL` (participant cap, `docs/sessions.md`). `GET /participants` returns `{participants[], leader, pendingHandoff}`.
* A leader is the holder of an expiring control lease (`docs/control.md`); a lapsed lease vacates the leader at the next leader check.
* A refusal by role or by being a non-leader is 403 (`ROLE_DENIED` for a read-only team role, `NOT_PARTICIPANT` for a writer who has not joined the run, `NOT_LEADER`, `NOT_REQUESTER`) and leaves one `authz.denied` audit row.
  An unknown or foreign-team run is 404 `NOT_FOUND` for everything (no existence oracle).

## 2. Redirects (14.003)

`POST /redirect {kind, content, reason?, requestId?}` with `kind` in `guidance` (default), `scope_change`, `permission_change`.

| Rule | Behaviour |
|---|---|
| Who | any joined writer for `guidance` (a viewer or guest: 403 `ROLE_DENIED`; not joined: 403 `NOT_PARTICIPANT`); **leader only** for `scope_change` and `permission_change` (403 `NOT_LEADER`) |
| Content | 1 to 4000 characters after trimming (422 `BAD_CONTENT`); `kind` outside the three values is 422 `BAD_KIND` |
| Reason | required for `scope_change` and `permission_change` (422 `REASON_REQUIRED`), at most 500 characters (422 `BAD_REASON`) |
| Effect | one `redirect.<kind>` event in `seq` order; every instruction is kept, none merged (`docs/audit-and-events.md` section 6). A scope/permission change **invalidates** every pending and approved approval of the run (section 5) |
| Shared steering | guidance from a non-holder while a timed lease is live is queued for the holder: `{queued: true, instructionId, seq: null}` (`docs/control.md`) |
| Response | `{seq, duplicate, invalidatedApprovals}` (queued responses add `queued`, `instructionId`) |
| Idempotency | `requestId` (max 64): the same user repeating it gets `duplicate: true` and the original `seq`. The key is **per actor**; another participant's id never suppresses yours. No `requestId` means no dedupe |

## 3. Handoff (14.004)

| Call | Rule |
|---|---|
| `POST /handoff {toUserId}` | leader only (403 `NOT_LEADER`); not yourself (422 `SELF_HANDOFF`); recipient must be a joined engineer/admin (422 `BAD_RECIPIENT`); one open handoff per run (409 `HANDOFF_PENDING`). Response `{handoffId, state: "pending", from, to}`; the leader keeps control meanwhile |
| `POST /handoff/{id} {accept}` | the recipient only (403 `NOT_LEADER` for anyone else); unknown or already decided is 404 `NOT_FOUND`. Accept demotes the proposer and promotes the recipient in one transaction and starts a fresh lease; if the proposer is no longer leader the whole accept is refused (409 `LEADER_CHANGED`). A lost decision race is 409 `HANDOFF_DECIDED` |

Handoff states: `pending` to `accepted` or `rejected` (by the recipient, or `rejected` when an admin takes over the run), or `cancelled` (the lease expired or the holder released). A rejected
or cancelled handoff changes nothing about who leads. Events: `handoff.requested|accepted|rejected`. `LEADER_CHANGED` and `HANDOFF_DECIDED` are
race-only codes; they cannot be provoked deterministically on SQLite and are exercised only by the live-PostgreSQL race suites (not run in this session).

## 4. High-impact approvals: states (14.005)

`POST /approvals {action: "delete"|"modify", paths[], summary, diff?}` creates a request bound to a **digest** (sha-256 over action, sorted paths and diff) and a
TTL (`HARNESS_APPROVAL_TTL_SECONDS`, default 3600). Viewers and guests cannot request (403 `ROLE_DENIED`), nor can writers who have not joined (403 `NOT_PARTICIPANT`); bad action 422 `BAD_ACTION`; empty summary or oversized
summary/diff 422 `BAD_PROPOSAL`; paths are validated (422 `BAD_PATHS`: relative only, no `..`, no NUL, no backslash, at most 100 of at most 512 characters).

States: `pending`, `approved`, `rejected`, `expired`, `invalidated`, `executing`, `completed`, `failed`. Terminal: `rejected`, `expired`, `invalidated`,
`completed`, `failed`. Every transition is a compare-and-swap `UPDATE`, so racing callers have exactly one winner, and writes one event and one audit row
in the same transaction.

| From | Action (who) | To | Refusal when the state is wrong |
|---|---|---|---|
| (none) | request (participant) | `pending` | |
| `pending` | decide approve/reject (leader, not the requester) | `approved` / `rejected` | digest differs: 409 `DIGEST_MISMATCH`; past its TTL: 409 `EXPIRED` and the row becomes `expired`; leader demoted mid-request: 403 `NOT_LEADER` |
| `pending` | amend (requester) | `pending` (new digest, new TTL) | |
| `approved` | amend (requester) | `pending`; the approval is voided and `approval.invalidated` is recorded | |
| `pending` | TTL passes | `expired` (swept on list, pending check or decide) | |
| `pending`, `approved` | scope or permission redirect (leader) | `invalidated` | |
| `approved` | claim (requester or leader) with the reviewed digest | `executing` | digest changed or approval past its TTL: 409 `NOT_APPROVED` (an approved row past its TTL stays `approved` but can no longer be claimed) |
| `executing` | complete (requester or leader) `{ok, outcome}` | `completed` / `failed` | |

Anything else is refused: a decision on a non-pending row is 409 `NOT_PENDING`; amend on any state but `pending`/`approved` is 409 `NOT_AMENDABLE`
(403 `NOT_REQUESTER` for anyone but the requester); claim on a row that is not `approved` is 409 `NOT_APPROVED`; complete on a row that is not `executing` is
409 `NOT_EXECUTING`; claim/complete by someone who is neither requester nor leader is 403 `NOT_LEADER`. Approving your own request is 403 `SELF_APPROVAL`
unless `HARNESS_ALLOW_SELF_APPROVAL=1`. `test_collab_contract.py::test_every_state_and_action_pair_follows_the_table` executes all of these pairs.

## 5. Invalidation (14.006)

A proposal is only as good as the digest the approver reviewed. An approval stops being usable when (a) the requester amends it (the new digest; an approved row goes back to
`pending`), (b) the leader issues a scope or permission change (`invalidated`, `invalidatedApprovals` in the redirect response), (c) the TTL passes (`expired`), or
(d) claim finds a different digest. A decision racing a scope change never leaves the approval usable (`test_scope_change_racing_an_approval_decision_never_leaves_it_usable`).

## 6. Ordering, replay, reconnect (14.007)

Per run, `seq` order equals commit order; a frame names the previous visible event (`prevSeq`); a client dedupes by `seq`, buffers out-of-order frames, and on a gap
refetches `GET /collab-history?after=<cursor>`. Full client rules, close codes and backpressure: `docs/realtime.md` sections 3 to 5. Frames carry `schemaVersion`
(today 1); a row newer than the client understands keeps its place with its payload withheld.

## 7. Limits (14.008)

Text 4000, reason 500, paths 100 of 512, diff 200000, event payload on the bus 8192 bytes, socket and HTTP rates: `docs/contracts-and-limits.md` and `docs/realtime.md` section 4.

## 8. Compatibility and contract documentation (14.009, 14.010)

Additive changes (new event kind, new optional field) need no major bump; breaking changes follow `docs/api-versioning.md`. The orchestrator OpenAPI file, the SDK specs and the UI
calls are checked against the mounted routes by `scripts/spec_drift.py` (clean at this commit).

## 9. Error index

| Code | Status | Meaning |
|---|---|---|
| `NOT_FOUND` | 404 | unknown run, approval or pending handoff, or a run of another team |
| `NOT_PARTICIPANT` | 403 | join the run as a non-observer first |
| `NOT_LEADER` | 403 | only the leader (or the handoff recipient / requester / claimant where stated) may do this |
| `NOT_REQUESTER` | 403 | only the requester may amend |
| `ROLE_DENIED` | 403 | the team role cannot lead the run |
| `SELF_APPROVAL` | 403 | another leader must approve your own request |
| `LEADER_EXISTS` | 409 | the run already has a leader (or you already lead it) |
| `LEADER_CHANGED` | 409 | the proposer or sitting leader changed during the call; retry (race-only) |
| `HANDOFF_PENDING` | 409 | a handoff is already open |
| `HANDOFF_DECIDED` | 409 | the handoff was decided by someone else first (race-only) |
| `SESSION_FULL` | 409 | the run's participant limit is reached |
| `NOT_PENDING` | 409 | the approval is no longer pending |
| `NOT_AMENDABLE` | 409 | the approval is past amendment |
| `NOT_APPROVED` | 409 | claim needs an approved, unexpired approval with the same digest |
| `NOT_EXECUTING` | 409 | complete needs an executing approval |
| `DIGEST_MISMATCH` | 409 | the proposal changed since the approver reviewed it |
| `EXPIRED` | 409 | the approval request expired |
| `BAD_KIND` | 422 | redirect kind not one of guidance, scope_change, permission_change |
| `BAD_CONTENT` | 422 | redirect content empty or over 4000 characters |
| `REASON_REQUIRED` | 422 | scope and permission changes need a reason |
| `BAD_REASON` | 422 | reason over 500 characters |
| `SELF_HANDOFF` | 422 | cannot hand off to yourself |
| `BAD_RECIPIENT` | 422 | recipient must be a joined engineer or admin |
| `BAD_ACTION` | 422 | approval action not delete or modify |
| `BAD_PROPOSAL` | 422 | summary required; summary or diff too large |
| `BAD_PATHS` | 422 | a path is absolute, escapes the workspace, or exceeds a limit |

`BAD_KIND`, `BAD_ACTION` and `BAD_REASON` are defence in depth: the request models (`Literal` kinds and actions, `max_length`) reject those inputs first with the
framework's 422 validation envelope, so only a direct call of `collab` reaches them.
