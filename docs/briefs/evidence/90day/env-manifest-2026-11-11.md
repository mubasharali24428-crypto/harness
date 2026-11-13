# Environment manifest & drift report — 2026-11-11 (F-03 / P-02 companion)

Task **F-03** of `docs/plans/90-day-execution-plan-2026-11-11.md`: capture exact running images,
digests, configuration and source mapping; compare with checkout. **Live capture is BLOCKED: the
Docker daemon is not running on this host.** This manifest therefore records (a) the live-state
evidence of absence, (b) the compose-derived intended manifest, (c) the source mapping, and
(d) the drift verdict with the owner action that unblocks it. Nothing was created, stopped,
removed, rebuilt or reconfigured.

## 1. Live state (captured 2026-11-11)

`python3 scripts/ops/stack_tags.py` (read-only; its only external command is `docker ps`; always exits 0):

```
docker ps exited 1: Cannot connect to the Docker daemon at
unix:///Users/user/.docker/run/docker.sock. Is the docker daemon running?
Running services: (none)
Backend services: 0 of 11 expected running
Missing: events-retention, github, identity, meeting, models, orchestrator,
         review, sandbox, sandbox-egress, siem-push, webhook-delivery
Agreement: UNKNOWN (no backend containers found)
Worker daemon: not running
```

**No running images or digests exist to capture.** Any claim about the live stack today would be
fabrication; the last *documented* deployment is the STATUS.md W27 slice 2/3 record (mixed tags,
lagged source — see §4).

## 1b. Live capture update (2026-11-11 evening — daemon started)

Owner-lane fleet started Docker Desktop and attempted the runbook demo boot
(`HARNESS_IMAGE_TAG=w24b1`, project `harness`). Captured live:

- **Mixed-state residue confirmed**: `worker-daemon` was running `vw27s2` while the
  attempted backend set was `vw24b1` — the documented "mixed tags" drift, observed live.
- **Drift gate refused boot (exit 37)**: `harness-migrate-1` logged
  `SCHEMA DRIFT: database has 73 applied migrations but this image declares only 71
  (applied-but-unrecognized here: 0073, 0075)`. The existing `harness_pgdata` volume was
  last migrated by a newer image; w24b1 correctly refuses to talk to a newer schema
  (forward-only migration law). **This is the safety system working, not a bug.**
- Resolution taken for the demo: stop project `harness` (containers only, volumes untouched),
  boot a **fresh isolated project `harness-demo`** with w24b1 + frontend w24b2 on clean volumes.
  Roll-forward alternative for the dev stack: any image built from a commit containing
  `section1/migrations/0075_*.sql` (current checkout has it — verify count at boot).
- F-03 live-capture state moves from **Blocked** to **captured for the demo project**;
  the dev-stack redeploy (A-06, one reviewed image set + digests) remains an owner decision.

## 2. Configuration (non-secret keys only)

`.env` was inspected **only** for `HARNESS_IMAGE_TAG`, `WALG_IMAGE_TAG`, `KEYCLOAK_VERSION`,
`MINIO_IMAGE`: none are set → **compose defaults apply**. No other `.env` content was read.

## 3. Compose-derived intended manifest (`deploy/docker-compose.yml`, line refs at 7c1acf4)

| Service(s) | Image ref | Default tag | Line |
|---|---|---|---|
| 11 backend services (identity, github, models, orchestrator, sandbox, meeting, review, events-retention, sandbox-egress, siem-push, webhook-delivery) | `harness-backend:v${HARNESS_IMAGE_TAG:-dev-local}` | `dev-local` | 15 |
| engine-worker | `harness-engine-worker:v${HARNESS_IMAGE_TAG:-dev-local}` | `dev-local` | 469 |
| worker-daemon | `harness-worker-daemon:v${HARNESS_IMAGE_TAG:-dev-local}` | `dev-local` | 492 |
| frontend | `harness-frontend:v${HARNESS_IMAGE_TAG:-dev-local}` | `dev-local` | 645 |
| proxy (profile) | `caddy:2-alpine` | floating | 686 |
| walg / backup profiles | `harness-db-walg:${WALG_IMAGE_TAG:-v3.0.9}` | `v3.0.9` | 169, 755, 842 |
| db (+ loadtest profile) | `postgres:18-alpine` | floating minor | 991, 1310 |
| keycloak (profile) | `quay.io/keycloak/keycloak:${KEYCLOAK_VERSION:-26.1}` | `26.1` | 1044 |
| minio (profile) | `bitnamilegacy/minio@sha256:451fe685…349c` | **digest-pinned** | 1092, 1121 |
| prometheus / alertmanager (observability) | `prom/prometheus:v2.53.0` / `prom/alertmanager:v0.28.1` | pinned | 1228, 1259 |

Note: with no `HARNESS_IMAGE_TAG` set, a boot today would build/tag `dev-local` — **not** a reviewed
release tag. A-06 (one reviewed image set, digests recorded) requires the owner to set the tag
explicitly at deploy time.

## 4. Source mapping & drift verdict

| Layer | State | Evidence |
|---|---|---|
| Checkout | `w28-s1-delegate` @ `7c1acf4` (tip = the 1 unpushed commit); 23 untracked entries | `docs/plans/branch-inventory-2026-11-11.md` |
| Pushed / PR | PR #24 open, head `19f5cae` per RC plan (not re-verified live — one `gh pr view` TLS timeout); **15/15 CI checks pass**, run 42195189977 | `gh pr checks 24`, 2026-11-11 |
| origin/master | `1753171`, local `master` 5 behind / 0 ahead; no fetch run | inventory §A |
| Live stack | **absent** (daemon down) | §1 |
| Last documented deploy | mixed image tags, source lagged (W27 slices 2/3) | STATUS.md; `stack_tags.py` docstring; FUTURE PLAN §2 |

**Drift verdict: UNVERIFIABLE live** (no daemon). Documented drift (mixed tags, lagged source) is
*unresolved until a fresh capture*. This matches FUTURE PLAN F-03's warning that current live state
was not independently checked.

## 5. Owner action to unblock (A-06 path)

1. Start Docker Desktop; confirm `docker ps` responds.
2. Re-run `python3 scripts/ops/stack_tags.py`; paste output into `week-01.md` (test-agent handoff).
3. Owner chooses the release revision (PR #24 merge decision), sets `HARNESS_IMAGE_TAG` explicitly,
   deploys **one** image set, records digests (`docker image inspect --format '{{.RepoDigests}}'`),
   re-runs `stack_tags.py` → agreement must be ONE tag.
4. Real-provider smoke + rollback/restore drill on the deployed tag (A-06 acceptance).

Evidence state: manifest **Implemented—unverified** (compose-derived, owner review pending);
live capture **Blocked** (daemon down — owner action §5.1). No readiness claim is made here.