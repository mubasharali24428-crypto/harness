# Task 45 — readiness drift (45a documented; 45b BLOCKED — owner Mac)

**45a (cloud): DONE — documentation only. 45b: BLOCKED — needs the owner's Mac (Docker + the RC stack).**
The manifest was deliberately **not** edited: no redeploy has happened, so no migration field may move.

## Current state (measured in the cloud, 2026-11-25, branch tip bce3aa8)
```
$ make readiness-drift      # == readiness_drift.py --ci --manifest docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json
ok   manifest git_head 0d9da4a is on HEAD's lineage (HEAD is 250 commit(s) ahead; a redeploy refreshes the manifest)
DRIFT migrations: disk 79|0081 != manifest 74|0076 — a merged migration is not in the reviewed deployed set; redeploy, then --write-manifest
drift: 1 item(s)                                                  # exit 1
```
This is the check working as designed (G-A): migrations 0077–0081 are on the branch but not in the deployed set (vv90r6, schema 74|0076). It stays red in CI until the RC stack is redeployed and a fresh manifest is committed.

## Ordering warning (read before redeploying)
Task 42 (D2 soft-remove) adds a **new migration** (expected 0082). Redeploy and write the manifest **after Task 42 lands**, otherwise the manifest goes stale again the moment 42 merges. Whatever the head migration is at redeploy time is what `--write-manifest` records (it reads the live DB), and `--ci` then compares it with the files on disk at the commit you commit the manifest in.

## Owner commands (Mac, repo root, Docker running) — 45b
Based on `docs/briefs/evidence/90day/a06-redeploy-runbook-vv90r5.md` and `docs/runbooks/redeploy-worker-and-orchestrator.md` (compose env-file rules). Pick a new tag; `v90r7` below is a placeholder (compose prefixes `v`).

```sh
git fetch origin && git checkout w28-s2-sprints1-4 && git pull --ff-only   # the commit you want deployed
export HARNESS_IMAGE_TAG=v90r7 ENGINE_LIVE_MODE=dry COMPOSE_PROFILES=worker
rc() { docker compose -p harness-rc --env-file .env \
  -f deploy/docker-compose.yml -f deploy/docker-compose.rc.yml --profile worker "$@"; }
rc build
rc up -d --no-build --pull never --wait --wait-timeout 150

# verify ONE tag set, schema at the on-disk head, gate parity
python3 scripts/ops/stack_tags.py --project harness-rc          # expect Agreement: AGREE, Missing none
docker exec harness-rc-db-1 psql -U harness -d harness -tAc \
  "select count(*), max(version) from schema_migrations"          # expect the on-disk head (79|0081 today; one more after Task 42)
make readiness-drift-live                                         # live stack vs OLD manifest: expected to show drift (images/schema) — confirms the tool sees the redeploy

# write the new manifest from the live stack (read-only inspection; refuses a MIXED stack)
make readiness-manifest        # == readiness_drift.py --write-manifest docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json --project harness-rc
```
Notes:
- `make readiness-manifest` **overwrites** the file named by `READINESS_MANIFEST` in the Makefile (still `...-vv90r6-deployed.json`). Either accept that (git keeps the vv90r6 record) or first run `python3 scripts/ops/readiness_drift.py --write-manifest docs/briefs/evidence/90day/image-manifest-<newtag>-deployed.json --project harness-rc` and point `READINESS_MANIFEST` at the new file in the same commit.
- Hand-fill the narrative fields of the manifest (`tag`, `git_head` note, `verification`) the way the vv90r6 file does; the tool writes images, digests, schema and the shell-gate hash.
- Then: `make readiness-drift` (CI mode) must exit 0 locally, commit the manifest, push, and confirm the CI job `readiness drift (manifest vs HEAD)` is green.
- Rollback is image-tag only; the schema is forward-only (see the vv90r5 runbook §3).

## Mac handback (paste)
```
Mac truth at SHA <sha>:
- stack_tags Agreement: AGREE/MIXED
- schema_migrations: <count>|<max>
- make readiness-drift rc=
- manifest file committed: Y/N  (path)
```
