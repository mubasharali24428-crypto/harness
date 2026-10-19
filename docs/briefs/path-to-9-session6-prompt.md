# Session 6 — Plans and execution, artifacts and version control, search and export (parts 44, 48, 50)

Prefix: `docs/briefs/stable-prompt-prefix.md`. Directives: `docs/master-prompts/harness-multiplayer-master-build-prompt.md` sections 44, 48, 50
(read only your section, plus `docs/briefs/PROJECT-STATE.md` section 6 "Facts that bite"). Target: each part at 9/10 = every directive implemented or
explicitly out of scope with a reason, invariant tests, an evidence file, docs that match, honest gaps.

## What exists (verify, do not trust)
- Plans today: `agent_plans` (migration 0001, `version` added by 0013), one plan per run proposed by the planner; `plan_gate.py` (propose / approve / reject /
  start-run routes), `planner.py`, legacy `GET /runs/{runId}/plan`, `POST runs/{runId}/plan/(approve|reject)`. States live in `state_machine.py`. Budgets: `budget.py`,
  `run_metering.py`, `cost_ledger`. Checkpoints: section5 + UI `runs/[runId]/checkpoints`. Done-signal diff bodies: `diffstore.py` (content-addressed, process-local disk).
  Staging / approvals bound to a digest: `staging.py`, `collab.py` (`high_impact_approvals`).
- Search today: repo-tree search in section7 only; `runs_index.py` lists runs; `discussion.py` has `GET discussions/search` (LIKE scan over comments only).
  Exports today: `GET /api/v1/audit/export` (section1) and `GET .../comments/export` (discussion).
- Session 4/5 landed sessions (`collab_sessions`, `session_runs`), presence, control, agents, discussion (`comments`), notifications, realtime. Read `docs/discussion.md`
  (it is the best style model for this session: team-scoped, no existence oracle, stable error codes, ids-only audit), `docs/audit-and-events.md`, `docs/realtime.md`.
- Org policy gate (`contracts/.../policy_enforce.py`): the lead already classified every path below. Sections other than section1 may import only `harness_contracts`;
  section4 speaks raw SQL (`sqlalchemy.text`). All migrations are section1 files; next free numbers: **0047 PL, 0048 EX, 0049 AR, 0050 SE**.

## Lanes (the lead wires everything; lanes never touch shared files)
**PL** plans (44.001-003), **EX** execution (44.004-010), **AR** artifacts and version control (48), **SE** search, export, deletion requests (50). A UI lane **X** (section7)
runs after the backend lanes report. Each lane owns ONLY:

- **PL** (section4): migration `0047_plans.sql`, `section4/src/harness_orchestrator/plans.py` (exports `router`, `configure_plans(service)`), tests
  `section4/tests/unit/test_plans*.py`, `docs/plans.md`, `docs/briefs/evidence/s6/lane-pl.md`.
- **EX** (section4): migration `0048_execution.sql`, `section4/src/harness_orchestrator/execution.py` (`router`, `configure_execution(service)`), tests
  `section4/tests/unit/test_execution*.py`, `docs/execution.md`, `evidence/s6/lane-ex.md`. EX is the only lane that may edit `service.py` (add calls only, at most one per
  place, copying the shape of the `session_gate`/`workspace_gate` calls) so that execution limits and the abandoned/failed states are honoured at run start and queue claim.
- **AR** (section4): migration `0049_artifacts.sql`, `section4/src/harness_orchestrator/artifacts.py` (`router`, `configure_artifacts(service)`), optional helper module
  `artifact_store.py`, tests `section4/tests/unit/test_artifacts*.py`, `docs/artifacts.md`, `evidence/s6/lane-ar.md`. AR may extend `diffstore.py` additively.
- **SE** (section4): migration `0050_search_export.sql`, `section4/src/harness_orchestrator/search.py` and `exports.py` (each exports `router` and `configure_<name>(service)`),
  tests `section4/tests/unit/test_search*.py` / `test_exports*.py`, `docs/search-and-export.md`, `evidence/s6/lane-se.md`.

Forbidden for all lanes: `models.py`, `app.py`, `audit.py` (Session 6 audit names are reserved, see below), `policy_enforce.py`, `capability-matrix.md`, `Makefile`, `deploy/`,
`openapi.json`/sdk files, `plan_gate.py`, `collab.py`, `discussion.py`, anything under `section7/` (except lane X). Do not run docker, `make pg-*`, `run_smoke.py` or any
Playwright e2e (the lead does). Never `ruff check --fix` (only `--select I001 --fix` on a single file you own). Do not commit. Report paths, test counts, honest gaps.
Use `text()` SQL, no new ORM models. Sqlite tests that spawn engine tasks must await them before `engine.dispose()`. Quote the repo path; zsh has no `timeout`; never `sleep N && cmd`.
Keep your whole test run well under a minute; avoid sleeping tests (use a fake clock).

## Reserved audit actions (section1 audit writer rejects unregistered names)
task.updated, plan.{version_created,comment_added,edit_proposed,edit_decided}, subtask.{created,updated,blocked,unblocked,completed,evidence_added},
execution.{limits_updated,limit_exceeded,checkpoint_resumed}, completion.{reported,accepted,rejected}, run.{abandoned,retry_requested,retry_refused},
artifact.{uploaded,downloaded,deleted,quarantined}, change.{recorded,reviewed}, merge.{approved,refused}, base.stale_detected,
export.{requested,completed,refused,downloaded}, search.reindexed, deletion.{requested,approved,rejected,executed}.
Section4 writes audit rows raw (see `discussion.py` / `workspace_gate._audit_refusal`); detail carries ids and before/after state only, never plan text, comment text, file
contents, secrets or hidden reasoning.

## Paths each lane serves (already classified by the lead; use exactly these; prefix `/api/v1/orchestrator`)
- **PL**: `GET|PUT runs/{runId}/task`; `GET|POST runs/{runId}/plan/versions`; `GET runs/{runId}/plan/versions/{n}`; `GET runs/{runId}/plan/versions/{n}/diff?against={m}`;
  `GET|POST runs/{runId}/plan/versions/{n}/comments` (comment permission); `GET|POST runs/{runId}/plan/versions/{n}/edits` (propose = comment);
  `POST runs/{runId}/plan/versions/{n}/edits/{editId}/decide` (approve permission, accept|reject).
- **EX**: `GET|POST runs/{runId}/subtasks`; `PATCH runs/{runId}/subtasks/{subtaskId}`; `POST runs/{runId}/subtasks/{subtaskId}/(block|unblock|complete|evidence)`;
  `GET|PUT runs/{runId}/execution-limits`; `GET runs/{runId}/execution-status` (usage vs limits, checkpoints and the safe-resume answer);
  `GET runs/{runId}/completion`; `POST runs/{runId}/completion/report` (agent-reported: steer); `POST runs/{runId}/completion/(accept|reject)` (approve permission);
  `POST runs/{runId}/abandon` (reason required); `POST runs/{runId}/retry` (idempotent via `requestId`).
- **AR**: `GET|POST runs/{runId}/artifacts`; `GET|DELETE runs/{runId}/artifacts/{artifactId}`; `GET runs/{runId}/artifacts/{artifactId}/(content|preview)`;
  `GET runs/{runId}/changes`; `POST runs/{runId}/changes/{changeId}/review` (approve permission); `GET runs/{runId}/diff`; `GET runs/{runId}/git-state`;
  `PUT runs/{runId}/git-state` (record base/observed head, steer); `GET runs/{runId}/git-permissions` (who may commit, push, open a PR, merge, derived from the policy
  engine for the caller and the roster); `POST runs/{runId}/merge-approval` (approve permission; human merge approval, a record SEPARATE from plan approval);
  `GET runs/{runId}/lineage`.
- **SE**: `GET search?q=&kind=&projectId=&participantId=&status=&approvalState=&from=&to=&limit=&cursor=`; `GET search/status` (index watermark, delay, consistency statement);
  `POST exports` (export permission); `GET exports`; `GET exports/{exportId}`; `GET exports/{exportId}/download` (export permission);
  `GET|POST deletion-requests`; `POST deletion-requests/{id}/(approve|reject)` (administer).
If a directive needs a path outside these, say so in the evidence file instead of inventing it.

## Fixed interfaces between lanes (so they can be built in parallel; each owner defines these columns, others only read them)
- `plan_versions(id, run_id, team_id, version INTEGER, content JSON {goal, steps, tests, risks, filesLikelyToChange, forbiddenPaths, expectedResult, estimatedCostUsd},
  content_sha256, author_kind 'agent'|'user', author_id, state 'draft'|'proposed'|'approved'|'superseded'|'rejected', created_at)` and
  `task_specs(run_id PK, team_id, statement, success_criteria JSON, constraints JSON, owner_user_id, version, updated_by, updated_at)` (PL owns). PL lazily mirrors the legacy
  `agent_plans` row as version 1 (and records the approved version's digest) on first read; the legacy approve/reject routes and engine keep working unchanged. Plan edits and
  new versions are refused after approval (409 `PLAN_LOCKED`) — approval binds the content digest.
- `run_subtasks`, `run_execution_limits(run_id PK, max_seconds, max_model_calls, max_tool_calls, max_cost_microusd)`, `run_completion(run_id PK, agent_state, human_state
  'none'|'accepted'|'rejected', ...)`, `run_attempts` (EX owns). Agent-reported completion NEVER equals accepted completion: only `completion/accept` by an `approve` holder
  who is not the reporting agent sets accepted.
- `artifacts(id, team_id, run_id, name, media_type, size_bytes, sha256, storage_ref, kind 'upload'|'output'|'diff'|'log', state 'available'|'quarantined'|'deleted', scan JSON,
  created_by, created_at, retention_until)`, `run_changes(id, run_id, team_id, path, change_kind, before_sha, after_sha, plan_version INTEGER NULL, review_state
  'pending'|'approved'|'rejected', reviewed_by, reviewed_at)`, `artifact_lineage(id, run_id, from_kind, from_id, to_kind, to_id, relation, created_at)`,
  `run_git_state(run_id PK, repo_full_name, base_branch, base_sha, head_branch, observed_head_sha, stale, checked_at)`, `merge_approvals` (AR owns). AR may READ `plan_versions`
  and `run_completion` for lineage (table names above; if absent in its own tests create minimal DDL).
- `search_documents(id, team_id, kind 'run'|'session'|'comment'|'event'|'artifact'|'approval', source_id, run_id, session_id, project_id, workspace_id, status, approval_state,
  title, body, participant_ids JSON, created_at, indexed_at)`, `search_index_state`, `export_jobs`, `deletion_requests` (SE owns). SE READS comments, run events, runs,
  sessions, approvals and artifacts (metadata and names; text content only for non-quarantined small text artifacts) by raw SQL; if a source table is missing it is skipped, not an error.

## Requirements common to every lane
Deny by default; team-scoped reads; foreign or unknown resources answer 404 with the same envelope (no existence oracle); never trust client-supplied identity (use the
principal); idempotent retries where a client may repeat (`requestId`); every refusal a user can hit is a stable error code; optimistic `expectedVersion` where two people can
edit the same thing; docs in `docs/` describe behaviour as built including limits. Tests run on sqlite with the real migration SQL (look at how 0036/0037 and the Session 4/5
migrations and their test helpers handle Postgres-only statements; avoid `ADD COLUMN IF NOT EXISTS`). Add tests that FAIL when the invariant breaks (wrong team, wrong role,
stale version, races at least at the event-loop level, fake clock). Postgres-only behaviour (advisory locks, FTS indexes) must degrade cleanly on sqlite and be listed as untested-on-PG
in your evidence; the lead runs a live-PG test afterwards.

## Per-lane acceptance (nine-out-of-ten bar)
- **PL / 44.001-003**: task statement with success criteria, constraints, owner (versioned, audited); plan as immutable versions with content digest, listing, structured diff between
  versions; reviewers comment on a version; proposed edits (structured patch of plan fields) accepted or rejected by an `approve` holder, accepting creates a new version; everything
  refused after approval (`PLAN_LOCKED`); bounded sizes; the legacy plan appears as version 1.
- **EX / 44.004-010**: subtasks with dependencies (cycle refused), blockers, completion evidence references; execution limits for time, model calls, tool calls (and cost) that are
  checked at run start / queue claim and via `execution-status`, with exceeded limits refused or the run stopped with `execution.limit_exceeded` as far as the existing code allows —
  state plainly what is enforced and what is only recorded; checkpoint and safe-resume answer (read what section5/engine already persists); outputs reviewable before accepted;
  agent-reported vs human-accepted completion; abandon / fail with reason and preserved evidence (state machine vocabulary only, never delete evidence); retry that cannot duplicate
  side effects (requestId, attempt number, refusal while a previous attempt still holds the run or has un-reconciled side effects).
- **AR / 48.001-010**: documented repo / branch / worktree / run-workspace relationship (docs), changed files bound to run + plan version + review state, safe diff rendering
  (binary, large, generated, minified markers; never HTML-rendered; size caps), artifact upload / download / preview / retention under access control (content-addressed store,
  size caps, `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, no inline active content), stale-base detection and a clear rebase/merge-required state,
  concurrent branches without shared mutable state, commit/push/PR/merge permission answer, human merge approval separate from plan approval (a plan approver is NOT thereby a
  merge approver; the two records are distinct and tested), scanning/restriction of executable and untrusted content (magic-byte/extension classification, quarantine, no
  execution), lineage from task request through accepted result.
- **SE / 50.001-010**: index built only from data the current policy permits (no hidden/deleted/purged comments, no presence/ephemeral events, no secret-shaped keys, no hidden
  reasoning), authorization re-checked when serving each hit and snippet (a user who lost access gets nothing even if the index still has the row; the count is not an oracle),
  filters (project, participant, status, date range, approval state, kind), cursor paging, documented indexing delay and consistency (`search/status`), export jobs of session /
  run history and audit evidence in documented formats (JSON and NDJSON at least) with event ordering and actor identity, redaction of secrets and hidden reasoning and of anything
  the requester cannot see, configurable size limits with a stable `EXPORT_TOO_LARGE`, every export audited (`export.*`), deletion requests (user for own data, admin decision) with
  a documented list of what is deleted and which audit exceptions are retained and why; deletion also removes search documents and export files.
- **X / UI (lane run later by the lead)**: plan versions panel (list, diff, comments, edit proposals), task statement editor, subtasks, completion report / accept, abandon / retry,
  artifacts list / upload / download / safe preview, change review, lineage, search page, export dialog, deletion request — keyboard accessible, reusing the Session 5 announcer.
