# Multiplayer pilot — release plan, runbook, known limits (P3.4)

Status: draft for review. No real pilot has run. Nothing here is a claim of production readiness.

## 1. Scope and gate
Supervised pilot, 1–2 allowlisted teams, 2 weeks, only after P0–P3 exit evidence exists
([P1](p1-shared-ui-evidence.md), [P2](p2-hardening-evidence.md), [P3](p3-contracts-data-release-evidence.md)).
Pilot users are told this is a pilot and that a human supervisor watches the audit log.

## 2. Flag and allowlist
**`HARNESS_PILOT_TEAMS`** (comma-separated team ids, Section 4 env) is a code-level per-team gate on all 15 collaboration routes (join, roster, leader, redirect, history, handoff, approvals, staged approvals/apply). Unset or empty = open to every team (dev/test default); **set it for the pilot**. Teams not listed get a 404 with code `PILOT_NOT_ENABLED`. Covered for all 15 routes by `test_pilot_allowlist_gates_every_collab_route`. Read per request, so changing it needs an env change and orchestrator restart, not a deploy of code. The UI recognises that code, stops polling and shows "Shared runs are not enabled for your team yet" instead of an error (`NotEnabled` in `sharedRunPanel.tsx`, vitest-covered).

| Control | How |
|---|---|
| Pilot teams | `HARNESS_PILOT_TEAMS=teamA,teamB` |
| Ownership enforcement | `HARNESS_REQUIRE_OWNERSHIP=1` |
| Engine confinement | `HARNESS_ENGINE_CONFINEMENT=container`; leave `HARNESS_ENGINE_ISOLATED` at default (on); image via `HARNESS_ENGINE_IMAGE` |
| Self-approval | `HARNESS_ALLOW_SELF_APPROVAL` unset (leader cannot approve own request). `1` is a dev/solo escape hatch, never for the pilot |
| Cost | Only free or explicitly low-cost models; none paid by default |
| Workspace source | `HARNESS_WORKSPACE_SEED=/path/to/checkout` on the orchestrator: each new run gets a copy (`.git` skipped, symlinks kept). Operators keep it a clean checkout of the pilot repo and refresh it before each pilot day; unset = empty workspace. Existing runs keep their own copy |
| SMTP sign-in codes | `HARNESS_SMTP_HOST` set (otherwise sign-in codes go to the dev log: never for real users). TLS is required and verified: a refused STARTTLS aborts the send. `HARNESS_SMTP_ALLOW_INSECURE` stays unset (local dev only) |
| Approval TTL | `HARNESS_APPROVAL_TTL_SECONDS` (default 3600) |

## 3. Rollback
1. Turn off shared-run features for the team: remove it from `HARNESS_PILOT_TEAMS` and restart the orchestrator (the 15 collab routes then 404 for it). The list must stay **non-empty**: an empty or unset list opens collaboration to every team. This gate does **not** stop a team from starting or running runs; there is no code-level admission stop. Stopping new runs is operational: tell the team, stop their active runs (step 2), and if needed stop the orchestrator.
2. Stop active runs via `POST /runs/{id}/stop`; confirm `run.cancel_confirmed` in the audit log (a `stopping` result with no confirmation means the container is still alive: `docker ps` and kill the per-run container).
3. Deploy the previous orchestrator/UI build. The collaboration tables (migration 0035) are additive; rollback = leave them unused. Dropping them (`run_participants`, `run_collab_events`, `run_handoffs`, `high_impact_approvals`) is a last resort and loses collaboration history.
4. Audit rows are never purged by any job; they remain after rollback.

## 4. Support runbook
| Symptom | Check | Action |
|---|---|---|
| Run stuck "awaiting leader approval" | Approvals panel; `high_impact_approvals` pending row | Leader decides; handoff can only be offered by the current leader, so if the leader is gone there is no forced takeover: escalate to the supervisor; approvals expire after TTL |
| Stop shows "stopping" | `run.cancel_requested` without `run.cancel_confirmed` | Kill the run's container, note it in the incident log |
| User sees 404 on a run | Wrong team (by design) | Verify team membership; do not widen access |
| 409 `RUN_NOT_PERSISTED` | Run existed only in memory | Restart the run |
| 403 `SELF_APPROVAL` | Requester tried to approve own request | Another engineer/leader decides; audit has `approval.self_denied` |
| Disk growth | session/workspace dirs | Run retention (below) |

### Retention job
Dry-run by default; deletes only finished runs older than N days with no open approval; never touches the audit log or approval metadata.
```bash
HARNESS_DATABASE_URL=... python -m harness_orchestrator.retention --days 30            # report
HARNESS_DATABASE_URL=... python -m harness_orchestrator.retention --days 30 --apply    # delete
```
`HARNESS_RETENTION_DAYS` sets the default; `--include-workspace` also removes run outputs (kept by default).
Suggested cadence: daily, report first, apply after the supervisor reviews the report.

### Metrics
```bash
HARNESS_DATABASE_URL=... python -m harness_orchestrator.pilot_metrics <team_id>
```
Reports created/finished/accepted, elapsed, redirects, approvals and latency, cancel confirmations.

**Baseline (must be measured by people before day 1):** copy `pilot-baseline-template.json`, fill in at least 5 comparable tasks the team did without the harness (minutes, accepted), then:
```bash
HARNESS_DATABASE_URL=... python -m harness_orchestrator.pilot_metrics <team_id> --baseline baseline.json
```
The `baselineComparison` block is `claimable: false` with "insufficient data" unless both sides have at least 5 tasks with valid durations (five created runs alone do not count); even then it is labelled descriptive, not a controlled comparison. The tooling exists; **real baseline numbers do not**.

### Verification commands (pilot gate)
Run each suite separately; none of these is a combined result. Section 4 and Section 1 need a scratch Postgres and **`HARNESS_REQUIRE_OWNERSHIP=true`** (a Section 1 envelope test fails without it, because the unit-test conftest turns ownership off unless it is set):
```bash
export HARNESS_TEST_PG=postgresql://USER:PASS@127.0.0.1:PORT/DB HARNESS_DATABASE_URL=postgresql+asyncpg://USER:PASS@127.0.0.1:PORT/DB HARNESS_REQUIRE_OWNERSHIP=true
section4/.venv/bin/python -m pytest section4 -q -ra
section1/.venv/bin/python -m pytest section1/tests -q -ra
HARNESS_P0_DOCKER=1 section4/.venv/bin/python -m pytest section4/tests/integration/test_container_p0.py -q -ra
make static-gates && (cd section7 && npx vitest run)
```

### Spec drift
`make static-gates` runs `scripts/spec_drift.py`: fails when any recorded spec (orchestrator `openapi.json`, every `scripts/sdk-gen/specs/*.openapi.json`, `section10/openapi.json`) lists different operations than the code, when the UI calls an unknown orchestrator route, or when the UI calls any other `/api/v1` URL that no deployed app, websocket or section 10/11 router serves. `KNOWN_UNSPECCED` is now empty and a test keeps it empty. `--write` regenerates all recorded specs from the deployed app factories (built offline, nothing connects); then run `python scripts/sdk-gen/generate.py` and `--target ts` for the SDKs.

### Real-mode browser proof
`section4/.venv/bin/python deploy/pilot/run_smoke.py --real` (needs `HARNESS_OPENROUTER_KEY_FILE` and a `:free` `HARNESS_TEST_OPENROUTER_MODEL`): fresh PG18, ownership enforced so sign-in is the real OTP form, real engine in the worker container on the free model, then checks the model's file on disk. Evidence: `evidence/pilot-prep/`. See [pilot-prep-evidence.md](pilot-prep-evidence.md).

## 5. Known limits (state these to pilot users)
- Approval gate is enforced at run level and by the staging gate for withheld high-impact changes; it is not a per-tool hook inside a running engine call.
- Guidance is applied at the next engine call, not mid-call; delivery is recorded after the engine call, so a crash between the call and the record can replay guidance once.
- The two-user handoff/approval browser proof still uses the deterministic stand-in worker (it needs a guaranteed deletion). A separate one-user `--real` proof covers real OTP sign-in and a real free-model run, with the OTP code read from the identity log (dev notifier, no SMTP). A real inbox and a multi-user run on the real model are not covered.
- Run workspaces are created on first start (`provision_workspace`), empty or copied from the operator-prepared directory `HARNESS_WORKSPACE_SEED` (`.git` skipped). Nothing fetches a real repo from GitHub: that needs a decision on whose token a headless run uses (Section 2 holds per-user OAuth tokens only).
- `agent_runs.state` is deliberately not maintained: completion is derived from events and the `run.stopped` audit row (what `runs_index.py` already treats as canonical). Dashboards must use the metrics tool or runs index, not that column.
- A leader can mark a staged approval "completed" via the generic complete route without applying it; the changes then simply stay withheld (a misreport, not a bypass). Use the apply route.
- Metrics have synthetic numbers only; no real baseline yet.
- P2 review was focused, not a full security audit.
- No presence/online indicator; collaboration updates are polled (3s), not pushed.
- Ten Section 1 and one Section 5 test skips are documented in the evidence docs.
