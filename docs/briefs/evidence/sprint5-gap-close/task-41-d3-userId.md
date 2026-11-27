# Task 41 — D3: `userId` on roster items (cloud, 2026-11-25)

**Verdict: PASS (cloud, no Docker needed).** Product verdict unchanged: NOT READY.

## Change
- `section1/src/harness_identity/invites.py`: `MemberItem.userId: str | None`; `GET /teams/{id}/members` returns `m.user_id` (null only for agent memberships with no user row).
- OpenAPI: `scripts/sdk-gen/specs/identity.openapi.json` (`MemberItem.userId`), regenerated `clients/python/harness_sdk/identity.py`, `clients/ts/src/identity.ts`, rebuilt `clients/ts/dist/identity.d.ts`.
- section7 `/settings/team`: `TeamMemberDto.userId`; Remove / role-change / busy / pending-confirm state and `data-testid`s now key on `userId`. The membership `id` is only the React key. Rows with no `userId` (agents) render no mutation controls.

## Evidence (this run)
| Step | Result |
|---|---|
| `pytest tests/test_roster_api.py tests/test_invites.py` (section1, sqlite lane) | 39 passed, incl. new `test_roster_items_expose_userid_distinct_from_membership_id` |
| `make check-ts-dist` | clients/ts dist matches a fresh build |
| `python3 contracts/ts/generate.py --check` | TS mirror up to date |
| `scripts/spec_drift.py`, `run_eval.py --check`, `check_env_knobs.py`, `sh -n deploy/entrypoint.sh` | ok (`make static-gates` itself needs `section4/.venv`, absent in cloud, so its steps were run individually; its `docker compose config` step was not run) |
| section7 `tsc --noEmit` | clean |
| section7 full vitest | 122 files / 1245 tests passed; team settings tests (21) include two new D3 cases (userId keyed testids; agent rows get no controls) |

## Not covered
Live Playwright against the stack (Task 43/50, owner Mac).
