# M2 W13 status — BYO runner control plane shipped, execution leg open (2026-10-29)

Supersedes the paused checkpoint of the same name. Read `docs/m2-w13-plan.md`
for the threat model, trust assumptions and gate position. The repository is the
source of truth: re-verify any number before relying on it.

## Done (this slice)

| Item | State |
|---|---|
| Migration 0068 (`team_runners`, `runner_nonces`, `runner_jobs`) | shipped, applied to the live DB by the rebuilt migrate image |
| Runner registry + one-time token + revocation | shipped |
| Auth-before-body (dependency), token rotation, nonce purge sweep | shipped (hardening pass) |
| Replay guard (single-use nonce + ±300 s window) | shipped |
| Signed, digest-bound confinement attestation | shipped |
| Team-scoped offer → claim → lease heartbeat → finish | shipped |
| Lease expiry → job `released`, run `orphaned` (never re-queued) | shipped |
| Audit actions (5) + route classification (9) + FK law (49 → 55) | shipped |
| Offline tests | `test_runner_api.py` 10 |
| Live drill | **42/42 PASS** — `docs/briefs/evidence/eval/w13-runner-1793260362.json` |
| `identity.openapi.json` (133 paths) + Python SDK (9 operations) | regenerated from the live service |
| Full suites | S1 1012/54 skipped · S2 48 · S3 128 · S4 1858 · S5 436 · S6 123 · S8 91 · S10 91 · S11 50 · MCP 18 |
| `make lint` (all nine sections), `make static-gates` | green |

## Also repaired on the way (were red on HEAD)

- `docs/eval/corpus.json` had been edited from a stale copy and dropped the
  W11/W12 gate entries; restored, then `w8-quotas-isolation` and
  `w13-byo-runner` added (38 tasks).
- The W8 concurrency drill existed only uncommitted; re-ran against current code
  (**PASS**, `w8-concurrency-1793254624.json`) and committed it with its
  evidence, after fixing 5 lint errors in the script.
- `make lint` was failing in section1 (`test_http_client.py` I001), section4
  (38 errors) and section5 (2 × E501 + unused `pytest`).
- `clients/python/harness_sdk/orchestrator.py` was stale against its recorded
  spec (run-checkpoints list, rewind body).
- Five backend services (github, models, meeting, review, sandbox) were running a
  pre-W12 image and reporting `/ready` 503 `schema-drift`; recreated.

## Known-open items

1. **Execution leg (blocks the roadmap gate).** A runner can hold and complete a
   lease; it does not yet execute the approved plan or stream bounded, scrubbed
   tool events into the orchestrator's state machine. Seam:
   `start_execution` in `section4/src/harness_orchestrator/service.py` must be
   able to dispatch to a runner instead of the local spawner, and the runner's
   `finish` must drive the run to a terminal state. The drill currently works
   around the missing seam by saturating the team's run cap so the 0.5 s queue
   poll cannot grab the seeded claim.
2. **mTLS/request signing, per-runner rate limits, `runner_jobs` retention, and
   any UI** are not shipped. Token rotation and the nonce purge sweep are now
   shipped (auth is a dependency, so anonymous malformed bodies are 401).
4. W11 design-to-code leg remains open (fidelity + provider empty response).

## Reproducing the evidence

```
cd "/Users/user/untitled folder 2/harness"
docker compose --env-file .env -f deploy/docker-compose.yml build identity   # slow: re-downloads the pinned wheels
docker compose --env-file .env -f deploy/docker-compose.yml run --rm migrate
docker compose --env-file .env -f deploy/docker-compose.yml up -d --no-deps \
  identity orchestrator github models meeting review sandbox
python3 scripts/e2e/w13_runner_drill.py
cd section1 && ./.venv/bin/python -m pytest tests/ -q
```

Spec regeneration: `fetch_specs.py` refreshes `identity` correctly but overwrites
`orchestrator.openapi.json` with the live superset (`/health`, `/ready`, and the
W9 routes that the recorded router fragment does not list) — revert that file, or
use `python scripts/spec_drift.py --write`, before running `generate.py`.
