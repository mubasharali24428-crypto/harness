# M18-C6 rollback rehearsal — raw evidence (2026-08-25)

Lane C6 executed the procedure in `docs/runbooks/rollback.md` once, locally,
against a THROWAWAY copy of the stack. Method (chosen so the live dev stack
and its pgdata were structurally untouchable):

- Isolated compose project via `deploy/docker-compose.rehearsal.yml`
  (project `c6rehearsal`): all host `ports:` reset, fresh named volume, and a
  pinned DISTINCT network name (`c6rehearsal_default`) — the base file pins
  `networks.default.name=harness_default`, and without that block a second
  project silently joins the live network (hit for real, see §0).
- Throwaway master key via shell env (`HARNESS_MASTER_KEY=c6-rehearsal-…`),
  per ops.md's requirement that a real key be present; ops.md's launchd
  bring-up was intentionally NOT used so no live secret was read.
- Own `WALG_FILE_PREFIX=/tmp/harness-walg-c6-rehearsal` so the rehearsal
  cluster could never write WAL into the LIVE archive repository.
- Frontend service excluded: `deploy/frontend.Dockerfile` fails to build at
  clean HEAD (webpack: `node:crypto` UnhandledSchemeError from
  `section7/src/lib/useEventStream.ts`; prod frontend image predates that
  commit). Pre-existing, sibling-owned, out of C6 scope — reported, not fixed.

## Phase 0 — config validation

```
$ docker compose -f deploy/docker-compose.yml --env-file deploy/.env config -q   -> OK
$ docker compose -f deploy/docker-compose.yml config -q                          -> OK (no .env)
$ sh -n deploy/entrypoint.sh                                                     -> clean
```

## §0 Lesson caught mid-rehearsal (first attempt)

The very first isolated `up` used the base file's implicit network and joined
`harness_default`: Docker DNS then returned TWO A-records for `db` (live PG +
rehearsal PG). Result observed live: the drift gate read the LIVE db ("OK")
while `migrate()` hit the FRESH db ("relation schema_migrations does not
exist") — same boot, two databases. Fixed by pinning
`c6rehearsal_default` in the override; teardown removed the stray attachment
(`db` back to a single A-record, live health checked green). Recorded as a
pitfall for anyone running second compose projects against this repo.

## Phase 1 — DEPLOY current tree (rollback.md §1 hygiene)

Tag scheme note (honest): compose resolves `harness-backend:v${HARNESS_IMAGE_TAG:-dev-local}`,
i.e. the value must be the BARE stamp `YYYYMMDD-HHMM`. Two early attempts
exported already-prefixed values (`v20260825-…` ⇒ images tagged `vv20260825-…`)
— cosmetic, corrected below; the runbook examples use bare stamps.

```
$ export HARNESS_IMAGE_TAG=20260825-1023 HARNESS_ALLOW_DEV_MASTER_KEY=1
$ docker compose -f docker-compose.yml -f docker-compose.rehearsal.yml \
    --env-file .env up -d --build db migrate identity github models orchestrator sandbox meeting review
c6rehearsal-db-1        harness-db-walg:v3.0.9            Up (healthy)
c6rehearsal-migrate-1   harness-backend:v20260825-1023    Exited (0)
c6rehearsal-{identity..review}-1  harness-backend:v20260825-1023  Up (healthy) ×7

$ docker logs c6rehearsal-migrate-1 | head -1
drift gate: OK (applied migrations match this image's chain)
$ docker logs c6rehearsal-migrate-1 | grep -c '^applied'      -> 26   (0001→0027)
$ docker exec c6rehearsal-db-1 psql -U harness -d harness -tc \
    'select count(*), max(version) from schema_migrations'     -> 26 | 0027
$ docker exec c6rehearsal-identity-1 python -c "import urllib.request; …/health"
{"ok":true}
```

(Note: 26 migrations because sibling lane C3's `0027_cost_ledger_indexes.sql`
was already on disk uncommitted; the rehearsal stack legitimately picked it
up — which is what makes phase 3's cross-version scenario real.)

## Phase 2 — ROLLBACK ONE TAG (rollback.md §2)

Attempt 1 — target tag did not exist locally:

```
Error response from daemon: No such image: harness-backend:vv20260824-1700
(pull access denied … )   # nothing recreated; tier stayed healthy on old tag
```

This is the intended safety property: a typo'd/garbage tag cannot take the
tier down; compose refuses and leaves the running generation alone.

Attempt 2 — the REAL previous-generation image. `388019d2ac8c` (built ~6h
before, pre-C6 tree: max migration **0026**, **no drift gate inside**) existed
only under the legacy default tag `harness-migrate:latest`. Tagged per the new
scheme and rolled back:

```
$ docker tag 388019d2ac8c harness-backend:v20260824-1700
$ export HARNESS_IMAGE_TAG=20260824-1700
$ docker compose … up -d --no-build migrate identity github models orchestrator sandbox meeting review
c6rehearsal-{identity..review}-1  harness-backend:v20260824-1700  Up (healthy) ×7
$ docker exec c6rehearsal-identity-1 python -c "… /health"   -> {"ok":true}
$ docker exec c6rehearsal-db-1 psql -U harness -d harness -tc \
    'select count(*), max(version) from schema_migrations'    -> 26 | 0027   # UNCHANGED
```

HONEST CAVEAT (this is the v0 story's known limit): the rolled-back-to image
predates C6, so it carries NO drift gate — nothing refused this recreation,
and the DB stayed one migration ahead of the image chain (26 rows vs 26-file…
chain ending 0026 vs ledger 0027). That direction (applied ⊇ declared, pure
additive index migration) is exactly the benign case runbook §2 declares
safe, and indeed every service ran healthy. The gate cannot protect operators
from images older than the gate itself; it protects everything FROM those
images going forward.

## Phase 3 — DRIFT-GATE TRIP on simulated mismatch (scratch DB)

Simulated a genuinely hostile mismatch (ledger ahead of image chain by an
unknown version) inside the throwaway DB only:

```
$ docker exec c6rehearsal-db-1 psql -U harness -d harness -tc \
    "insert into schema_migrations (version) values ('0099_rehearsal_sim') returning version;"
 0099_rehearsal_sim
```

D1 — current-image one-shot `migrate` (the compose deploy gate):

```
$ docker compose … run --no-deps --rm migrate ; echo exit=$?
DRIFT GATE REFUSED BOOT: SCHEMA DRIFT: database has 27 applied migrations but
this image declares only 26 (applied-but-unrecognized here: 0099_rehearsal_sim).
This image's code predates the schema it would talk to. ROLL BACK THE SCHEMA
FIRST (restore a pre-deploy backup per docs/runbooks/backup-and-pitr.md) or
roll FORWARD to an image whose migrations include 0099_rehearsal_sim — i.e.
any harness-backend tag built from a commit containing
section1/migrations/0099_rehearsal_sim_*.sql. Never boot app servers against a
mismatched schema; details: docs/runbooks/rollback.md.
migrate_exit=37
```

D2 — `up -d identity` WITH dependencies: compose refuses to start the app
tier because its `migrate` dependency exited 37:
`service "migrate" didn't complete successfully: exit 37` → identity left
`Created`, never started.

D3 — `up -d --no-deps --no-build identity` (per-container pre-flight through
the real entrypoint): every start attempt prints the same
`DRIFT GATE REFUSED BOOT: SCHEMA DRIFT: …` line and exits 1;
`restart: unless-stopped` then parks it:

```
c6rehearsal-identity-1  Restarting (1) Less than a second ago
```

— i.e. the app tier can NEVER come up serving against the drifted schema.

## Phase 4 — teardown + live-stack integrity proof

```
$ docker compose … -p c6rehearsal down -v --remove-orphans
Volume c6rehearsal_pgdata Removed / Network c6rehearsal_default Removed
-> 0 c6rehearsal containers, 0 c6rehearsal volumes; /tmp scratch prefix rm -rf'd

$ docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
harness-{db,frontend,github,identity,models,orchestrator,sandbox,meeting,review,walg-db}-1
  — all "Up 6 hours", none recreated during the rehearsal
$ docker exec harness-db-1 psql -U harness -d harness -tc \
    'select count(*), max(version) from schema_migrations'   -> 25 | 0026   (unchanged)
```

## Unit-level gate coverage

```
$ cd section1 && ./.venv/bin/python -m pytest tests/test_migrate_drift_gate.py -q
6 passed in 0.04s
(subset / exact-match / empty-ledger pass; rolled-forward + ghost-version
refusals; declared_versions()==disk; migrate() refuses & mutates nothing)
```

## Verdict

| Runbook claim | Evidence |
|---|---|
| Explicit tags; flip one var to roll back | Phase 1/2 ps output (two generations side by side) |
| Bad/missing tag cannot take the tier down | Phase 2 attempt 1 (compose refusal, tier untouched) |
| Image-only rollback is minutes-fast, non-destructive | Phase 2 attempt 2 (healthy ×7, ledger byte-identical) |
| Deploy-time gate refuses drifted DB (migrate path) | Phase 3 D1 (exit 37 + operator message) |
| Gate blocks the whole `up` via depends_on | Phase 3 D2 |
| Per-container pre-flight refuses forever | Phase 3 D3 (Restarting (1), never Up) |
| Rehearsal isolates from live data/network | §0 lesson + Phase 4 integrity proof |

Not covered here (tracked as follow-up in rollback.md §6): the full
image+schema PITR sequence (§4 of the runbook) end-to-end in one sitting —
its halves are proven separately (2026-08-24 PITR drill; gate tests above).

## Addendum — same-day post-rehearsal changes (attribution)

Two changes to `check_drift` landed AFTER phases 1–4 were captured; neither
invalidates the evidence above (the refusal message and exit codes are
byte-identical; only the missing-ledger path changed):

1. **M18-C4** found live that a brand-new database exited 37 at the gate
   ("cannot read schema_migrations … run the migrate service first" — circular
   advice on the very first boot) and changed the semantic: an UNDEFINED
   ledger table now means "nothing applied yet" and PASSES.
2. **C6 portability fix**: C4's implementation probed
   `information_schema.tables`, which is Postgres-only and broke every
   SQLite-backed section1 test (5 failed with "no such table:
   information_schema.tables"). Replaced with SQLAlchemy `Inspector.has_table`
   (`conn.run_sync`) — dialect-portable, same semantic. Test updated to pin
   the new contract (missing/empty ledger ⇒ pass; only existing-but-unrecognized
   versions refuse): 6/6 green after merge.
