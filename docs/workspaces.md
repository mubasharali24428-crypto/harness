# Workspaces (master prompt part 39)

Code: `section1/src/harness_identity/workspaces.py` (router, `/api/v1/workspaces/...`),
migration `section1/migrations/0040_workspaces.sql`, run gate
`section4/src/harness_orchestrator/workspace_gate.py`. Everything below is covered by
`section1/tests/test_workspaces_*.py` and `section4/tests/unit/test_workspace_gate.py`
unless marked **not enforced**.

## Model
A workspace belongs to one team (`workspaces.team_id`), has an owner, members, versioned
settings, visibility, data residency and a lifecycle state. `projects.workspace_id`
(nullable) links a project; a project with no workspace behaves exactly as before.

**Naming (001):** 2-80 chars, `[A-Za-z0-9 ._-]`, starts alphanumeric. Unique per team,
case-insensitive, among non-deleted workspaces (partial unique index). Another team may
reuse a name; a deleted workspace frees its name.

**Ownership / membership:** the creator becomes `owner` (workspace roles `owner|member|viewer`).
Members must already be members of the owning team (else 422 `NOT_A_TEAM_MEMBER`). The last owner
cannot be removed or demoted (409 `LAST_OWNER`). Creating and mutating needs team role admin or
engineer; mutating also needs workspace owner or team admin.

## Repository access is not collaboration access (002)
Linking a project (`POST /{id}/projects`) only sets `projects.workspace_id`. It never inserts a
`workspace_members` row, and workspace membership never changes the project's repository fields.
Tested: after linking, a teammate who is not a workspace member still gets 404 for a private
workspace and its project list. Project links need a project of the same team (foreign project:
404) and a project belongs to at most one workspace (409). **Not enforced:** this lane does not
change what the existing project/GitHub routes allow; it only keeps the two concepts separate.

## Approved bindings (003)
`workspace_binding_allowlist` holds approved `repository` / `environment` values, managed by team
admins (`/allowlist`). Adding a binding (`POST /{id}/bindings`) is denied with 403
`BINDING_NOT_APPROVED` unless the exact (kind, value) is on the allowlist of the team or, when
`teams.org_id` is set, of its organization. Empty allowlist = nothing can be bound. Revoking an
allowlist entry does not delete existing bindings; `GET /{id}/bindings` reports `approved:false`.
**Not enforced:** org-level (non-team) admins managing the org allowlist; project linking does not
check the allowlist.

## Settings with history (004)
Closed schema: `description`, `default_branch`, `max_concurrent_runs` (1-50),
`require_plan_approval`. `PUT /{id}/settings` is a merge patch (null removes a key). Each real
change writes a `workspace_settings_versions` row (version, before, after, actor), bumps
`settings_version` by compare-and-set, and audits `workspace.settings_changed`. A no-op change
creates nothing. `expectedVersion` gives optimistic concurrency (409 `VERSION_CONFLICT`).
`POST /{id}/settings/revert {toVersion}` writes a new version equal to the old one (history is
never rewritten). **Not enforced:** the orchestrator does not read these settings yet; they are
recorded policy only.

## Templates (005)
Team admins save templates (`POST /templates`); each save of a name is a new immutable version.
Defaults are limited to `visibility`, `dataResidency`, `tags`, `settings` and validated by the same
rules as direct input. Applying one (at create via `templateName`, or `POST /{id}/apply-template`)
stores `template_name`/`template_version` on the workspace, tags the resulting settings version
row, and audits `workspace.template_applied` with the version.

## Visibility and data residency (006)
Visibility is explicit: `private` (workspace members + team admins), `team` (default; whole team),
`org` (whole team, plus read-only `GET` for teams with the same `org_id`; requires the team to be in
an organization). Data residency is required at create (or from a template) and must be in
`HARNESS_WORKSPACE_ALLOWED_RESIDENCY` (default `us,eu,uk,ap,ca`), stored lowercase.
**Not enforced:** residency is a validated, audited label. Nothing moves or pins data to a region.

## Lifecycle (007, 008)
| action  | allowed from        | result   | who                    |
|---------|---------------------|----------|------------------------|
| pause   | active              | paused   | owner / admin          |
| archive | active, paused      | archived | owner / admin          |
| restore | paused, archived    | active   | owner / admin          |
| delete  | archived            | deleted  | team admin + reason    |

Repeating an action already satisfied is a 200 with `changed:false` and no new audit row.
Anything else is 409 `ILLEGAL_TRANSITION` (delete from active/paused: 409 `WORKSPACE_NOT_ARCHIVED`).
`deleted` is terminal. Transitions use a compare-and-set UPDATE so concurrent transitions lose
cleanly. Each audits `workspace.paused|archived|restored|deleted` with before/after state and reason.
Paused/archived workspaces reject settings, member, binding, link and metadata edits (409
`ILLEGAL_STATE`); unlinking a project stays allowed so an admin can lift a block.

**Soft delete / retention:** delete keeps the row, sets `deleted_at` and `purge_after`
(= +30 days, `RETENTION_DAYS`), hides it from everyone but team admins, and frees the name.
**Purge:** the orchestrator job `workspace-purge` (`HARNESS_WORKSPACE_PURGE_SECONDS`, default 3600,
0 turns it off) scrubs a `deleted` workspace once `purge_after` has passed: members, bindings and
settings history are deleted; name (`[purged]`), tags, settings and reason are cleared. The row stays
as a tombstone, so its linked projects stay blocked until unlinked. Audited as `workspace.purged`.
Workspace templates are team-owned and are not touched.

**Run gate:** `workspace_gate.py` reads the project's workspace state from the store on every call.
Non-active -> HTTP 409 `WORKSPACE_NOT_ACTIVE` plus a `workspace.run_refused` audit row. It runs in
`OrchestratorService.start_execution` (covers `/runs/{id}/start` and the plan-gate start) and in
`_dispatch_claim` (covers single and batched queue claims), so a run queued before the archive is
dropped at claim. If the workspaces schema is absent (pre-0040 stores) the gate allows and logs.
A `resume` that would respawn a non-terminal run is a new start and gets the same 409; resuming a
finished run stays a silent no-op.
**Running runs:** the orchestrator sweeper (`HARNESS_WORKSPACE_SWEEP_SECONDS`, default 30, 0 off) stops
runs that still hold a `running` claim in an `archived` or `deleted` workspace (terminal stop, audited
`workspace.run_stopped`). A `paused` workspace only blocks new starts and resumes; its running runs are
not stopped. A run can keep executing for up to one sweep interval after the archive.

## Search (009)
`GET /api/v1/workspaces?q=&state=&template=&tag=&limit=&cursor=`: always scoped to the caller's
team; private workspaces only for members and admins; deleted only for admins and only when
`state=deleted`. `q` is a case-insensitive substring of the name (LIKE wildcards escaped). Paging is
keyset by (name, id), `limit` 1-100 (default 25), `nextCursor` is null at the end. A cursor from
another team yields an empty page. Org-visible workspaces of sibling teams are NOT listed.

## Limits and quotas (010)
- Workspaces per team: `HARNESS_MAX_WORKSPACES_PER_TEAM` (default 50) counting every non-deleted
  workspace; exceeded -> 409 `QUOTA_EXCEEDED`. Creates of one team are serialized by a no-op write on the team
  row before the count (row lock on PostgreSQL, write lock on SQLite), so simultaneous creates cannot
  overshoot (`test_concurrent_creates_cannot_overshoot_the_quota`: 8 racing creates, quota 3, exactly 3
  succeed). Not run on PostgreSQL.
- Tags: 20 per workspace, 32 chars each. Name 80 chars. Settings description 500 chars.
- Page size 100; settings history 200.

## Isolation guarantees (what tests prove)
- A workspace of another team (not org-visible) answers 404 on every route, identical to an unknown
  id; search never returns foreign rows, including through the cursor.
- A private workspace answers 404 to non-members of the team and of the workspace.
- Cross-team org readers can only GET; every mutation answers 404.
- Deny by default: no allowlist, no binding; viewers cannot create or mutate; non-owners get 403.
- Every consequential change writes an audit row via the shared audit writer; audit failure never
  breaks the request.
