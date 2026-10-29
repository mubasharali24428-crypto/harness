# M2 W13 checkpoint — BYO runner control plane (2026-10-29, paused)

Resume guide for the next session. Read this first, then `docs/m2-w13-plan.md`.
The repository is the source of truth; re-verify every claim before relying on
it. Nothing here is a release claim.

## Where the work stands

| Item | State |
|---|---|
| W12 verification (Section 1/4 suites, static gates) | **pass** — S1 999→1009 passed, S4 1858 passed, static gates green before W13 edits |
| Issues found and fixed (see below) | **done** |
| W13 control plane (migration, API, audit, policy, tests, drill, docs) | **implemented, unit-verified** |
| Backend image rebuilt with W13 code | **in progress at pause** (`/tmp/w13-build.log`); daemon was re-downloading pinned wheels |
| Migration 0068 applied to live DB | **not yet** (the old migrate image cannot see 0068) |
| identity OpenAPI + Python SDK regenerated | **not yet** — must be fetched from the rebuilt service |
| `make static-gates` | **RED** only on `identity.openapi.json missing: [9 runner routes]` — expected until the spec is regenerated |
| Live drill `w13_runner_drill.py` | **not run yet** |
| W13 execution leg (runner executes the approved plan) | **OPEN by design** — see plan §Remaining |

## Issues found and resolved this session

1. **Corpus regression (working tree).** `docs/eval/corpus.json` had been edited
   from a stale copy: it dropped `w11-grounding` and `w12-skills-memory` and
   reverted the w11-vision verifier. Rebuilt from HEAD + the new W8 entry.
   Corpus is now 37 tasks (was 36 + 1 new), then 38 with `w13-byo-runner`.
2. **Uncommitted W8 gate.** `scripts/eval/tasks/concurrency_task.py`,
   four `w8-concurrency-*.json` evidence files and a corpus entry existed only
   in the working tree. Re-ran the drill against current code: **PASS**
   (`w8-concurrency-1793254624.json`). Fixed 5 lint errors (unused var, three
   ISC004 concatenations, missing +x) and committed the work.
3. **Five services on a stale image** (github, meeting, models, review, sandbox)
   reported `/ready` 503 `schema-drift` after W12's migrations. Recreated on the
   current tag; all report `{"ok":true,"db":"ok","migrations":"ok"}`.
4. **`AGENTS.md` working-tree noise** (claude-mem timestamp only) reverted.
5. **Pre-existing lint failures that made `make lint` red on HEAD:**
   `tests/test_http_client.py` (I001), section4 (38 errors: unused imports,
   I001, UP017, E501, F811 on the intentional pytest fixture import in
   `test_deployments.py`/`test_shares.py` — silenced with a file-level
   `# ruff: noqa: F811` explaining the pattern), section5 (2 E501 in
   `browser_gate.py`, unused `pytest`). `make lint` is now green for all nine
   sections.
6. **Stale Python SDK.** `clients/python/harness_sdk/orchestrator.py` was
   missing already-shipped operations (run checkpoints list, rewind body). The
   generator was re-run; this diff is legitimate drift repair.

## W13 files added / changed

Added:
- `section1/migrations/0068_team_runners.sql` — `team_runners`,
  `runner_nonces`, `runner_jobs` (see plan for semantics; deliberately no
  command/tool/permission columns).
- `section1/src/harness_identity/runner_api.py` — admin registration (token
  shown once, sha256 + derived attestation key stored), idempotent revocation,
  signed-attestation verification, nonce+timestamp replay guard, team-scoped
  job offer/claim/lease-heartbeat/finish, expiry → `released` + run `orphaned`.
- `section1/tests/test_runner_api.py` — 10 offline tests (all passing).
- `scripts/e2e/w13_runner_drill.py` — live drill (not yet run).
- `docs/m2-w13-plan.md` — threat model, trust assumptions, gate position,
  remaining work.

Changed:
- `section1/src/harness_identity/models.py` — `TeamRunner`, `RunnerNonce`, `RunnerJob`.
- `section1/src/harness_identity/app.py` — router mounted.
- `section1/src/harness_identity/audit.py` + `audit_emit.py` — five actions
  (`runner.registered|revoked|attested|job_assigned|job_completed`).
- `contracts/src/harness_contracts/policy_enforce.py` — nine routes classified.
- `section1/tests/test_fk_deletion_story.py` — six FKs pinned, inventory 49 → 55.
- `docs/eval/corpus.json`, `CHANGELOG.md` — W13 entries.
- Lint-only repairs in section1/section4/section5 as listed above.

## Verified before the pause

```
cd section1 && ./.venv/bin/python -m pytest tests/ -q      # 1009 passed, 54 skipped
cd section4 && ./.venv/bin/python -m pytest tests/ -q      # 1858 passed, 58 skipped (one spec-drift fail is expected: identity spec not regenerated yet)
make lint                                                  # all nine sections green
cd section5 && ./.venv/bin/python -m pytest tests -q       # 436 passed, 8 skipped
```

## Resume steps (in order)

1. **Finish or restart the image build.** It was launched with visible progress:
   ```
   cd "/Users/user/untitled folder 2/harness"
   docker compose --env-file .env -f deploy/docker-compose.yml build --progress=plain identity
   ```
   The slow step is `pip install` re-downloading the 60 MB pinned
   `deepseek-harness-runtime-bin` wheel (this link measured ~35–100 KB/s).
   Fallback if the network stays this slow: build from the existing image by
   copying `contracts/` + `section1/` into a container and running
   `pip install --no-deps -e ./contracts -e ./section1`, then `docker commit`
   as `harness-backend:vdev-local`; record that provenance in the evidence.
2. **Apply 0068 with the NEW image** (the old migrate image cannot see it):
   ```
   docker compose --env-file .env -f deploy/docker-compose.yml run --rm migrate
   docker exec harness-db-1 psql -U harness -d harness -tAc \
     "select version from schema_migrations order by version desc limit 2"   # expect 0068
   ```
3. **Recreate the backend services** on the new tag so no service runs stale
   code against the new schema:
   ```
   docker compose --env-file .env -f deploy/docker-compose.yml up -d --no-deps \
     identity orchestrator github models meeting review sandbox
   for p in 8001 8002 8003 8004 8005 8006 8008; do curl -s localhost:$p/ready; echo; done
   ```
4. **Regenerate the identity spec from the live service, then the SDKs:**
   ```
   python3 scripts/sdk-gen/fetch_specs.py
   python3 scripts/sdk-gen/generate.py
   make static-gates                                       # must go green
   ```
   (Do not generate the spec in-process: the live app mounts `/ready`, the bare
   `make_app` does not, and the gate checks that path.)
5. **Run the drill and keep the evidence:**
   ```
   python3 scripts/e2e/w13_runner_drill.py
   cat docs/briefs/evidence/eval/w13-runner-*.json
   ```
   Expected: `verdict: PASS`, evidence at
   `docs/briefs/evidence/eval/w13-runner-<stamp>.json`, residual live claims 0.
6. **Re-run the spec-drift test** now that the identity spec is current:
   ```
   cd section4 && ./.venv/bin/python -m pytest tests/unit/test_spec_drift.py -q
   ```
7. **Fill in** `docs/m2-w13-plan.md` §Outcome with the drill result and the real
   check count; adjust the gate table (execution leg stays OPEN).
8. **Final gates:** `make static-gates && make lint && make test-section1 &&
   make test-section4`, then commit as `M2 W13: …`. Do not push without the
   owner's go-ahead.

## Notes and traps

- The drill **saturates team A's run cap** (8 synthetic `running` claims owned by
  `w13-saturate`) *before* seeding queued rows: the orchestrator polls
  `run_claims` every 0.5 s and would otherwise requeue the partially seeded run
  mid-flight. The runner's claim is a direct UPDATE and is not cap-bound.
- Run ids passed to `POST /team-runners/{id}/jobs` must be exactly 36 chars
  (`AssignBody`), matching the queue column. The drill pads with `ljust(36,"0")`.
- The drill seeds `run_claims` with `psql` because there is still no BYO
  dispatch endpoint; recorded as a limitation in the evidence.
- `runner_nonces` are pruned inline on the auth path (2× the ±300 s window);
  there is no standalone purge job yet.
- Revocation kills the token on the very next request; an in-flight lease is
  handled by expiry (job `released`, run `orphaned`, never re-queued).
- The image may have been built from the tree **before two comment-only edits**
  to `AssignBody`/drill ids; behaviour is identical (36/36 constraint kept).
- Never commit `.qoder/` (session context) or `__pycache__`.

## Carried from W12 (unchanged)

- W11 design-to-code leg is still **OPEN** (fidelity + provider empty response).
- W13 execution leg is **OPEN**: the runner holds and completes a lease; it does
  not yet execute the approved plan or stream tool events into the orchestrator.
  The seam is `start_execution` in `section4/src/harness_orchestrator/service.py`.
