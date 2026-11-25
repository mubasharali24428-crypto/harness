# Task 42 — D2: soft-remove team memberships (cloud, 2026-11-25)

**Verdict: PASS for the targeted cloud lane (unit + live Postgres 16). Full `make gate` is NOT claimed: the owner runs it plus the section1 PG suites on the Mac at this SHA.** Live Playwright B-09 (Task 43) is still
owner-Mac work. Product verdict unchanged: **NOT READY**.

## The defect
`DELETE /api/v1/teams/{id}/members/{userId}` hard-deleted the `team_members` row. `audit_log.actor_member_id`
references that row `ON DELETE RESTRICT` (migration 0023) and audit is append-only, so for any member who had
ever acted the delete raised `RestrictViolationError` / `ForeignKeyViolationError` → **500**. SQLite (the unit
default) does not enforce the FK, which is why unit tests stayed green (Task 36 live journey found it).

Reproduced in the cloud before the fix, on a local PG 16 (`HARNESS_TEST_PG=1`, base `da11276`):
```
sqlalchemy.exc.IntegrityError: (…asyncpg.ForeignKeyViolationError) update or delete on table "team_members"
violates foreign key constraint "fk_audit_log_actor_member_id" on table "audit_log"
[SQL: DELETE FROM team_members WHERE team_members.id = $1::VARCHAR]
FAILED tests/test_member_soft_remove_pg.py::test_member_who_acted_is_soft_removed_not_500
```

## Design (owner decision D2: soft-remove)
Migration **`0082_team_member_soft_remove.sql`** adds two nullable columns on `team_members`:

| Column | Meaning |
|---|---|
| `removed_at TIMESTAMPTZ` | when the seat stopped being a seat; `NULL` = active |
| `removed_user_id VARCHAR(36)` FK `users(id) ON DELETE SET NULL` | who held the seat (forensics, erasure) |

Removal (`harness_identity/memberships.py::soft_remove_seats`) stamps `removed_at`, copies `user_id` into
`removed_user_id` and **sets `user_id = NULL`**. The row stays as the FK target, so historical audit rows keep
resolving their actor (id and display name). `audit_log` is never read-modified: `actor_member_id` is untouched.

Why null `user_id` instead of only filtering on `removed_at`: every door that turns a person into a principal
looks the seat up by `(team_id, user_id)` — Section 1 `get_principal`, login, the WebSocket door, SSO, SCIM,
notification targeting, the shared bearer dependency in `contracts/…/fastapi_auth.py` (sections 2, 8, 11,
runners, extensions) and ~25 raw-SQL lookups in section4. Nulling `user_id` makes all of them miss a removed
member **without touching them** (fail closed; this is the same mechanism account erasure already uses).
Queries that scan a whole team *without* a user id filter `removed_at IS NULL` explicitly:

- roster list `GET /teams/{id}/members` (`invites.py`) — active seats only;
- last-admin guards: `teams_api._count_other_admins`, `sso._is_last_admin` (a removed admin row still says
  `role=admin`; it must not count). SCIM's and erasure's admin counts already require a non-null `user_id`.

A re-invited person gets a **fresh** seat row; a dead seat is never reactivated (no unique `(team_id, user_id)`
constraint exists, and the dead row has `user_id NULL`, so nothing collides).

### Sessions / tokens
Unchanged pattern: the handler deletes the person's `auth_sessions` for that team before the seat goes. A token
that survives anyway is dead because the seat no longer resolves (every token door answers 401). Provider `api_keys` are
team-scoped, not per-person, so nothing to revoke there.

### Hard DELETE path
**Removed.** `remove_member` always soft-removes (no "delete if unreferenced" branch), so `member.removed`'s
`target_id` always resolves. The same FK class existed on the org paths (`orgs._revoke_access`,
`DELETE /orgs/{id}/teams/{team}/members/{user}`): they deleted only *un*-audited seats and silently left audited
ones **live**, so a person removed from an org could sign straight back into that team (found by code reading).
Both now call the same `soft_remove_seats`.

### Erasure
`erasure.py` also anonymises seats removed earlier (`removed_user_id = :u` → display name "Deleted user",
`removed_user_id` NULL) and its retention-hold probe matches `removed_user_id` too.

## Files
- `section1/migrations/0082_team_member_soft_remove.sql` (new)
- `section1/src/harness_identity/memberships.py` (new helper), `models.py` (2 columns), `teams_api.py`,
  `invites.py`, `sso.py`, `orgs.py`, `erasure.py`, `audit_emit.py` (docstring)
- Tests: `tests/test_member_soft_remove.py` (SQLite, 5), `tests/test_member_soft_remove_pg.py` (live PG, 3),
  `tests/test_fk_deletion_story.py` (FK map + pinned inventory 64 → 65)

No OpenAPI / TS change: the endpoint still answers 204 with an empty body.

## Evidence (this run)
| Step | Result |
|---|---|
| New PG test on base `da11276` (before fix) | **3 failed** — FK violation 500 reproduced (above) |
| `HARNESS_TEST_PG=1 pytest tests/test_member_soft_remove_pg.py` (PG 16, full 0001–0082 chain) | **3 passed**: audited member → 204, roster excludes, old bearer 401, sessions 0, audit rows byte-identical, re-login 403; removed admin does not count; org team departure soft-removes an audited seat |
| Fresh PG 16 DB, `psql -f` every migration 0001–0082 | applies cleanly; `fk_team_members_removed_user_id` confdeltype `n` (SET NULL) |
| `pytest tests/test_member_soft_remove.py tests/test_roster_api.py tests/test_orgs.py tests/test_account_erasure.py tests/test_account_deactivation.py tests/test_invites.py tests/test_sso_*.py tests/test_scim_users.py tests/test_fk_deletion_story.py` | 160 passed, 3 skipped (Docker-only live FK class) |
| `ruff check src tests scripts` (section1) | All checks passed |
| Full section1 suite (SQLite lane) | 1294 passed, 68 skipped, **8 errors**, all in `test_runs_index_tuning.py` (SQLite `agent_events` seed insert fails at fixture setup). **Pre-existing:** the same 8 errors reproduce on base `da11276` with this change stashed; unrelated to D2. |
| section1 live-PG suites (`*_pg.py`, `HARNESS_TEST_PG=1`) | **49 passed** (member_soft_remove, session3/4/5/11, owner_transfer, policy_sockets, audit_chain, collab_push) |

## Not covered (owner Mac)
- `test_fk_deletion_story.py::TestLivePostgresDeletionStory` needs the `harness-db-1` Docker container; the
  equivalent confdeltype check was done by hand above.
- Task 43: un-`test.fail` `section7/e2e/invite-revoke-live.e2e.ts` and run the live B-09 journey.
- Readiness manifest: the on-disk head is now **0082**. Redeploy, then `--write-manifest` (Task 45b runbook).
