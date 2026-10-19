# Lane P evidence (part 38, permissions and policy)

Files: `section1/src/harness_identity/policy.py`, `section1/migrations/0039_policy.sql`, `section1/tests/test_policy_engine.py`, `section1/tests/test_policy_api.py`, `docs/permissions.md`.

## Commands (from `section1/`)
- `./.venv/bin/python -m pytest tests/test_policy_engine.py tests/test_policy_api.py -q` -> 46 passed (34 pure engine, 12 API).
- `./.venv/bin/python -m pytest -q` (whole section1, all lanes present at the time) -> 765 passed, 26 skipped.
- `./.venv/bin/ruff check src tests` -> clean (no `--fix`; `ruff format` was run on my three files only).

## Covered by tests
Table-driven precedence (19 cases), 300-iteration randomized determinism / input-order independence / non-overridable-deny-wins, escalation denied (custom role, assignment, emergency approval, agent delegation), emergency window with a fake clock (engine and API, including lazy `emergency.expired`), step-up required / not required / stale / bad code / real OTP path, preview equals real evaluation, decision log rows for allow and deny with bounds, cross-org 404 isolation, migration-vs-table drift check.

## Step-up verifier
Default verifier re-uses the existing OTP mechanism through `auth.verify_otp` without touching app.py (a 409 from it, multi-team user, is treated as success because the code was already validated and consumed; the throwaway AuthSession it mints is deleted). Pluggable via `app.state.stepup_verifier`; tests use a double plus one test with the real OTP path.

## Honest gaps
- The router is not mounted: the lead must `app.include_router(policy.router)` in app.py. Postgres migration 0039 was not run (no docker); on sqlite the tables come from `ensure_schema` (create_all).
- Server-side paths that do NOT yet call the engine (still legacy `Role.ADMIN` / `roles.can` checks): app.py admin routes (lines ~546, 584, 681), billing.py, invites.py, teams_api.py, settings_api.py, sso.py, rules_api.py, api_keys.py, orgs.py (own checks), all section4 run/plan/approve/steer paths (`authz.py require_role`), audit/event export, collab comment/steer endpoints, section4 `workspace_gate`. Reads are not evaluated anywhere outside `/policy/...`. Part 38.005 ("every read and mutation") is therefore met by the integration function `authorize()` only, not by wiring.
- Step-up is enforced only on this lane's own mutations; export and the existing role-change (teams_api PATCH) endpoints do not check `stepup_required` yet.
- Chain construction is the caller's job: `authorize` verifies the chain starts at the given org but not that a team/project/workspace/run id really belongs to that org (no resolver; `teams.org_id` may not exist). Assignment and emergency grants at lower levels evaluate a two-node chain (org + node), so intermediate-level policies are not consulted at grant time.
- Users who are team members but not `org_members` are denied by `authorize` (default deny); there is no backfill from team_members to org membership.
- Role bindings use `team_members` for team nodes only; project/workspace/run roles exist only through `policy_assignments` (no endpoint to delete or list assignments).
- No UI, no metrics, no purge job for the decision log (retention is documented only). Preview of decisions does not write the decision log, but the admin gate on the preview call itself is logged.
- Anti-escalation for invitations/handoffs: `missing_for_grant` exists and is used by assignments, roles and emergency approval; `invites.py` and run handoff code do not call it yet.
