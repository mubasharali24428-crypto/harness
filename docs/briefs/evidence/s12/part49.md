# Part 49: GitHub write opt-in, issues adapter, confined extension runner (session s12-b)

Date: 2026-10-20. Branch `s12/scale-ops`, worktree `harness-s12-b`.

## What changed

GitHub writes are off per project (`section1/migrations/0057_github_write_optin.sql`).

- `projects.github_writes_enabled BOOLEAN NOT NULL DEFAULT FALSE`. Existing and new projects start with writes off.
- `createBranch`, `commitFiles`, `openDraftPR` and `createIssue` check the flag first. While it is off they return 403 `GITHUB_WRITES_DISABLED` and make no GitHub call.
- `GET /api/v1/projects/{projectId}/github/writes` reads the flag for any team member. `PUT` with `{"enabled": bool}` changes it, and only a team admin may call it (others get 403 `FORBIDDEN`). Both are scoped to the caller's team.
- Why: GitHub OAuth Apps cannot request read-only repository access, so the token keeps write scope (`public_repo` by default, `repo` in private mode). The per-project opt-in is the mitigation.

Issues go through the GitHub connector (`section1/migrations/0058_github_write_keys.sql`).

- `GET /api/v1/projects/{projectId}/issues?state=open|closed|all` (default `open`, 50 per page). Pull requests are dropped from the list.
- `POST /api/v1/projects/{projectId}/issues` returns 201. It takes `title` (1 to 256 characters) and `bodyMd` (up to 65536), and needs writes on and an `Idempotency-Key` header of 8 to 200 characters.
- Each key is reserved in `github_write_keys` (`ON CONFLICT DO NOTHING`) before GitHub is called, and the response is stored after the call. A repeated key returns the stored issue. A key whose first request is still running returns 409 `IDEMPOTENCY_IN_PROGRESS`. If the GitHub call fails, the key is released so the caller can retry.

Stdio extensions run only under OS confinement.

- `section5` `confinement.confine_argv(argv, *, workspace, tmpdir, allow_network=False)` wraps a command.
  - macOS: a deny-default Seatbelt profile that allows writes only under the workspace and tmpdir.
  - Linux: a bootstrap that applies Landlock and then execs the command.
  - With neither available it raises `ConfinementUnavailableError`.
- `ExternalMcpClient.discover_stdio(command, args, *, confine)` raises `StdioRefusedError` when `confine` is None. Otherwise it starts the server only through the argv the confiner returns. The registry service still never spawns processes.
- `docs/integrations.md` now describes these routes and the confinement rule in place of "no issue-tracker adapter" and "no sandboxed extension API".

## Verification

Run on the final tree, each from its own directory:

| Command | Result |
|---|---|
| `cd section2 && .venv/bin/python -m pytest -q` | 48 passed (new: `test_github_write_optin.py` 4, `test_github_issues.py` 5) |
| `cd packages/extensions && .venv/bin/python -m pytest -q` | 93 passed (new: `test_stdio_confinement.py` 3) |
| `cd section5 && .venv/bin/python -m pytest -q` | 233 passed, 1 skipped (`landlock is linux-only`; new: `test_confine_argv.py` 3) |
| `cd section1 && HARNESS_TEST_PG=1 .venv/bin/python -m pytest -q tests/test_session5_pg.py tests/test_account_erasure.py tests/test_billing.py tests/test_workspaces_support.py tests/test_csp_rollout.py` | 42 passed; the live-PG test applies every migration through 0058 |
| `HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5499/harness section1/.venv/bin/python deploy/runners/migrate_selftest.py` | PASS: 57 migrations (0001 to 0058) apply clean on PostgreSQL; re-run idempotent |
| `section1/.venv/bin/python scripts/spec_drift.py` | pass. Before the routes were recorded it failed with `github.openapi.json missing` for all four. They are now in `scripts/sdk-gen/specs/github.openapi.json`, and `scripts/sdk-gen/generate.py` (Python and `--target ts`) regenerated `clients/python/harness_sdk/github.py` and `clients/ts/src/github.ts` |
| `cd section4 && ../section1/.venv/bin/python -m pytest -q tests/unit/test_spec_drift.py` | 5 passed |
| `cd clients/python && ../../section1/.venv/bin/python -m pytest -q tests/test_sdk.py` | 27 passed |
| `cd clients/ts && ../../section7/node_modules/.bin/tsc --noEmit -p tsconfig.json --typeRoots ../../section7/node_modules/@types` | exit 0 |

What the new tests prove:

- With writes off, every write route returns 403 and the fake GitHub sees no write request.
- Only an admin can change the flag, every member can read it, and the flag is scoped to the caller's team.
- The issue list drops pull requests.
- Two creates with one key make one issue. A create without a key gets 422. After a failed create, the same key works.
- A confined process can write inside its workspace and cannot write to a directory outside its roots, which is checked on macOS Seatbelt. `confine_argv` refuses when no confinement exists.
- A stdio MCP server lists its tools through the confiner. Discovery refuses with no confiner, and a confiner that refuses stops the launch.

## Limits

- The Linux Landlock path ran no test here; this host is macOS and the Landlock test skips.
- Stdio servers are used for tool discovery only. Tool calls over stdio are not implemented.
- Idempotency keys are kept per project with no expiry and no cleanup job.
- The token still carries write scope. Turning writes on for a project lets the agent use that scope on that project's repository.
- `spec_drift.py --write` run from a pydantic 2.14 venv also rewrites the identity, orchestrator, section4 and section5 specs. Those changes are noise (Decimal `pattern` lines, and section5's hand-kept 400/409 responses), so they were restored, and only `github.openapi.json` was kept.
- Migration numbers 0057 and 0058 were free on session A's worktree when checked (its last is 0056). If A adds migrations before merging, they must start at 0059, because `migrate_selftest.py` fails on duplicate version prefixes.

## Follow-up on master (2026-10-20): chat notifications, 49.003

- A team admin may create a webhook with `"payloadFormat": "slack"` (migration 0060, CHECK
  `harness|slack`, default `harness`). The worker posts `{"text": "Harness: <eventType> on <type>
  <id> (team <id>, <time>)"}` built only from the closed id payload; host allowlist, public-address
  check, signing and bounded retries are unchanged. The Section 7 Integrations page offers it as
  "Message format"; the identity spec and both SDKs record the field.
- Tests: `section1/tests/test_webhooks.py::test_slack_format_is_an_explicit_choice_and_sends_one_id_only_line`
  (default stays the JSON envelope, `teams` is refused with 422, the chat body is exactly one `text`
  key and never carries audit detail, the request is still signed); `section7/tests/unit/integrationsApi.test.ts`
  (2). Section 7: typecheck clean, 852 unit tests pass. `migrate_selftest`: 59 migrations apply clean.
- Limits: one Slack-shaped format; no chat app or inbound commands.
