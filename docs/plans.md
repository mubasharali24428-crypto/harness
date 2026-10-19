# Task statement and versioned plans (part 44, directives 001-003)

As built. Code: `section4/src/harness_orchestrator/plans.py` (router, `configure_plans(service)`). Schema:
`section1/migrations/0047_plans.sql`. Tests: `section4/tests/unit/test_plans.py`, `test_plans_edits.py` (fixture:
`test_plans_support.py`). Execution, completion and retry (directives 004-010) are a different lane (`docs/execution.md`).

## 1. Shape

* **Task spec** (`task_specs`, one per run): `statement` (1..4000), `successCriteria` (<= 20 items of <= 500), `constraints` (<= 20 of
  <= 500), `ownerUserId` (a human member of the run's team, or none), `version`. Every save adds a `task_spec_revisions` row.
* **Plan version** (`plan_versions`): immutable `content` plus its `content_sha256`; only `state` and the approval fields ever move.
  Content: `goal`, `steps`, `tests`, `risks`, `filesLikelyToChange`, `forbiddenPaths`, `expectedResult`, `estimatedCostUsd` (the fields of the
  legacy `AgentPlan`). Bounds: goal and expectedResult <= 4000 chars, steps <= 100 x 2000, tests and risks <= 50 x 1000, paths <= 200 x 512
  (relative, no `..`, no backslash, no NUL), cost 0..1,000,000, whole plan <= 64 KB (`422 PLAN_TOO_LARGE`), no unknown keys.
  The digest is the sha256 of the canonical JSON (fixed key order, sorted keys, cost rounded to 6 decimals), so the same plan always has
  the same digest. States: `draft`, `proposed`, `approved`, `superseded`, `rejected`. A run keeps at most 100 versions.
* **Comment** (`plan_comments`) on one version: body 1..4000, optional `fieldPath` (`goal`, `steps/2`, ...), <= 500 per version. Comments are
  never edited or deleted here and are never agent input.
* **Proposed edit** (`plan_edits`) against one version: a patch of <= 20 ops, `pending` -> `accepted` | `rejected`; <= 50 pending per version.

## 2. Routes (prefix `/api/v1/orchestrator`)

| Route | Permission | Notes |
|---|---|---|
| `GET runs/{runId}/task[?version=n]` | read | version 0 and `exists:false` when never set |
| `PUT runs/{runId}/task` | steer | `expectedVersion` required (0 for the first save) |
| `GET runs/{runId}/plan/versions` | read | metadata, counts, `latestVersion`, `locked`, `approvedVersion`; no content |
| `POST runs/{runId}/plan/versions` | steer | `content`, `expectedVersion` (the latest number you saw, 0 if none), `requestId` |
| `GET runs/{runId}/plan/versions/{n}` | read | content, digest, author, state, `can` flags for the caller |
| `GET runs/{runId}/plan/versions/{n}/diff?against={m}` | read | `against` defaults to n-1; version 1 needs it (`422 NO_BASE`) |
| `GET\|POST .../versions/{n}/comments` | read \| comment | keyset paged (`limit` 1..100, `after=<comment id>`, `nextCursor`) |
| `GET\|POST .../versions/{n}/edits` | read \| comment | proposing an edit needs only `comment` (viewers may, guests may not) |
| `POST .../versions/{n}/edits/{editId}/decide` | approve | `{decision: accept\|reject, note}` |

Roles are checked before the run (no new existence oracle for low roles); a refused role leaves one `authz.denied` audit row.
`approve` is admin or engineer. A foreign or unknown run, and every sub-resource of one, answers the same `404 NOT_FOUND`;
within a visible run an unknown version is `404 VERSION_NOT_FOUND` and an unknown edit `404 EDIT_NOT_FOUND`. Run routes also obey the pilot
allowlist. Creates return 201; a replay by `requestId` returns 200 with the original.

## 3. The legacy plan is version 1 (lazy mirror)

The planner and the legacy routes (`POST runs/{id}/plan`, `plan/approve`, `plan/reject`, `GET runs/{id}/plan`) are untouched. On every request
to the routes above, unmirrored `agent_plans` rows of the run are copied, oldest first, into `plan_versions` (`source: "legacy"`,
`legacyPlanId`, author `agent/planner`, the legacy `created_at`); a unique index on `source_plan_id` makes it happen once even for
concurrent first readers. A mirrored version then **follows the legacy status**: `approved` -> state `approved`, `approvedSha256` = its digest and
`approvedAt`; `rejected` -> `rejected` (and a legacy approve-then-reject clears the approval). Mirroring and status following write no audit
row of their own (the legacy decision already did); they are derived state.

## 4. The approval lock (`PLAN_LOCKED`)

While any version is `approved` the plan is locked: a new version, a comment, an edit proposal and an edit decision are all refused with
`409 PLAN_LOCKED` (`approvedVersion` in the body). Reads keep working and pending edits show `stale: true`. When a plan becomes approved every
other open (`draft`/`proposed`) version becomes `superseded`, because only the approved content may run. The lock is checked inside the
same transaction after syncing the legacy status, so a legacy approval that has just landed is honoured. The approval binds the digest:
`approvedSha256` equals the content digest of the approved version, and that content cannot change.

## 5. Versions, concurrency, idempotency

* `POST versions` needs `expectedVersion` = the latest number; anything else is `409 VERSION_CONFLICT` with `currentVersion`. A unique
  `(run_id, version)` constraint decides simultaneous creators (one wins). Creating a version supersedes older `draft`/`proposed` ones.
* `requestId` (<= 64) makes a retried create, comment or edit proposal one row, per author.
* `PUT task` is compare-and-set on `expectedVersion`; saving identical values again is a no-op (no new version, no audit row).

## 6. Reviewing: comments and proposed edits (directive 003)

An edit is `patch`: ops `{op, path, value}` applied in order to a copy of the version's content (later ops see earlier list changes):
`replace` a whole field (`/goal`, `/steps`, `/estimatedCostUsd`), or `replace`/`add`/`remove` one list item (`/steps/2`, `/steps/-` appends;
`add` at an index inserts). Anything else is `422 BAD_PATCH`. The result must be a valid plan (`422 BAD_PLAN`, says where, never echoes the value)
and different from the base (`422 NO_CHANGE`); the patch is checked when proposed and again when accepted, and is stored (<= 64 KB, the `value` of a
`remove` is dropped).

* Propose: only on the **latest open** version (`409 VERSION_NOT_OPEN` otherwise).
* Decide: needs `approve`. Nobody accepts their own proposal (`403 SELF_APPROVAL`, audited as `authz.denied`; `HARNESS_ALLOW_SELF_APPROVAL=1` is the
  same single-person-team opt-out as everywhere); withdrawing your own (reject) is allowed. **Accept** creates the next version (`fromEditId`,
  `parentVersion`, author = the decider), supersedes the base and records `resultingVersion`. An edit whose base is no longer the latest open
  version cannot be accepted (`409 STALE_EDIT`, `currentVersion`) but can still be rejected. The decision is a compare-and-set on `state = pending`, so
  simultaneous deciders make one decision: a repeat by the same person with the same decision is a 200 replay, anything else is
  `409 EDIT_ALREADY_DECIDED`.

## 7. Audit and events

Audit (`target_type agent_run`, `target_id` = run id), detail = ids, versions, digests and before/after states only, never task, plan or comment text:
`task.updated` (version, changed field names, owner id), `plan.version_created`, `plan.comment_added`, `plan.edit_proposed`, `plan.edit_decided`.
Each also writes a durable collaboration event of the same kind (`run_collab_events`, payload ids only) so open clients refresh.

## 8. Not covered, and limits

* **There is no route that approves a plan version.** Approval stays the legacy `plan/approve` on a `planId`; a version that was created natively
  (not mirrored) has no `agent_plans` row, so the legacy approve cannot name it and only the mirrored plan can be approved by the existing routes.
  The engine executes the in-memory legacy plan, not a `plan_versions` row. Binding "approve version n" to what runs needs a lead decision (see lane
  evidence).
* The task statement is not locked by plan approval; it is versioned and audited instead.
* The task statement and plan versions are not read by the planner or the engine yet.
* Diffs are structural (field and list-item level, `difflib` opcodes), not word level.
* JSON columns are text so one migration loads on PostgreSQL and SQLite; PostgreSQL behaviour is not exercised by these tests.
