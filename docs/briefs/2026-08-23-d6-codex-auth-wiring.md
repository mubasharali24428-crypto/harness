# TASK: Wire bearer authentication into Sections 5, 6, 8 (defect D6 / plan item P0.6)

You are working in the AI Engineering Harness monorepo at the current directory
(`/Users/user/untitled folder 2/harness`). Read `AGENTS.md` first: `contracts/`
is FROZEN, every section imports ONLY `harness_contracts`, schema changes need a
new numbered migration (you will NOT change any schema), tests must pass before
any hand-off.

## Project context (what you need to know)

- Python >=3.11 FastAPI microservices, one folder per section, each with its own
  `.venv`. Postgres event store; `agent_events` is the source of truth.
- Auth model: Section 1 mints bearer sessions (`auth_sessions` table). The shared
  dependency factory `make_bearer_dep(session_factory)` lives in the FROZEN
  package at `contracts/src/harness_contracts/fastapi_auth.py`. It returns a
  `Principal(userId, teamId, role)` (see `contracts/src/harness_contracts/roles.py`)
  or raises HTTP 401. Role permissions helper: `can(principal, action)` with
  actions "read" | "write" | "approve".
- Deploy assembly: `deploy/runners/services.py` builds one FastAPI app per service
  (`app_sandbox`, `app_meeting`, `app_review`). Section 4's `app_orchestrator`
  already shows the correct pattern (F2, fail-closed):
      app.state.get_principal = make_bearer_dep(factory)
  and each router resolves auth through a `get_principal(request)` dependency
  that hard-fails with 503 AUTH_NOT_CONFIGURED when nothing is wired.
- Frontend (section7/src/lib/api.ts) ALREADY sends `Authorization: Bearer ...`
  on every call to S6 and S8. It still puts legacy `userId` /
  `reviewer_user_id` fields in bodies — keep accepting those body models for wire
  compatibility, but STOP trusting them for identity.

## The defect you are fixing (verified today)

1. **Section 5** (`section5/src/harness_sandbox/api.py`): all four routes
   (`POST /api/v1/sandbox/tasks`, `/tasks/{id}/exec`, `/tasks/{id}/edits`,
   `/tasks/{id}/destroy`) are completely UNAUTHENTICATED — anyone who can reach
   :8005 can execute commands in the sandbox.
2. **Section 6** (`section6/src/harness_meeting/api.py`): completely
   UNAUTHENTICATED and takes `userId` from the request BODY (forgeable) for
   messages, AI invokes, and brief approve/reject.
3. **Section 8** (`section8/src/harness_review/api.py`): `make_app` creates
   `get_principal = make_bearer_dep(session_factory)` and stores it on
   `app.state`, but NO route depends on it. `_resolve_reviewer()` reads
   `request.scope.get("_principal")` — a key nothing ever sets — so it silently
   falls back to the spoofable `x-user-id` header / `"unknown"`.
   Approve/request-changes take `reviewer_user_id` from the body (forgeable).

## What to do

### Section 8 (do this first — smallest)
- Add a `get_principal` route dependency (FastAPI `Depends`) to ALL `/reviews/*`
  routes so requests without a valid bearer token get 401. Follow the same
  fail-closed shape S4 uses: if `app.state.get_principal` is missing => 503,
  never an open endpoint. Note the module-level `router` is shared by both
  `make_app(...)` and deploy's own app construction — resolve the dependency via
  `request.app.state.get_principal` inside a small helper (mirroring
  `section4/src/harness_orchestrator/api.py::get_principal`) rather than closing
  over one factory.
- `_resolve_reviewer`: return the resolved principal's `userId`; DELETE the
  `x-user-id` header fallback (keep behavior honest: no principal => the route
  never ran, because auth runs first).
- `/approve` and `/request-changes`: use the PRINCIPAL's userId as the acting
  reviewer. Ignore `reviewer_user_id` from the body for identity (leave the
  field in the pydantic models so old clients don't break; optionally echo which
  identity was used in the response).

### Section 6
- Same fail-closed auth dependency pattern on ALL `/api/v1/meetings*` routes
  (401 without token, 503 when auth unwired).
- Identity: wherever a route currently does `_service.xxx(meetingId, req.userId, ...)`,
  pass the principal's `userId` instead of `req.userId`. Keep the request models
  unchanged (extra field tolerated) EXCEPT where the body value would now be
  ignored — in that case reject mismatches? NO: simply prefer the principal and
  ignore the body value; do NOT add mismatch 4xx logic (keep the diff minimal;
  frontend always sends the session user anyway).
- `create_meeting`, `generate_brief`, `generate_summary`, GET routes: they take
  no userId today — just add auth, nothing else.

### Section 5
- Same fail-closed auth dependency on all four task routes (401/503 semantics).
  No body changes needed. Role check: use `harness_contracts.roles.can(principal,
  "write")` and return 403 ROLE_DENIED for viewer/guest roles on all four routes
  (executing code is a write action). Keep handlers sync/async exactly as they are.

### Deploy assembly (`deploy/runners/services.py`)
- `app_sandbox`: set `app.state.get_principal = make_bearer_dep(factory)` (a
  factory already exists there).
- `app_meeting`: currently uses `simple_app(router, ...)` with NO factory. Build
  the app like `app_review` does: create `factory = get_factory()`, attach
  `make_bearer_dep(factory)`, include router, keep a `/health` endpoint open.
  Do not break the `meeting_app = app_meeting` uvicorn alias at the bottom.

## Tests you must add/update (each section: `./.venv/bin/python -m pytest tests/ -q`)

- S5 conformance test (`section5/tests/conformance/test_sandbox_conformance.py`)
  drives the router via TestClient with NO auth wiring: update it to override the
  auth dependency with a stub Principal (role ADMIN, teamId "team-golden") the
  same way `section4/tests/conformance/test_orchestrator_conformance.py` does via
  `app.dependency_overrides[...]`. Add one new test asserting an unwired app
  rejects with 503/401, and one asserting a VIEWER-role principal gets 403.
- S6 conformance test: same treatment — override auth with a stub principal whose
  userId is e.g. "u1"; existing body fields can stay in the JSON payloads. Add:
  unauth -> 503/401 test; and a test proving a POSTED message is recorded under
  the PRINCIPAL's userId even when the body sends a different userId.
- S8: there are no route-level tests today (only unit tests for service logic).
  Add a small `section8/tests/unit/test_api_auth.py`: build `make_app` with a
  dummy sqlite/aiosqlite session_factory if practical, else monkeypatch
  `app.state.get_principal`; assert (a) no-token -> 401/503, (b) with overridden
  principal, approve records the principal's userId (inspect via the service
  stub or response), (c) `x-user-id` header alone no longer works.
- Do NOT touch anything under `contracts/`, `section4/`, `section7/`,
  `integration/`, or CI files. Do NOT modify request/response schemas beyond what
  is described. No reformatting of untouched lines.

## Environment facts

- Run tests per section: `cd sectionN && ./.venv/bin/python -m pytest tests/ -q`
  and lint: `./.venv/bin/ruff check src tests` (line-length 100, ruff rules E,F,I,UP,B).
- macOS host; venvs already installed. If aiosqlite is missing in a venv you need,
  install it into THAT venv: `./.venv/bin/pip install 'aiosqlite>=0.20'`.
- NEVER print or export API keys. No network calls needed.

## Deliverable

Do NOT `git commit` — leave changes in the working tree. Finish with: list of
files changed, full pytest/ruff results for sections 5, 6, 8, and anything you
intentionally did NOT do.
