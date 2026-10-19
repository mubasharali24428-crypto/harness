# Sessions and runs (master prompt part 40)

Code: `section1/src/harness_identity/sessions.py` (router `/api/v1/sessions/...`), migration
`section1/migrations/0041_sessions.sql`, run gate `section4/src/harness_orchestrator/session_gate.py`.
Covered by `section1/tests/test_sessions_core.py`, `test_sessions_lifecycle.py` and
`section4/tests/unit/test_session_gate.py` unless marked **not enforced**.

## Session vs run (001, 002)
A **run** (`agent_runs`) is one execution of an agent against a project: plan, approval, execution,
review. A **collaboration session** (`collab_sessions`) is a team-owned container that groups many
runs, their members and their history. The link is `session_runs(run_id PRIMARY KEY, session_id,
parent_run_id, linked_by, linked_at)`: a run belongs to at most one session, a session holds many
runs. A run that belongs to no session behaves exactly as before. Session operations never write
to `agent_runs`, `agent_events` or `agent_plans`.

Artifacts, discussions and decisions are not session tables in this lane: they stay attached to
their runs and are reached through the session's run list and its audit history (`/resume`).
**Not enforced:** a session-level artifact/discussion store.

Runs are created by the orchestrator, then linked with `POST /{id}/runs {runId, parentRunId?}`.
Linking needs: a run of the caller's team (foreign or unknown run: 404), a session that is
`active` (409 `SESSION_NOT_ACTIVE`), session permission to link (below), and, when the session has a
workspace, a run whose project is in that workspace (409 `WORKSPACE_MISMATCH`). Re-posting the same
link returns 200; a run in another session is 409 `RUN_ALREADY_LINKED`.

## People: owner, leader, participants, observers, agents (003)
`session_members` holds one row per identity and is never deleted by leaving or removal.

| Role | How it is set | Can do |
|---|---|---|
| `owner` | the creator; fixed (no transfer in this lane) | everything a manager can; cannot leave |
| leader | `PATCH {leaderUserId}`; must be an active owner or participant | pause, complete, link and unlink runs |
| `participant` | add / `PUT members/{id}` | read, link runs (needs team role admin or engineer) |
| `observer` | add / `PUT members/{id}` | read only |
| `agent` | add with `agentId` (+ optional `displayName`), or via a template | an identity on the roster; has no credentials here |

Team **admins** manage every session of their team without being members. Team role `viewer` or
`guest` cannot create sessions or link runs even as participants. Only members of the owning team
can be added (422 `NOT_A_TEAM_MEMBER`). A session holds at most 100 active members (409
`MEMBER_LIMIT_REACHED`). Agents are session identities only: `agentId` is a free string validated
for shape, with no registry check (the agent registry is lane A).

## Visibility
A session is readable by its **active members** and team admins. Everyone else, other teams and
unknown ids get the same 404 `NOT_FOUND` (no existence oracle). Deleted sessions are visible to
admins only. Mutations on a visible session the caller may not change return 403.

## Run states (004) and terminal rules (005)
Run state is not a column: section4 derives it from the durable event log. `run_phase()` in
`sessions.py` replays the same rules for the session views (it is a mirror of section4's replay;
keep them in step).

| Phase | Event(s) that put a run there | Terminal |
|---|---|---|
| `created` | no events yet | no |
| `awaiting_plan_approval` | `plan_proposed` | no |
| `approved` | `plan_approved` | no |
| `queued` | `run_claims.state='queued'` while created/approved/executing | no |
| `executing` | `execution_started`, `resumed` | no |
| `paused` | `paused` (only from executing) | no |
| `awaiting_done_signal` | `engine_completed` | no |
| `reviewing` | `done_signal_received` | no |
| `blocked` | `blocked_awaiting_clarification`, `engine_failed`, `review_failed` | no (needs a human) |
| `pr_opened` | `pr_opened` | no |
| `done` | `review_passed` behind an open done-gate; also a stop | **yes (sealed)** |

Mapping to the directive's words: creation = `created`, queued = `queued`, planning = plan proposed,
approval = `awaiting_plan_approval`/`approved`, executing/paused as named, completed = `done`,
**failed = `blocked`** (a failed engine or review waits for a human, it is not terminal), and
**stopped = `done`** (the state machine seals a stopped run as DONE and emits no distinguishing
event, so a stop cannot be told apart from an accepted review). `done` is the only terminal phase;
nothing resurrects it.

Session states: `active`, `paused`, `completed`, `archived`, `deleted`.

| Action | From | To | Who |
|---|---|---|---|
| `pause` | active | paused | owner, admin, leader |
| `complete` | active, paused | completed | owner, admin, leader; refused (409 `SESSION_HAS_ACTIVE_RUNS`) while any linked run is not `done` |
| `archive` | active, paused, completed | archived | owner, admin |
| `restore` | paused, completed, archived | active | owner, admin |
| `delete` | archived | deleted | owner, admin, reason of 3+ chars; refused while a linked run is not `done` |
| `purge` | deleted | (tombstone) | admin, after retention |

Repeating a transition is a no-op (`changed:false`, no audit). Anything else is 409
`ILLEGAL_TRANSITION`; delete from a live session is 409 `SESSION_NOT_ARCHIVED`.

**Can a session launch another run?** Only while `active`. For `paused`, `completed`, `archived`
and `deleted` sessions: linking a run is refused (409 `SESSION_NOT_ACTIVE`), starting a linked run
is refused by the section4 gate (`start_execution`, 409 `SESSION_NOT_ACTIVE`, audited as
`session.run_refused`), and a run that was already queued when the session stopped is dropped from
the queue at claim time (single and batch claim). Runs already executing are **not** stopped; they
finish normally. The gate re-reads the state on every call and does not cache. It tolerates only a
missing session schema (pre-0041 stores); any other database error propagates and the run does not
start. `resume_agent` is not gated. Unlinking a run (`DELETE /{id}/runs/{runId}`, owner/admin/leader,
any session state) detaches it and lifts the gate for that run; it is audited.

## Parent tasks, subtasks and spawned agents (008)
`parent_run_id` links a run to another run **in the same session** (422 `PARENT_NOT_IN_SESSION`
otherwise, also for self or unknown parents). A parent with children cannot be unlinked (409
`RUN_HAS_CHILDREN`). `GET /{id}/runs` returns the flat list with `parentRunId`, `phase` and
`terminal`; the tree is the client's to build. Spawned agents appear as `kind:'agent'` members; the
spawn tree itself (depth, budget, assignments) is lane A's `agent_tree`. **Not enforced:** a
`spawned_by_agent` column on `session_runs`, and cycle detection beyond "parent must already be
linked" (a run can only get a parent at link time, so cycles cannot form).

## Templates (006)
`POST /templates` (team role admin or engineer) saves a new **version** of a named template
(`name@N`, N increments per team and name). Defaults are a closed schema: `description` (<=1000),
`retentionDays` (1-365), `agentIds` (<=10). Unknown keys or wrong types are 422. Create with
`templateName` (+ optional `templateVersion`, default latest) or apply later with
`POST /{id}/apply-template`; the session records the template name and version, and the audit rows
(`session.template_applied`) carry before/after. Applying overwrites description and retention and
adds the template's agents idempotently; it never removes members. Templates are team-scoped
(another team's template is 404). **Not enforced:** templates that pre-assign human members or
settings beyond the three keys.

## Resume (007)
`GET /{id}/resume?limit=50` returns in one payload: the session; the full roster including members
who left or were removed; the runs with phase; the last `limit` (max 200) audit events for the
session (`action`, `at`, actor display name, scrubbed `detail`); `you` (member role, leader flag and
the caller's **current** effective permissions: `linkRuns`, `manageMembers`, `changeState`,
`restore`, `delete`, `purge`); and `policy` (`launchAllowed`, `launchBlockedBy`
`SESSION_NOT_ACTIVE`|`WORKSPACE_NOT_ACTIVE`, retention, purge date, linked workspace state).
Permissions are computed from the live state, so a paused session reports `linkRuns:false`.
Resume does not reopen a session (use `restore`) and writes one `session.resumed` audit row per call.
**Not enforced:** org policy rules from the policy gate are not folded into `policy`.

## Leaving and losing access (009)
- Leaving (`DELETE members/{self}`) sets `status='left'`; removal by a manager sets `'removed'`.
  Neither deletes the row. The row keeps `display_name` as a snapshot, so history stays readable
  after the person is renamed or removed from the team. Rejoining reactivates the same row.
- The owner cannot leave (409 `OWNER_CANNOT_LEAVE`). A leader who leaves or is removed clears the
  leader (`leaderCleared` in the audit row).
- Someone who left or was removed gets 404 on the session; the history stays for everyone else.
- If a user is removed from the **team**, their member row stays `active` and the roster marks
  `accessLost:true`; they cannot authenticate into the team, so they cannot read or act. A manager
  should remove them from the session explicitly. **Not enforced:** automatic cleanup on team
  removal and the audit actor name for a deleted `team_members` row (resume shows `actor:null`).

## Deletion and retention (010)
- `delete` is a **soft delete**: state `deleted`, `deleted_at`, `purge_after = now + retention_days`
  (per-session `retentionDays`, default 30 or env `HARNESS_SESSION_RETENTION_DAYS`). The session
  disappears for non-admins immediately. Runs are never deleted or modified.
- `purge` (admin only, `POST /{id}/purge`) requires `deleted` and `purge_after` elapsed (409
  `RETENTION_NOT_ELAPSED`). It deletes the `session_members` rows (names, identities), blanks title
  and description, sets `purged_at`, and **keeps** the tombstone row, the `session_runs` links and
  the audit log. Keeping the links is deliberate: a queued run of a purged session still cannot
  start. Runs and their events are retained untouched. Purge is idempotent.
- There is **no scheduler**: nothing purges automatically when `purge_after` passes. An operator
  or a later job calls `purge`. `unlink` is the way to detach a run from a deleted session.
- Audit rows are append-only and are not erased by purge.

## Concurrency
State changes are compare-and-set on the current state. Link and complete/delete serialize on the
session row: `link` updates the row guarded by `state='active'`; `complete`/`delete` flip the state
first, then check linked runs and roll back if one is live. Tested on sqlite with separate
connections; see the evidence file for the limits of that test. Edits use `version` (optional
`expectedVersion`, 409 `VERSION_CONFLICT`). Create accepts `clientRequestId` so a retried POST
returns the first session (200) instead of a duplicate.

## Audit actions
`session.created|updated|state_changed|member_joined|member_left|member_removed|run_linked|
run_unlinked|template_saved|template_applied|resumed|deleted|purged` from the API (state changes
carry `before`/`after` and `reason`) and `session.run_refused` from the section4 gate.
Detail carries ids and states only, no secrets.

## Not wired by this lane
The router is not registered in `app.py` and the openapi/policy classification is the lead's.
Errors use the stable envelope `{"detail": {"error": {"code", "message"}}}`.
