# Step-3 operations checklist — turning the reviewed deployment into an operated one

Owner checklist for the "live production" step 3 (after the PR #24 merge and the
vv90r6 one-tag-set redeploy). Every line names the exact file to touch and what
"done" looks like. Total owner time: ~30 minutes.

## 1. Grafana dashboards (10 min)

Built and committed, waiting to be imported:

- `deploy/observability/dashboards/harness-overview.json`
- `deploy/observability/dashboards/harness-cost-latency.json`
- Prometheus scrape config: `deploy/observability/prometheus.yml`

Steps:

1. Start the observability profile of the RC stack (or point your existing
   Grafana at the stack's Prometheus).
2. Import both dashboard JSONs (Grafana → Dashboards → Import → upload file).
3. Verify: the overview panel shows the running containers; the cost panel
   shows `cost_ledger` rates (zero rows until a provider key goes live —
   an empty chart is correct, not broken).

Done when: both dashboards render against the live stack's metrics endpoint.

## 2. Offsite backup target (5 min, needs a second machine)

`deploy/backup/nightly.sh:63` already rsyncs both backup roots when
`OFFSITE_RSYNC_TARGET` is set — until then, every backup exists on one machine
only, which is not a backup posture.

1. Put `OFFSITE_RSYNC_TARGET=user@host:/path/` into the env file nightly.sh
   reads (see the comment at `deploy/backup/nightly.sh:24`; an ssh key is
   expected).
2. Run `./deploy/backup/nightly.sh --if-due` once by hand and confirm the
   rsync line succeeds.

Done when: the next nightly log contains a successful offsite copy AND
`store_verify.py` passes on a file fetched FROM the offsite target (verify the
copy, not just the send).

## 3. Demo db port collision (2 min decision)

`deploy/docker-compose.yml:258` binds the demo db on `127.0.0.1:5499`, which is
also the port the live db exposes for host-side tests (`.env:37-39`). When both
stacks are up, whichever starts second fails its port bind and — the November
finding — ends up network-detached with `{}` networks.

Decision for the owner (pick one):

- **(a) move the demo db** to `127.0.0.1:5498` (one-line change in
  `deploy/docker-compose.yml` + the demo stack's env) — recommended, zero
  blast radius;
- **(b) keep 5499 for demo and drop the live db's host bind** (tests use the
  test-container path instead).

Done when: both stacks run simultaneously and `docker inspect` shows no
container with `"Networks": {}`.

## 4. Scratch-restore drill (10 min, needs owner go)

The restore path exists and is scripted: `deploy/backup/restore.sh` (live WAL-G
restore) and `deploy/backup/store-restore.sh` + `verify_restore.sh` (store
snapshots). The drill = restore the latest store snapshot into a THROWAWAY
container (never onto `harness-db-1`), run `verify_restore.sh`, record the
result in the backup ledger row.

Done when: the ledger row says "restored <snapshot> in <minutes>, verify PASS".
Until then the backup story is "taken", not "restorable".

## 5. After all four: what this closes

With these, the deployment stops being "correct but unoperated": metrics are
watchable, backups leave the machine and are provably restorable, and the port
collision can't silently detach a stack again. That is the difference between
"pilot-ready ~70%" and "~85%" in the readiness assessment — the rest is the
first real team.
