# Master handoff prompt — harness pilot remediation

You are a senior coding agent taking over the `harness` monorepo at
`/Users/user/untitled folder 2/harness`. Your job is to finish the remaining
pilot-remediation work from the 2026-10-21 audit, verify it, and report
honestly. Do not restart completed work. Do not claim production readiness.

## Read first (in this order)

1. `AGENTS.md` — layout, frozen contracts, commands.
2. `docs/briefs/startup-audit-2026-10-21.md` — the audit driving this work.
3. `docs/pilot-remediation/BASELINE_2026-10-21.md` — verified current state.
4. `docs/pilot-remediation/FINAL_REPORT.md` — historical; it is superseded.
5. `contracts/api-conventions.md` before touching any API shape.

Contracts under `contracts/` are FROZEN. A wire-shape change requires a version
bump, TS mirror update, fixture/mock updates, and conformance updates in the
same change. Prefer changes that do not touch contracts.

## Current state — already done and verified

Working tree is uncommitted on `master` at `29ce731`. Do not revert it.

- `make gate` passed green after the remediation changes (static gates, lint all
  sections, all Python suites, frontend tsc + 863 vitest).
- `make e2e-compose` passes 12/12 against the rebuilt compose stack.
- `git diff --check` clean.
- S-02/S-03 fixed: model defaults and `opts.extra` overrides are team-scoped.
- S-04 fixed: MCP `start_run` only proposes a plan and stops at
  `AWAITING_PLAN_APPROVAL`; it never approves or starts.
- S-01 partial: macOS Seatbelt now denies reads+writes for `forbidden_paths`
  even through an allow-listed interpreter, and sibling workspaces are isolated
  with per-task denies (the old deny-parent/re-allow-own pair defeated child
  rules). Linux Landlock now actually attaches — `PR_SET_NO_NEW_PRIVS` was
  missing, which made every Linux host look unsupported. Landlock enforces
  writes; it does NOT yet restrict reads of forbidden paths.
- Tenant isolation: every Section 6 meeting route 404s unknown and foreign ids
  identically; Section 1 revokes sessions on member removal; Section 4 requires
  `projectId`, checks run/project ownership, and actually applies the auth
  dependency.
- Review evidence: blank diffs refused; request-supplied diffs disabled by
  default (`HARNESS_ALLOW_REQUEST_DIFF`); GitHub reads use the authenticated
  reviewer; model summaries receive the team id.
- Confinement fails closed: `auto` refuses to run with no usable OS boundary;
  explicit `off` requires `HARNESS_SANDBOX_ALLOW_UNCONFINED=1`;
  `DockerSandboxRunner` raises `NotImplementedError` instead of silently
  executing on the host.
- CI/Makefile: section11 and the MCP server are in `make gate` and CI; workflow
  declares `permissions: contents: read`; isolation guard no longer flags
  parity-only test reads.
- Test counts: S1 986/54 skip, S2 48, S3 97, S4 1602/58 skip, S5 251/2 skip,
  S6 122, S8 91, S10 69, S11 50, MCP 18, frontend 863.

## Remaining work — execute in this order

### 1. Decide and close S-01 for Linux (highest security priority)

Problem: `section5/src/harness_sandbox/confinement.py` handles only write/remove/
make rights in `_HANDLED`; forbidden-path reads are unrestricted on Linux, so a
string-splitting bypass still works inside the workspace. Seatbelt is already
fixed on macOS.

Choose ONE, based on evidence, and implement it fully:

- **Option A (preferred if tractable):** enforce read deny via stacked Landlock
  rulesets or a safe allow-list design. You must prove it with a Linux test that
  spawns a child through `apply_landlock_in_child` and fails to read a canary in
  a forbidden subdirectory, while normal toolchain reads still work.
- **Option B (acceptable only if A is unsafe):** explicitly scope sandboxed
  execution to macOS for the pilot. Enforce this at startup (Linux + sandbox
  enabled fails closed with a clear message), update `README.md`, the risk
  register, and `BASELINE_2026-10-21.md`. Do not silently document it away.

Do not weaken fail-closed behavior. Do not mark S-01 fixed unless the enforcement
is real.

### 2. Durable meeting state (Phase 7)

`section6/src/harness_meeting/service.py` keeps `self.meetings` and `self.rooms`
as process-local dicts. A restart loses meetings and transcripts. The frozen
`meetings`/`meeting_messages` tables exist but have no `team_id` column (team is
inferred through the project).

Requirements:
- Persist meetings, messages, briefs, and prompts across process restart.
- Preserve team isolation and the no-oracle 404 behavior.
- Add a new numbered migration if schema changes are needed; never edit `0001`.
- Add tests that create data, rebuild the service/pool, and read it back.
- If the frozen schema cannot support this without a contract change, stop and
  report the exact conflict instead of inventing a parallel store.

### 3. Cost attribution for REST model calls (Phase 6 remainder)

`section3/src/harness_router/api.py` calls `record_call_usage(...)` without a
`run_id`, and `cost_ledger` has no team column; team spend joins
`run_id -> agent_runs -> projects`, so REST calls made outside a run are
unattributed.

Requirements:
- Thread an optional `runId` (and only if necessary a non-contract team marker)
  from callers that have one; never trust a body-supplied team id over the token.
- For calls with no run, make the unattributed state explicit in the spend
  response rather than silently dropping rows.
- Add tests for attributed and unattributed calls.
- Do not add columns to frozen contracts tables without a migration and contract
  review.

### 4. Docker sandbox runner

`section5/src/harness_sandbox/docker_runner.py` raises `NotImplementedError`.
Either implement a real container-isolated runner with the same policy contract
and tests, or remove it from any configuration path and state clearly that only
the local runner is supported. No silent host execution.

### 5. Supply-chain hardening (H-02)

Pin GitHub Actions to commit SHAs (with version comments), not floating tags.
Verify `.github/workflows/ci.yml` still parses and `make static-gates` passes.

### 6. Phase 10 commercial/legal deliverables

Draft/refresh `PRICING.md`, `TERMS.md`, `PRIVACY.md`, and a pilot SOW/DPA
checklist. Mark every legal item `COUNSEL REVIEW REQUIRED`; do not assert legal
sufficiency. Include: data retention/deletion, subprocessors, model-provider
data flow, incident notice, limitation of liability, and pilot acceptance
criteria. No real customer data or secrets in examples.

### 7. Final verification and docs

- Run `make gate` once at the end and paste the final line.
- Run `make e2e-compose` against the rebuilt stack and paste the result.
- Run `git diff --check`.
- Update `docs/pilot-remediation/BASELINE_2026-10-21.md` with the new evidence
  and an honest verdict. Keep `FINAL_REPORT.md` marked superseded.
- Re-run any flaky test you observe at least twice before calling it flaky.
  `section11 tests/test_api.py::test_index_lifecycle_over_http` failed once
  (`assert 7 == 8`) then passed in isolation — investigate before dismissing.

## Non-negotiable constraints

- Never weaken a security control or a test to make a gate pass.
- Never auto-approve, auto-merge, or bypass human approval.
- No secrets in logs, prompts, audit rows, tests, or model context.
- No paid model calls and no production deploys. Use mocks, replay fixtures,
  local Ollama, or disposable DBs.
- Smallest change that works; no speculative abstractions; no unrelated
  refactors or reformatting.
- Do not commit, amend, push, or open a PR unless the user explicitly asks.
- If a plan dies on contact with reality, stop and report; do not improvise a
  different design silently.

## Working method and commands

For non-trivial work: state a 2–3 sentence plan, then execute. Iterate with
per-section targets; `make gate` is the full monorepo run and takes several
minutes, so run it once at the end, not after every edit.

```sh
cd "/Users/user/untitled folder 2/harness"
make test-section5 lint-section5     # fast loop for sandbox changes
make test-section6 lint-section6     # meeting changes
make test-section3 lint-section3     # router changes
make static-gates
make gate                            # final, long
make e2e-compose                     # needs the compose stack up
git diff --check
```

Compose must be invoked with the env file when starting services from the root:

```sh
docker compose --env-file .env -f deploy/docker-compose.yml up -d
```

## Definition of done

Every item above is either (a) implemented with a named passing test, or (b)
explicitly blocked with the exact reason and a documented mitigation. The final
report must state, in this order:

1. What changed, file by file, in one line each.
2. Commands run and their verbatim final output.
3. What remains blocked and why.
4. A verdict: `READY FOR PILOT`, `READY FOR PRIVATE DESIGN PARTNER ONLY`, or
   `NOT READY` — with the evidence that justifies it.

Do not end with “should work.” End with executed evidence.
