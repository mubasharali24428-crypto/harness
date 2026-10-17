# S1-R01 — Auth / Authz / Tenant Isolation

Status: in progress (plan approved; implementation underway)
Owner: pilot remediation Session 1

## Sub-risks covered
- R-01a S3 model router: no auth on any route (incl. /config, /spend).
- R-01b S5 sandbox: no tenant scoping; principal never checked vs task owner.
- R-01c S2 github: repo resolved by project_id only; no team filter on read/write paths.
- R-01d S6 create_meeting unscoped; S8 approve/request-changes no role gate.
- R-01e (demoted) OTP rate limit: already wired in deploy; residual documented in register.

## Threat model
Attacker = authenticated user of team B (or unauthenticated caller for S3).
- T1: unauthenticated caller hits S3 /call → model spend on the operator's keys; /config → repoints roles to attacker-controlled models (key exfil via BYO-key plumbing); /spend → cross-team cost data.
- T2: team B calls S5 exec/edit/destroy/tree/checkpoints with team A's taskId/projectId → code execution inside A's workspace, data read, workspace destruction.
- T3: team B calls S2 with A's projectId → reads private repo tree/files, pushes branches, opens PRs, reads PR diffs (also via S8's internal diff_provider with user "system").
- T4: team B's user approves a PR / requests changes on A's PR (no role check, no scope); creates a meeting pinned to A's projectId (whence briefs/transcripts flow into A's run pipeline).
Assets: model-provider keys + spend ledger, workspaces, repos/PRs, review state, meeting/brief content.

## Fix design (mechanism per surface)

Uniform fail-closed rule: **when a dependency needed to make the security decision is unwired, the route answers 503 — never "allow".** Mirrors existing D6/P0.6 auth pattern (`getattr(app.state, …) is None → 503`).

### S3 (`section3/src/harness_router/api.py`)
- Add fail-closed `get_principal` (app.state.get_principal) + role deps via `harness_contracts.roles.can`.
- /call → Writer. /spend → Reader, forced to `principal.teamId` (no free-form cross-team query). /estimate-cost, GET "", /catalog → Reader. /config → Writer (the frozen role vocabulary has no "admin" action; team-scoped "write" is the correct gate). /metrics stays public (prometheus scrape; noted residual R-13).
- Deploy: `app_models` wires `app.state.get_principal = make_bearer_dep(factory)`.
- Rollback: revert route dep params; wiring line removal degrades to 503 (safe).

### S5 (`section5/src/harness_sandbox/{api.py,tree_api.py,local_runner.py,service.py}`)
- Task-keyed routes (exec/edit/destroy): task record carries `team_id` bound at create from principal; foreign team → 404 (no oracle).
- Project-keyed routes (create-with-projectId, tree, checkpoints): resolve project→team via `app.state.project_team_lookup`; mismatch/unknown → 404; lookup unwired → 503.
- `local_runner.create_task` / `service.run_task` gain optional `team_id` stored on the task record.
- Deploy: `app_sandbox` wires lookup (one SQL: `SELECT team_id FROM projects WHERE id=:pid`) + bearer (already wired).

### S2 (`section2/src/harness_github/{store.py,service.py,app.py}`)
- `get_repo_full_name(db, project_id, team_id)` → `WHERE id=:id AND team_id=:team`.
- All service methods gain required keyword `team_id`; routes pass `principal.teamId` (bearer already wired in S2).
- `run_pr_id` route: join projects, filter `team_id` of principal.
- Cross-section caller S8: diff_provider gains `team_id` arg → `getPRDiff("system", …, team_id=…)`.
- Tests: service tests gain team kwarg; seeds already carry `team_id='t1'`.

### S6 (`section6/src/harness_meeting/api.py`, service)
- create_meeting: project_team_lookup required — mismatch/unknown → 404; unwired → 503. `team_id` bound onto the meeting.
- Reads stay as-is this session (meetingId-keyed, UUIDs; residual noted).

### S8 (`section8/src/harness_review/{service.py,store.py,api.py}`)
- Role gate: approve/request-changes require `can(principal, "approve")` → else 403.
- Team scoping: `get_pull_request_record(db, pr_id, team_id=None)` adds `JOIN projects … AND team_id=:t` when provided; service methods take optional `team_id` and verify; routes pass `principal.teamId` → foreign PR reads as 404.
- diff_provider + getPRStatus calls inside get_review_data/evaluate_verdict threaded with the PR's team (`_project_team` SQL helper resolves it when the route didn't).
- All record-touching routes scoped, including comments (list/add), risks, deep-link, verdict. Controller catches `LookupError` → 404 uniformly.

## Tests (positive + negative per section)
- S3: 401 unauth /call,/spend,/config; 403 reader→/call and /config; /spend ignores foreign teamId (locked to principal); /metrics 200 unauth.
- S2: service reads/writes with wrong team → LookupError; route-level 404 on foreign project; run_pr_id foreign → 404.
- S5: foreign taskId exec/edit/destroy → 404; foreign projectId create/tree/checkpoints → 404; lookup unwired → 503.
- S6: foreign project create → 404; unwired → 503; same-team → 201.
- S8: viewer approve → 403; foreign pr_id reads → 404 (service + route).

## Rollback
Per-section revert only; no migrations, no schema changes, in-memory stores, additive kwargs. Deploy wiring rides existing settings; removing wiring lines yields fail-closed 503, not open.

## Residuals (explicitly out of pilot-blocking scope)
- S6 meeting reads not team-checked (UUID-keyed; register R-01d scoped to create path). NOTE: create is now team-scoped; get/messages remain session-2 tenant-pass work.
- S5 service-direct (non-HTTP) callers can create tasks with no team binding — in-process only.
- S3 /metrics public (prometheus); unauthenticated route enumeration on S3 remains (R-13).
- S10→S2 run-flow service calls use team from run context (checked during S2 edits).

## Verification results (R-01 COMPLETE — all five surfaces)
Commands run (each in the section's venv, `.venv/bin/python -m pytest tests/ -q` + `./.venv/bin/ruff check src tests`):
- section2: 32 passed, ruff clean (S2 team-scoped reads/writes; new `tests/test_s1_r01_tenant_scope.py`).
- section3: 95 passed, ruff clean (S3 bearer deps + role gates; new `tests/unit/test_s1_r01_authz.py`).
- section4: 465 passed, 5 skipped, ruff clean (metering tests re-wired to stub the new S3 principal dep — 3 failures were the fail-closed 503 firing as designed, fixed by stubbing auth).
- section5: 162 passed, 1 skipped (pre-existing landlock-only skip), ruff clean (S5 task/team scoping; new `tests/unit/test_s1_r01_authz.py`; tree/checkpoint suites re-wired with project_team_lookup stubs).
- section6: 104 passed, ruff clean (S6 create_meeting gated + team bound; new `tests/unit/test_s1_r01_authz.py`; 8 test helpers re-wired with lookup stubs).
- section8: 86 passed, ruff clean (S8 role gate + full PR/comment scoping; new `tests/unit/test_s1_r01_authz.py`; conftest fakes + test_team_thresholds lambda take the new 3rd provider arg).
- section1 628 passed / section10 65 passed / section11 50 passed (unaffected, re-run green).
Deploy wiring (deploy/runners/services.py):
- `app_models`: `app.state.get_principal = make_bearer_dep(get_factory())` added (required — S3 routes fail closed without it).
- `app_sandbox`: `app.state.project_team_lookup` (SELECT team_id FROM projects WHERE id=:pid).
- `app_meeting`: same lookup wired.
- `app_review`: `diff_provider(project_id, pr_number, team_id=None)` → `getPRDiff("system", …, team_id=team_id)`.
No commits made (working tree left dirty on purpose — review pending).

Verification notes:
- Contract additions kept within frozen vocab: `can()` has no "admin" action; /config gated with "write".
- Role strings in tests must match contract values exactly ("engineer", not "engineering") — caught by S8 negative test.
- Oracle-free behavior asserted in S5: foreign task errors are byte-identical to missing-task errors (same status/envelope/message shape).
