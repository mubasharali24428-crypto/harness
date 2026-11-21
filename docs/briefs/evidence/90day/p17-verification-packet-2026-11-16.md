# P-17 client trust portal: verification packet (2026-11-16)

For the model that verifies this work. The author (Claude) built it and ran the checks in
section 4; nothing here is committed, deployed, or seen in a browser. Trust nothing below
without rerunning it. Branch `w28-s1-delegate`; other lanes have uncommitted files in this
checkout (section4 `reviewer.py`, `service.py`, `signals.py` and tests): leave them alone.

## 1. What was asked

90-day plan, Week 9, P-17: a read-only view for a partner's end-client ("every change,
approver, test evidence, cost"), with cross-member negative tests. Owner decision
2026-11-16: **per-project guest**. An admin grants a guest named projects; a guest is
refused everywhere else. P-16 (paid contract) is the owner's and was not touched.

## 2. Design in five lines

- `project_guest_grants(project_id, user_id, granted_by)`: migration 0076 + ORM model.
- `guest_gate` in `contracts/src/harness_contracts/fastapi_auth.py`: for a team member
  whose role is `guest`, refuse the request (403 `GUEST_PORTAL_ONLY`) unless it is on a
  fixed list **and** resolves to a granted project of the session's team. Default deny.
- The gate runs inside both principal resolvers: `make_bearer_dep` (every service except
  identity) and `section1/.../auth.py:get_principal` (identity).
- Admin routes in `section1/.../portal_api.py`: `PUT`/`DELETE /projects/{p}/guests/{u}`,
  `GET /projects/{p}/guests`; plus `GET /portal/projects` (a guest's grants).
- UI: `/portal` lists granted projects and runs; a run opens the existing read-only
  report page. The workspace shell sends a guest to `/portal`.

What a guest may call: `GET /me`, `GET /portal/projects`, `POST /auth/logout`,
`GET /orchestrator/runs?projectId=<granted>`, `GET /orchestrator/runs/{run}` and
`/plan`, `GET /reviews/{pr}`. Nothing else.

**Deviation from the wording the owner approved:** the option said "a new read-only portal
API". There is no new aggregate API; guests read a fixed list of existing GET routes
through the gate. Same boundary, less code. Overrule if a curated payload is wanted.

## 3. Files

New: `section1/migrations/0076_project_guest_grants.sql`,
`section1/src/harness_identity/portal_api.py`, `section1/tests/test_portal_guest.py`,
`section7/app/portal/page.tsx`, this file.

Changed for the feature: `contracts/.../fastapi_auth.py`, `contracts/.../policy_enforce.py`
(route classification), `section1/.../auth.py`, `models.py`, `app.py` (router),
`audit.py` (two actions), `erasure.py` (purge grants), `section7/src/components/AppShell.tsx`,
`workspace/RunEvidence.tsx` (`canExport`), the report `page.tsx`.

Changed because the old rule "guests read the whole team" was pinned in tests:
`section1/tests/test_{memory,rules,skills,settings,runner}_api.py` (guest read now 403),
`test_fk_deletion_story.py` (62 -> 64 FKs), `section7/src/lib/commands.test.ts`
(`/portal` skipped by the palette walk). **Check each of these edits is the minimum.**

Generated: `scripts/sdk-gen/specs/identity.openapi.json`, `clients/python/harness_sdk/identity.py`,
`clients/ts/src/identity.ts`. The same regeneration also rewrote the orchestrator spec and
SDK from another lane's uncommitted work; those four files were restored with `git checkout`.

## 4. Checks the author ran (rerun all of them)

Run each from the directory shown.

| # | Command | Result on 2026-11-16 |
|---|---|---|
| 1 | `cd section1 && .venv/bin/python -m pytest tests/test_portal_guest.py -q` | 14 passed |
| 2 | Mutation: in `guest_gate` change `if project_id and (` to `if True or project_id and (`, rerun #1, restore | 8 failed, 6 passed (the gate tests do bite) |
| 3 | `cd section1 && .venv/bin/python -m pytest tests -q` | See section 5 |
| 4 | `cd section8 && .venv/bin/python -m pytest tests -q` | 104 passed |
| 5 | `cd section7 && npx tsc --noEmit && npx vitest run` | tsc clean; 1201 passed |
| 6 | `section4/.venv/bin/python scripts/spec_drift.py` (repo root) | "specs and UI calls match the router" |
| 7 | `deploy/runners/migrate_selftest.py` against a throwaway `postgres:18` on 127.0.0.1:55476 | PASS: 74 migrations (0001 -> 0076), re-run idempotent |
| 8 | The gate's three SQL statements executed on that migrated Postgres | Ran without error |

For #7 use your own container, never `make pg-up` (it starts the live `harness` db):
`docker run -d --rm --name p17-pg-probe -e POSTGRES_USER=harness -e POSTGRES_PASSWORD=harness -e POSTGRES_DB=harness -p 127.0.0.1:55476:5432 postgres:18`,
then `HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@127.0.0.1:55476/harness HARNESS_TEST_PG_DSN=postgresql://harness:harness@127.0.0.1:55476/harness HARNESS_TEST_PG_CONTAINER=p17-pg-probe section1/.venv/bin/python deploy/runners/migrate_selftest.py`,
then `docker rm -f p17-pg-probe`.

## 5. Known failures and what is not verified

- `section1/tests/test_runs_index_tuning.py`: 4 tests fail with `no such column: e.payload`
  on SQLite. The query that reads `e.payload` came in with `9d4fab2` (2026-11-13) and the
  test's hand-built `agent_events` table has no such column; neither file is touched by
  this work (`git status` clean on both). Not rerun on a clean checkout: confirm at `01fa370`.
- Final full section1 run: 1256 passed, 63 skipped, 4 failed (only those 4).
- section4: `.venv/bin/python -m pytest tests -q --deselect tests/test_done_signal.py`
  gave 2365 passed, 60 skipped. `test_done_signal.py` was left out (another lane's
  uncommitted edits, and a known global-state trap), so it is **not** covered.
- Frontend `npm run build`: compiled; `/portal` and the report route are in the output.
- **No browser run.** Nothing is deployed: the RC stack lacks migration 0076 and the new
  code. There is no `e2e/portal.e2e.ts`. P-17's acceptance ("end-client completes a review
  session") is therefore **not met**.
- The gate tests use stand-in routes at the real paths, on SQLite. They prove the gate, not
  that the real orchestrator and review handlers return a guest's data correctly. The real
  handlers also filter by `principal.teamId`, which a guest's session carries.

## 6. Things to attack

1. Path tricks against the regex list: trailing slash, `//`, `%2F`, `..`, upper-case, a
   proxy prefix. `request.url.path` is matched with `re.match` and `$`; note `$` also
   matches before a trailing newline.
2. `GET /orchestrator/runs?projectId=A&projectId=B`: the gate reads the first value. Which
   one does the handler use?
3. Routes mounted without either resolver (API-key auth, runner tokens, the WebSocket
   ticket path, the event stream). Can a guest's session reach team data through one?
4. A person who is a guest in two teams; a guest whose role is later raised or lowered.
5. A removed guest who is re-invited: their old grant rows are still there and come back
   to life. Membership removal does not delete grants. Decide if that is acceptable.
6. Does the review route's `pr_id` always equal `pull_requests.id`? (Author's assumption.)
7. `PUT .../guests/{u}` repeated, `DELETE` of a grant that does not exist, another team's
   admin: expect 200 / 204 / 404 and exactly one audit row per real change.
8. What the allowed payloads expose: does the run aggregate or review data carry anything
   about other projects, other clients, team members' emails, or internal prompts?

## 7. Behaviour changes to know before deploying

- Every existing team member with role `guest` (including ones mapped by SSO groups) loses
  all access until an admin grants them a project. No live data was checked for such users.
- Granting is API-only. There is no screen for an admin to grant or revoke.
- A guest who opens a workspace URL is redirected to `/portal`; a guest who opens a report
  for a project they were not granted sees "The report didn't load".

## 8. Hand-back

Write `docs/briefs/evidence/90day/p17-verification-result.md`: each section-4 check with
your own output, each section-6 item as held / broken with the request you sent, and any
edit you think is not minimal. Change no source; report instead.
