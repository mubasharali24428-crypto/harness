# Harness Multiplayer AI: project state and resume guide

Updated 2026-11-06 (reconciled against git, GitHub and `docker ps`; section 2 tracks Roadmap 3.0; the rest is the path-to-9 record from 2026-10-21, so the matrix counts in §3 are stale). Read this first in a new session, then
`docs/briefs/path-to-9-plan.md`, `docs/briefs/capability-matrix.md` and `docs/briefs/path-to-9-checkpoint.md`. The repository is the
source of truth: re-verify any number before relying on it. Nothing here claims readiness for real users (see "Not cleared").
Per-session results, the old verification records and the local Session 11 detail (§3g) are in `docs/briefs/evidence/s11/state-history.md`.

## 1. What the project is

The "master prompt" is the 61-part *Harness Multiplayer AI: Full Product Master Build Prompt*
(`docs/master-prompts/harness-multiplayer-master-build-prompt.md`, committed in `95bb6b2`).
The programme brings every part to 9/10: each directive implemented or explicitly out of scope, invariant tests, an evidence file under
`docs/briefs/evidence/`, docs that match behaviour including limits, and a `verified` matrix row. Parts that need an outside party stay
capped at 8: 03, 37, 52, 53, 58, 61. Part 57's cap was removed because Stripe is out of scope (owner direction, 2026-10-19).

Layout: section1 identity, auth, audit, event bus, orgs, policy, workspaces, integrations (migrations 0001 to 0075) · section3, 6, 8, 10
auxiliary · section4 orchestrator · section5 sandbox · section7 Next.js UI and Playwright · `packages/` (A5 extensions, ops, telemetry) ·
`integrations/connectors/` · `deploy/` compose stack, runners, observability, `deploy/pilot` smoke stack · `docs/` · `scripts/` · `clients/`.

## 2. Where the work is now: Roadmap 3.0 (2026-11-05)

The live plan is `docs/roadmap-3.0.md` (stages 0 to 10); the path-to-9 sessions table moved to `evidence/s11/state-history.md`.
Done: 0 stabilize (PR #7), 1 trust and recovery (code side), 2 reliability W17 (code side), 3 daily loop W23/W24/W7, 7 W30 audit
(`docs/decisions/2026-11-05-w30-candidate-audit.md`), 8 W31 Scheduler pilot (`docs/decisions/2026-11-04-w31-scheduler.md`).
Partial: 4 benchmark (W25/W26 run 2026-11-05: 5 sequential runs per task on each of two models, 3 concurrent; Haiku is the stack default; the three formerly local-model tasks now pass on Haiku; the other-harness comparison is open), 6 delegation (W28 S1a delegate verb behind `HARNESS_SUBAGENTS`; S1b-1
child runs with own approval and cancel cascade built and live-drilled; synthesis S1b-2 built (read-only; deployed as vw28s4 with live drills 12/12 and 9/9 per commits `41fc733`, `eb53563`); W13 exec leg built), 9 W32 (seam analysis only, gated on W28).
Partial: 5 governance W27 (slice 1: gate denials carry reason, alternative and the scope to request, drilled on Haiku and deployed; slice 2: denial circuit breaker, timeout marked apart from denial, `scopeRequest` object, tested without a model, deployed on vw27s2; slice 3: the orchestrator ends a run on repeated denials, the scope request rides the tool-result event, and the run read returns `estimatedCostUsd`, deployed on vw27s3 and proved live for the stop). All 23 live eval tasks pass at n=1 on 2026-11-05. W29 capability review first pass written (`docs/capability-review-2026-11-05.md`, summary-derived facts tagged). W33 readiness pack prepared for an independent reviewer (`docs/reviews/w33-readiness-pack-2026-11-05.md`), not reviewed; candidate measurement not done. Verdict unchanged: NOT READY. Newer detail is in `STATUS.md`.

2026-11-06 reconciliation (see the `STATUS.md` entry of that date): the work above sat as 41 local-only commits on `w28-s1-delegate` until this PR.
The live stack runs mixed image tags (orchestrator `vw27s3`, worker-daemon `vw27s2`, other backends `vw28s6`, frontend `vw24b2`), so the
frontend lacks the synthesis panel and scope-request editor; redeploy one tag set and run `scripts/ops/stack_tags.py` before any claim about the running system.
hy4 batches 1 to 4 are committed (`57b466e` to `7cdd365`). Open from them: four strict-xfail shell-gate bypasses (not fixed), and two owner rulings
(delegating to paths that do not exist yet; what `0` means for the denial knobs). Part 52 below is therefore not closed at argument level.

## 3. Where the matrix stands

Counts (2026-10-21): verified 50 · implemented 1 · partial 8 · missing 0 · blocked 2.
Implemented: 26. Partial: 31, 51, 52 (capped), 53 (capped), 55, 56, 57, 58 (capped). Blocked: 03, 61.
Readiness: **NO-GO** for broad rollout (`docs/briefs/final-deliverables.md`, section 10).

## 4. Open work, in order

1. **Part 52 (unblocked 2026-10-21, B1).** The runtime's PTY bash tool, which cannot start on Linux, is disabled per run and
   replaced by `section4/.../dsh_plugins/harness-shell.js`. The plugin sends every command through Section 5's
   `shell_gate.py` (allow-list, forbidden and intent scope paths, Landlock), and it runs in the hardened worker image
   (`evidence/s12/part52.md`). The same work fixed Section 5's Landlock, which never applied on Linux before. Left: external
   pen test (cap 8), argument-level path checks, one program per call.
2. Row 26: `run_smoke.py --real` needs an OpenRouter key (not configured).
3. Row 55: dashboard not imported into Grafana and looked at (no Grafana image; pulling one not authorised).
4. Row 56: no screen reader; only two pages walked by the browser check; English only (contrast and target size closed 2026-10-21).
5. Part 57: in-flight engine spend is an estimate. Part 51: residency is deployment-wide.
6. Parts 31/58: backups expired outside the scripts (bucket lifecycle rules) are not covered by holds.
7. Multi-replica (closed 2026-10-21 on one host, `evidence/s12/part54.md`): artifacts, exports and diffs share one Docker volume
   across orchestrator replicas, and 1000 sockets over two identity replicas delivered every envelope. Several hosts need NFS or an
   object store; the store volume is not in the backup scripts. Part 28: alerts go to one email address plus one optional webhook
   (`HARNESS_ALERT_WEBHOOK_URL`); no pager integration. Part 53: point-in-time restore is drilled from the live file archive and
   through self-hosted S3 (SeaweedFS; `evidence/s12/part28-55.md` sections 5-6), not yet against a cloud bucket.
8. Smaller limits: a session state change reaches running runs within 30 s, by sweep (40); only intent
   `scopePaths` are engine-enforced, at argument level (42); a timed-out engine call's tool calls are not recorded (44); GitHub OAuth token keeps write scope once a project opts in (49); the audit anchor file is only as safe as its storage (11).
9. Externally gated: 03 (real pilot cohort and baseline), 37 (live identity provider), 52 and 58 (pen test, counsel), 53 (real restore), 61.

Not a gap: a paused workspace only blocks new starts, by design (`workspace_gate.py` docstring).

## 5. Facts that bite

- **Run each section's tests from its own directory** (`cd section1 && .venv/bin/python -m pytest tests`). From the repo root,
  `Settings` reads the developer's root `.env` (it sets `HARNESS_REQUIRE_OWNERSHIP`), and `test_f1_envelope_idempotency` fails.
- Live-PG runs: `HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@127.0.0.1:5499/harness`
  (add `HARNESS_TEST_PG_DSN=postgresql://harness:harness@127.0.0.1:5499/harness` for `deploy/runners/migrate_selftest.py`).
  Tests build and drop their own throwaway databases.
- A throwaway local PostgreSQL on macOS needs `LC_ALL=en_US.UTF-8`, and under a long path `-c unix_socket_directories=` plus TCP.
- The A5 packages (`packages/*`, `integrations/connectors/*`) have no venv in the repo: install them with their `dev` extras in a
  fresh venv (extensions needs `mcp<2`) and run `pytest tests` from each package directory with `PYTHONPATH=src`.
- `HARNESS_PILOT_TEAMS` is an **allowlist**: unset or empty opens collaboration to every team.
- Prometheus scrapes the **root `/metrics`**; sandbox `confinement=off` has no OS boundary; Alertmanager emails `HARNESS_ALERT_EMAIL_TO` (and posts to `HARNESS_ALERT_WEBHOOK_URL` if set; `scripts/test-alerts.sh` runs the promtool alert tests) (`docs/runbooks/alerting.md` §7).
- `--env-file .env` makes compose skip `deploy/.env`, so the root `.env` must also set `WALG_FILE_PREFIX` or the live `db` archives
  nothing (`evidence/s12/part28-55.md` §4). Run the `walg` backup with `--no-deps` against `db`, or `walg-db` archives into the live prefix.
  The same trap applies to money: a backend recreated against an env file without `HARNESS_RATE_CARD_JSON` boots pricing from the frozen
  catalog while its peers price from the card (observed 2026-10-29 — router quote vs ledger booking, ~16x). Keep every shared knob in the
  root `.env`; `test_every_backend_service_inherits_the_pricing_and_budget_knobs` guards the compose side.
- `ruff check --fix` deletes imported pytest fixtures. Never use it broadly (`--select I001 --fix` on one new file is fine).
- SQLite tests with spawned engine tasks must await the tasks before `engine.dispose()`, or teardown stalls.
- SQLite hides PostgreSQL typing errors: an untyped empty `IN` list failed only on PostgreSQL (`evidence/s11/live-pg-search-export.md`).
  Run the live-PG modules after touching raw SQL.
- **A new HTTP route must be recorded in the specs and SDKs:** `section4/.venv/bin/python scripts/spec_drift.py --write`, restore
  `section5/openapi.json` with `git checkout`, then `python3 scripts/sdk-gen/generate.py` and `--target ts`.
- `execution.retry()` returns `(body, created)`; a repeated `requestId` is `created == False` (HTTP 200).
- With the collab push socket up the UI safety poll is 20 s, so the peer-offline alert can take that long (e2e waits 30 s).
- `run_smoke.py` needs about 2 minutes between runs (port 8101 TIME_WAIT) and overwrites `docs/briefs/evidence/p1/*`.
- FastAPI 0.141 nests included routers: walk `app.openapi()["paths"]`, not `app.routes`.
- Dev SQLite has no `teams.org_id` (migration 0038 adds it on PostgreSQL); `require_team_stepup` tolerates that.
- Migrations are 73 files numbered 0001 to 0075 (there is no 0014 and no 0074); `deploy/runners/migrate_selftest.py` checks disk against the ledger.
- Owner's machine is macOS zsh (no `timeout`, BSD `sed -i ''`, repo path has a space, `--include=*.py` does not expand). The cloud
  box is Linux, root, no Docker daemon.
- `AGENTS.md` may carry an auto-injected `<claude-mem-context>` block from a memory plugin: never commit it.
- Commit by explicit path only; never `git add -A`.
- Worktree `.venv` dirs are symlinks to master's venvs and import master's sources: set `PYTHONPATH=<wt>/contracts/src:<wt>/section1/src:<wt>/section4/src`.
- Load probe: `PROBE_ORCH_REPLICAS=2 scripts/loadtest/run_probe.sh run <vus> 60 <label>`; the loadtest profile lifts the tier cap
  (`PROBE_TIER_RUNS_PER_DAY`), else it measures the 20 runs/day free tier.

## 6. Not cleared

The pilot is **not cleared for real users**: no measured baseline (part 03), alerts reach one email address and one optional webhook only, operator-present rules are manual, live
SSO unproven, no pen test, point-in-time recovery proven on file storage and self-hosted S3, not a cloud bucket, no counsel review, the real-engine smoke (`--real`) has not run, and
the browser checks used synthetic data and no screen reader.

## 7. How to resume

```
cd "/Users/user/untitled folder 2/harness"
git fetch origin && git log --oneline -8 && git status --short | head -40
cd section1 && .venv/bin/python -m pytest tests -q        # add the live-PG env from §5 for PostgreSQL
cd ../section4 && .venv/bin/python -m pytest tests -q
# Docker up: make pg-probe-s1 && make pg-probe-migrate      (expect 73 migrations, 0001 to 0075)
```

Per-part work: read the matrix row, audit directive by directive, add the missing invariant test, write
`docs/briefs/evidence/s11/<part>.md`, flip the row only with that evidence, and run `section4/tests/unit/test_program_docs.py`.
