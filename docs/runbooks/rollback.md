# Runbook: Rollback — harness Docker stack

Mission 18 C6 (OPS-24 residual, v0). Owner: deploy lane. Related:
[ops.md](ops.md) (topology, bring-up env, DNS crash-loop),
[backup-and-pitr.md](backup-and-pitr.md) (backups, PITR restore drill,
RPO/RTO). This file is the DECISION layer for a bad deploy; it does not
duplicate those. No schema down-migrations exist (`section1/migrations/` is
forward-only by law) — "schema rollback" here always means **restore from
backup**.

---

## 0. TL;DR decision table

| What's on fire | Roll back | Time box |
|---|---|---|
| Bad app code, schema untouched (this deploy added no migrations, or its migrations are harmless) | **Image only** (§2) | minutes |
| Deploy applied migrations AND code is broken in a way the new schema causes or enables | **Image + schema** (§4) | PITR-bound (see backup-and-pitr.md RTO) |
| `DRIFT GATE REFUSED BOOT` / migrate exits 1 at deploy time | Nothing is broken yet — decide via §3 | minutes |

Collaboration features only (shared runs misbehaving, a team must leave the
pilot): no image or schema change is needed; use the flag rollback in
[pilot.md](pilot.md) section 5 (tested by
`section4/tests/unit/test_pilot_flag_rollback.py`).

If you are unsure whether the bad behavior involves the new schema, treat it
as image+schema and read §3 first — §2 is only safe when the DB never moved.

---

## 1. Image tags (M18-C6)

Every compose service that builds an image now pins an explicit tag — no
floating `:latest` anywhere in the app tier:

- Backend tier (identity/github/models/orchestrator/sandbox/meeting/review +
  the one-shot `migrate`, all one image): `harness-backend:v<TAG>`
- Frontend: `harness-frontend:v<TAG>`
- `<TAG>` = `YYYYMMDD-HHMM` UTC of the build, set once per deploy via
  `HARNESS_IMAGE_TAG` in `deploy/.env` (or shell env). Default in the file is
  `dev-local` so a bare local `up --build` still works.
- Each `build:` also emits a rolling `latest` alias as a convenience ONLY;
  compose itself always resolves the explicit `v<TAG>`.
- The Postgres/wal-g image keeps its own upstream-pinned tag
  (`WALG_IMAGE_TAG`, default `v3.0.9`) — DB engine versions roll on a
  different schedule than app code; see OPS-17 for the minor-pin caveat.

Because every backend service shares ONE tag variable, rollback is a single
edit + `up -d`: compose recreates exactly the services whose image reference
changed. Old tags stay on disk until pruned, which is what makes this runbook
possible at all (before M18-C6 an `up --build` left only dangling layers).

Tag hygiene per deploy (do this BEFORE `up`, not after):

```sh
cd /path/to/harness/deploy
TAG="$(date -u +%Y%m%d-%H%M)"
grep -q "^HARNESS_IMAGE_TAG=" .env && sed -i '' "s/^HARNESS_IMAGE_TAG=.*/HARNESS_IMAGE_TAG=$TAG/" .env \
  || echo "HARNESS_IMAGE_TAG=$TAG" >> .env   # Linux: sed -i without ''
docker compose -f docker-compose.yml --env-file .env up -d --build
docker images 'harness-*' --format '{{.Repository}}:{{.Tag}}  {{.ID}}  {{.CreatedSince}}'
# Record the previous tag somewhere greppable before it scrolls away:
echo "$(date -u +%FT%TZ) deployed v$TAG (prev: <fill-in>)" >> DEPLOYLOG.txt
```

## 2. Roll back IMAGE ONLY (schema compatible)

Use when: the previous tag ran fine, and THIS deploy either shipped no new
migrations or its migrations are backward-compatible with the old code
(added columns/tables/nullable fields usually are — the drift gate will tell
you instantly if they're not).

```sh
cd /path/to/harness/deploy

# 1. Pick the last known-good tag (from DEPLOYLOG.txt / docker images):
PREV=20260825-1145                       # <-- the tag you're rolling back TO
sed -i '' "s/^HARNESS_IMAGE_TAG=.*/HARNESS_IMAGE_TAG=$PREV/" .env

# 2. Recreate the whole app tier on the old image (db volume untouched):
docker compose -f docker-compose.yml --env-file .env up -d --no-build

# 3. Confirm every service went healthy (exit when all green):
docker compose -f docker-compose.yml ps

# 4. Smoke:
curl -fsS localhost:8001/health    # {"ok":true} — repeat :8002..:8008
curl -fsS localhost:3000           # frontend answers
```

Notes:

- `up -d` (no `--build`) reuses the EXISTING tagged images — nothing rebuilds,
  nothing overwrites the good image.
- If `migrate` shows as exited-but-failed after the swap, check whether the
  OLD image's chain is behind the DB (next section) — do not just retry.
- Frontend rolls with the same tag variable; if you must roll tiers separately
  (rare — API contract drift), override per-service instead:
  `docker compose up -d --no-build identity github ...` after editing only
  their `image:` lines via a compose.override file.

## 3. The drift gate — what refusing boot means (M18-C6)

Both boot paths now compare `schema_migrations` against the migration files
shipped INSIDE the running image before anything starts:

- the `migrate` one-shot checks FIRST and applies nothing on mismatch
  (entrypoint exits 1);
- every app-tier container runs the same check read-only before uvicorn
  (entrypoint exits 1); `restart: unless-stopped` then keeps it parked
  instead of crash-looping against a schema it doesn't understand.

Pass condition: **applied ⊆ declared**. Pending migrations (DB behind image)
pass and get applied by `migrate` as usual — that's a normal deploy, not an
incident.

Failure looks like:

```
DRIFT GATE REFUSED BOOT: SCHEMA DRIFT: database has 27 applied migrations but
this image declares only 25 (applied-but-unrecognized here: 0027_cost_indexes,
0099_x). This image's code predates the schema it would talk to. ROLL BACK THE
SCHEMA FIRST (restore a pre-deploy backup per docs/runbooks/backup-and-pitr.md)
or roll FORWARD to an image whose migrations include 0099_x — i.e. any
harness-backend tag built from a commit containing
section1/migrations/0099_x*.sql. Never boot app servers against a mismatched
schema; details: docs/runbooks/rollback.md.
```

Decision tree when you see it:

1. **Read the extra versions named.** They are the migrations your bad deploy
   applied before things went wrong.
2. **Was the release otherwise sound?** Then the fastest exit is FORWARD:
   redeploy a fixed image built from the same commit generation — its chain
   includes those files, the gate passes, done. Fix-forward beats PITR.
3. **Must go back?** You need the DB to match the OLD image again ⇒ full
   image+schema rollback (§4): PITR to just-before the deploy, then §2 onto
   the previous tag.
4. Never delete rows from `schema_migrations` by hand and never hand-edit
   applied DDL — forward-only ledger law; the audit story depends on it.

Why so strict: an old image pointed at a newer schema doesn't fail loudly —
it silently ignores new columns/tables (the exact OPS-16 failure where live
code ran 14 migrations behind its own DB) until some code path hits the gap.
The gate turns that silent wrongness into a refused boot at deploy time.

## 4. Roll back IMAGE + SCHEMA (PITR interplay)

Schema is forward-only: undoing applied migrations means restoring the
database to a point in time before the deploy, then starting the previous
image against it. Full mechanics, storage targets, and the timed drill live
in [backup-and-pitr.md](backup-and-pitr.md) — the sequence HERE is the
rollback-shaped slice:

```sh
# 0. Freeze the scene (evidence + stops bad writes from landing on the
#    drifted schema):
docker compose -f docker-compose.yml stop identity github models orchestrator sandbox meeting review frontend

# 1. Note the deploy timestamp T (DEPLOYLOG.txt entry) — recovery target is
#    strictly BEFORE T.
docker exec harness-db-1 psql -U harness -d harness \
  -c "select version, applied_at from schema_migrations order by applied_at desc limit 5;"
#    ^ confirms WHICH versions the bad deploy added (matches gate output)

# 2. Restore per backup-and-pitr.md §drill steps [5]-[7], with:
#    target time = (T - safety margin), same WALG_FILE_PREFIX/S3 prefix as
#    production, restore into a SEPARATE data dir/container (never in place).
#    Verify the restored cluster's schema_migrations ends BELOW the bad
#    deploy's first new version before proceeding.

# 3. Cut over: stop the old db container, repoint/rename volumes per
#    backup-and-pitr.md, bring up db alone, wait healthy.

# 4. Now the image half of §2 (previous tag, --no-build):
cd deploy && sed -i '' "s/^HARNESS_IMAGE_TAG=.*/HARNESS_IMAGE_TAG=$PREV/" .env
docker compose -f docker-compose.yml --env-file .env up -d --no-build

# 5. Verify (§5 below) — including that the drift gate says OK: restored DB ⊆
#    rolled-back image chain.
```

Caveats (all detailed in backup-and-pitr.md):

- RPO: WAL archives continuously; archive_timeout bounds how much tail you
  lose (default 60s window). Anything written between the restore target and
  the freeze is gone unless you export it separately from the drifted DB.
- Restores land on a NEW timeline — subsequent backups continue from there.
- The drill procedure has been executed end-to-end (2026-08-24 evidence in
  backup-and-pitr.md); the rollback-shaped variant above has NOT been drilled
  against prod data yet — that is the v1 follow-up for this runbook.

## 5. Verification checklist (after ANY rollback)

```sh
cd /path/to/harness/deploy
# 1. Images: every app-tier container on the intended tag (not :latest):
docker compose -f docker-compose.yml ps --format '{{.Name}} {{.Image}} {{.Status}}'

# 2. Drift gate opinion — should be OK, not REFUSED. (--env-file is NOT
#    optional here: without it the one-shot dies at the HARNESS_MASTER_KEY
#    guard before the drift gate ever runs. Feed it the same key env the app
#    tier booted with — HARNESS_MASTER_KEY, or HARNESS_ALLOW_DEV_MASTER_KEY=1
#    locally; verified live 2026-08-26, M23-Z4 release-readiness.)
docker compose -f docker-compose.yml --env-file .env run --rm migrate 2>&1 | grep -E "drift gate|applied"
#    expect: "drift gate: OK ..." and NO newly applied files

# 3. Ledger sanity: max(version) equals the PREVIOUS deploy's newest file:
docker exec harness-db-1 psql -U harness -d harness -tc 'select max(version) from schema_migrations;'
#    e.g. 0026 after rolling back from a 0027 deploy

# 4. Health edges + frontend:
for p in 8001 8002 8003 8004 8005 8006 8008; do curl -fsS localhost:$p/health; echo; done
curl -fsSI localhost:3000 | head -1

# 5. Events flowing again (seq advancing, no replay storms in logs):
docker logs harness-orchestrator-1 --since 2m 2>&1 | tail -20
```

Record the rollback in DEPLOYLOG.txt (tag you left behind, tag you landed on,
whether schema was touched, PITR target if any) — next incident reads it.

## 6. Rehearsal log (v0)

- **2026-08-25 (local rehearsal, M18-C6)** — documented procedure executed
  end-to-end on this machine against the live dev stack; transcript with raw
  command output: `docs/briefs/m18-c6-rehearsal-evidence.md`. Summary:
  deployed current tree under tag `v20260825-HHMM`, verified health, then
  rolled back one tag (`--no-build` path, §2) and re-verified; drift gate
  exercised positive + negative on a scratch database (§3 trip condition);
  schema+image combined path (§4) covered by the 2026-08-24 PITR drill plus
  the gate tests, still un-drilled as one sequence (tracked above).

- **2026-10-18 (ops acceptance, part 28)** - migration-level rollback rehearsal
  on a scratch PG18 (port 55432), `deploy/runners/migrate.py` driven directly:
  0001->0035 applies clean from empty (34 files, 0014 intentionally absent) and
  the re-run is a no-op; a failing migration (`0099_bad.sql`) leaves NO partial
  DDL and NO ledger row (whole chain runs in one transaction); `pg_dump -Fc`
  taken at 0034, 0035 applied, old-image drift check raised `SchemaDriftError`
  naming 0035, then `dropdb`/`pg_restore` returned the ledger to 0034 and the
  old-image drift check passed. There is no down-migration path: restore is the
  only schema rollback. Not covered: docker image swap, PITR (no Docker
  daemon in that session). Evidence: `docs/briefs/evidence/s1/ops-acceptance.md`.
