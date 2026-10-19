# Session 3 — Identity, orgs, permissions, workspaces (parts 37, 38, 39)

Prefix: `docs/briefs/stable-prompt-prefix.md`. Part 37 is capped at 8 (live SSO/Stripe are externally gated); 38 and 39 target 9.
Directives: `docs/master-prompts/harness-multiplayer-master-build-prompt.md` sections 37, 38, 39.

## Lane contract (lead wires everything; lanes never touch shared files)
Lanes: **O** orgs/identity (37), **P** policy engine (38), **W** workspaces (39). Each lane owns ONLY its files:
- O: migration `section1/migrations/0038_organizations.sql`, `section1/src/harness_identity/orgs.py` (exports `router`), tests `section1/tests/test_orgs*.py`, `docs/organizations.md`, `docs/briefs/evidence/s3/lane-o.md`.
- P: migration `0039_policy.sql`, `policy.py` (pure engine + `router`), tests `test_policy*.py`, `docs/permissions.md`, `evidence/s3/lane-p.md`.
- W: migration `0040_workspaces.sql`, `workspaces.py` (`router`), section4 `workspace_gate.py` plus ONE call in the run-start path in `service.py`, tests `section1/tests/test_workspaces*.py` and `section4/tests/unit/test_workspace_gate.py`, `docs/workspaces.md`, `evidence/s3/lane-w.md`.
Forbidden for lanes: `models.py`, `app.py`, `audit.py` (Session 3 audit actions are already reserved: org.*, policy.*, stepup.*, emergency.*, workspace.*), `capability-matrix.md`, `Makefile`, `deploy/`. Use SQLAlchemy Core `text()` SQL, not new ORM models.
Fixed table names so lanes interoperate: `organizations(id,name,slug,status,created_at)`, `org_members(org_id,user_id,role,expires_at,created_at,removed_at)` with roles `owner|admin|billing_admin|member|guest`, `teams.org_id` (nullable add), `workspaces(id,team_id,org_id,project_id,name,state,...)` states `active|paused|archived|deleted`.
Every lane: deny by default, team/org-scoped reads, 404 for foreign resources, audit every consequential change through the existing audit writer, closed-label metrics optional. Tests run on sqlite (`./.venv/bin/python -m pytest`), ruff clean; Postgres/compose/smoke are run by the lead only (do not start docker).
Never `ruff check --fix` (it deletes fixture imports). Do not commit. Report paths, test counts and honest gaps.
