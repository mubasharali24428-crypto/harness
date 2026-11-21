# A-06 one-tag-set redeploy runbook — `vv90r5` (owner executes)

Everything here is prepared and verified; the deploy itself is the owner's
(FUTURE PLAN §3: deploys are R). Total owner time: ~10 minutes.

## 0. What this closes

- Week-03 finding: RC `Agreement: MIXED` (`vv90i13` ×10, `vv90r4` ×2,
  identity + review untagged) — replaced by ONE set.
- Week-02 finding: deployed gate predates the A-01 argument checks — the new
  worker image's gate is byte-identical to HEAD (verified: diff 0 lines,
  `OUT_OF_SCOPE` present).
- RC schema 0075 → 0076 (P-17 `project_guest_guests` lands).

Prepared set: `docs/briefs/evidence/90day/image-manifest-vv90r5-proposed.json`
(4 images, local IDs, built from `2de9167`; migrate selftested on scratch PG;
e2e compose journey green in CI at this revision).

## 1. Deploy (dry mode, zero provider keys)

```sh
cd "/Users/user/untitled folder 2/harness"
export HARNESS_IMAGE_TAG=v90r5            # compose prefixes v -> vv90r5/vw90r5
export ENGINE_LIVE_MODE=dry
export COMPOSE_PROFILES=worker
rc() { docker compose -p harness-rc --env-file .env \
  -f deploy/docker-compose.yml -f deploy/docker-compose.rc.yml --profile worker "$@"; }
rc build            # confirms the local builds match; skips if cached
rc up -d --no-build --pull never --wait --wait-timeout 150
python3 scripts/ops/stack_tags.py --project harness-rc   # expect Agreement: AGREE
```

Note: `harness-backend:vv90r5` and friends were built locally from `2de9167`;
if you prefer CI-built images, re-run the three build jobs on master after
merging #24 and re-point the tag. Local IDs are recorded in the manifest.

## 2. Verify (5 checks, 5 minutes)

1. `stack_tags.py --project harness-rc` → **Agreement: AGREE**, 11/11, Missing none.
2. `docker exec harness-rc-db-1 psql -U harness -d harness -tAc "select count(*), max(version) from schema_migrations"` → **74 | 0076**.
3. `docker inspect harness-rc-identity-1 --format '{{.Config.Image}}'` →
   `harness-backend:vv90r5` (no more untagged `abdc28b02505`).
4. Guest gate live: `curl -s -o /dev/null -w '%{http_code}\n'
   http://localhost:18001/api/v1/me` without a session → 401 (resolver runs);
   with a guest session → 403 `GUEST_PORTAL_ONLY` on anything unlisted.
5. Frontend: `http://localhost:3011` opens; baked endpoints are the RC ports
   (they are built in — see the manifest's `frontend_args`).

## 3. Rollback

`rc stop` leaves volumes intact; the previous set's images still exist
(`vv90i13`/`vv90r4`/untagged) so `HARNESS_IMAGE_TAG=v90i13 rc up -d …`
restores the old stack. Schema is FORWARD-ONLY: rolling the image back to
pre-0076 code against a 0076 database is refused by the migrate drift gate —
if 0076 must be undone, restore the RC scratch DB from its dump instead
(it is not the live database; nothing here touches `harness-db-1`).

## 4. After the deploy

- Attach the fresh `stack_tags.py` output to week-04 ledger §A-06 and flip the
  drift finding in week-03 §5 to resolved (owner or fleet, one line).
- The verdict-bot workflow (`.github/workflows/verdict-bot.yml`) can then be
  exercised with a real RUN_ID (dry-run first).
