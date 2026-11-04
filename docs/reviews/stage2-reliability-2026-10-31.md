# Stage 2 — Reliability and ownership (W17): engineering record

Taken 2026-10-31 at revision **`0bc3128`** (`W17 Stage-2: event-freshness histogram
end to end + verifiable alert routing`), on top of the lane's `28a7f04`.

Stage 2's exit evidence, quoted from [`roadmap-3.0.md`](../roadmap-3.0.md) line 45:

> "Measured capacity and failure outcomes, backup/alert verification, named SLO and
> on-call owner. Provider experiments require agreed budget."

Three demanded outcomes. Engineering closed the measurement half of two of them and
can close none of the third. That split is the substance of this record.

## What was actually missing (recon, not assumption)

The Stage-1 residual named two gaps. Recon against the running stack found the
picture was better *and* worse than the runbooks said:

| Runbook claim | Live reality on 2026-10-31 |
|---|---|
| "No production end-to-end freshness histogram is currently confirmed" | A live-delivery histogram **did** exist (`harness_event_delivery_lag_seconds`, part 46) and **was** scraped from `identity:8001` — but with zero observations, no replay leg, and no alert reading it. |
| "there is no configured Alertmanager delivery destination" | Alertmanager **was** configured and running, pointing `smtp_smarthost` at `mailpit:1025` — a container that **exited 6 days earlier**. |
| (unstated) | `HarnessBackupStale` was **firing** with a real 6.1-day-old backup: the evaluation chain works, delivery is dead. |

So the honest work was not "build the missing thing" — it was closing the two real
holes (replay leg + alert; delivery to a live target) and *confirming end to end*
what had only ever been unit-tested.

## Unit A — Linux-confined shell cleanup (committed `30a6b0a`)

The Linux-host leg of W17 confinement, previously blocked for want of a Linux host.
Driven under **Landlock** in a real container: `exec` budget expires → runner
`os.killpg` → detached grandchild gone.

- PASS, with a false-pass guard: `_alive()` reads `/proc/<pid>/stat` (the container
  has no `ps`, so a `ps`-based check would have reported a live process as dead),
  plus a liveness **positive control** proving the check is not blind.
- `scripts/e2e/w17_linux_shell_cleanup_drill.py`,
  `docs/briefs/evidence/w17/w17-linux-shell-cleanup.json`.

## Unit B — eligible-request availability counter (committed `88d379c`)

The availability SLI had a numerator proxy (5xx rate) but no denominator. Added
`harness_eligible_requests_total{outcome="ok|error"}` to both parallel
`MetricsCollector` copies (`deploy/runners/observability.py`,
`section4/src/harness_orchestrator/api.py`), excluding caller 4xx, synthetic probes
(`/health`, `/ready`, `/metrics`) and a declared `HARNESS_MAINTENANCE_MODE` window —
the exclusions `slo-and-capacity.md` already specified. Both series render at zero
traffic so `ok/(ok+error)` is stable. 3 tests.

## Unit C — event-freshness bounded-lag histogram (this commit)

The objective is "≥99% of committed collaboration events **delivered or replayable**
within 5 seconds". Closed the three real gaps:

1. **Replay leg.** Catch-up sends on both sockets (project + collab) now record
   `harness_event_replay_age_seconds`. Deliberately a **separate** metric, not a
   label on the SLO histogram: a client that was closed for ten minutes legitimately
   replays ten-minute-old events, and folding that into the SLO would turn correct
   behaviour into a phantom violation. The docstring and `docs/realtime.md` both say
   which is which.
2. **The alert.** `HarnessEventFreshnessSlo` — the `le="5.0"` bucket below 99% of
   live observations for 10m, guarded with `and on()` so **absent traffic cannot
   page as a violation** (a fresh restart has zero observations, not 0% attainment).
   promtool unit tests cover degraded, healthy and no-traffic. `scripts/test-alerts.sh`:
   **19 rules found, tests SUCCESS**.
3. **End-to-end confirmation.** `scripts/e2e/w17_event_freshness_drill.py` commits a
   real event through the orchestrator while a project socket is connected, reads the
   pushed frame, scrapes the deployed `/metrics`, and asserts the histogram *grew* and
   the new observation landed inside the bound — then reconnects and asserts the
   replay leg recorded.

| Run | commit→socket write | wall-clock to frame | within 5 s | replay obs | verdict |
|---|---|---|---|---|---|
| `1bcc774d5b53` | 0.0110 s | 0.0973 s | 1/1 | 1 | PASS |
| `03ee401bcbd7` | 0.0086 s | 0.0415 s | 1/1 | 1 | PASS |
| `15aeb4977e3c` | 0.0103 s | 0.0525 s | 1/1 | 1 | PASS |
| `ab08c71ba65a` | 0.0100 s | 0.0471 s | 1/1 | 1 | PASS |

**4/4 PASS, lag 8.6–11.0 ms.** Evidence:
`docs/briefs/evidence/w17/w17-freshness-*.json`.

Two boundaries stated plainly:

- *"Push acknowledgement" is what it is.* The far edge is `send_json` completing to
  the ASGI transport, not a client ack — clients are read-only by contract and send
  no ack. The `ts` start point is written inside the publishing transaction
  immediately before `commit()`, so the measurement starts at durable commit.
- **This proves the instrument, not the objective.** Four samples on one Docker
  Desktop host are not a 5-minute window over 30 days, and are not the target
  topology. The lag excludes nothing it should: it includes host clock skew.

## Unit D — alert routing to a live target + backup-freshness verify

**`scripts/e2e/w17_alert_delivery_drill.py` — PASS.** It renders the repo's *real*
`deploy/observability/alertmanager.yml` the way the compose entrypoint does (same
`#WEBHOOK#` marker rule, same `${VAR}` substitution), enables the webhook receiver,
runs a throwaway Alertmanager, POSTs a synthetic `severity="page"` alert and asserts
it arrives at a live HTTP sink.

`amtool check-config` SUCCESS · delivered in **10.4 s** (the `page` route's 10 s
`group_wait`, so the route tree was genuinely traversed) · `status=firing` ·
`receiver=email`. Evidence `w17-alert-delivery-cd70fddddd.json`. Container and config
removed afterwards; the shared stack was not touched.

It proves the **mechanism**. It does not choose a destination — that is the owner's
call, and the shared stack's receiver is still the dead `mailpit`.

**`scripts/e2e/w17_backup_freshness_verify.py` — PASS.** Reads the scraped
`harness_backup_last_success_timestamp_seconds`, computes its age and asserts the
alert state agrees: age **528 299 s (~6.1 days) > 25 h** → `HarnessBackupStale`
**firing**, `HarnessBackupNeverRan` quiet while the series exists. Evidence
`w17-backup-freshness-1793449933.json`.

Two deliberate refusals: it does **not** run a backup (the backup runbook forbids a
drill archiving into the live prefix) and it does **not** insert a synthetic
`backup_runs` row — that would turn a real 6-day outage of the backup cadence into a
green number and prove nothing. The *fresh → clears* leg is covered by the promtool
rule test and `section1/tests/test_backup_metrics_pg.py`.

**Finding for the owner, not a pass:** the base-backup cadence has not succeeded in
~6 days on the deployment this repo calls live, and no alert could have told anyone.

## Unit E — shared run-volume hazard (fixed)

`harness_runs` carries an explicit `name:` (worker-daemon mounts it by literal name), and
compose does not prefix an explicit name with the project. So `docker compose -p <any
project> down -v` resolves to the **live** volume, protected only by Docker's "in use"
refusal. Reproduced on scratch volumes (never the live one): with the pattern as it was,
`down -v` on project `t1` reported `Volume scratch_live_runs Removed`; with an override it
removed only `t2_runs` and the other volume survived.

Fix: the name is `${HARNESS_RUNS_VOLUME:-harness_runs}` (default unchanged, so the live
stack is untouched) and worker-daemon's `HARNESS_RUNS_VOLUME` reads the same variable, so
the daemon and compose cannot disagree. Every script that tears down a throwaway project
now exports a project-scoped value: `scripts/pitr_drill.sh`, `scripts/loadtest/run_probe.sh`,
`run_ladder.sh`, `deploy/runners/rollback_rehearsal.sh`. Regression test
`section1/tests/test_deploy_volume_isolation.py` (3 tests) pins the variable, the
daemon/compose agreement and the four scripts. Also corrected two false claims: the PITR
script and runbook said it "never reads `.env`"; compose auto-loads `deploy/.env`.

Verified end to end: the real-5xx drill's isolated stack was torn down with the fixed
`run_probe.sh down`; `harness_runs` still existed afterwards and no live container was
affected by that command.

**Residual:** the default is still the shared name. A new script that runs `down -v` on an
isolated project and forgets the variable reintroduces the hazard; the test covers the four
known scripts, not future ones. Making the default fail closed would break the live
bring-up, which is why it was not done.

## Unit F — real 5xx generation (closed)

`scripts/e2e/w17_real_5xx_drill.py`, run against an isolated `loadtest` stack (image
`vs2fresh`, project `harness-w17-5xx`, torn down afterwards). It stops the stack's
database, so the deployed orchestrator fails the way production would, and reads the
answer from the deployed `/metrics`:

| Check | Result (both runs) |
|---|---|
| Healthy plan requests move `outcome="ok"`, not `error` | yes |
| 5 authenticated requests during the outage | **5 × HTTP 500** (genuine, `INTERNAL_ERROR`) |
| `harness_eligible_requests_total{outcome="error"}` | rose by exactly 5 |
| `http_requests_total{status="500"}` | rose by exactly 5 |
| Caller error (unauthenticated) | 401, **not counted** |
| `/ready` during outage | 503, **not counted** (synthetic probe) |
| Recovery | `/ready` 200 within 1.0 s of `docker start db`, no orchestrator restart; next request 201 |
| Run SLI | 4 ok / 5 error = 0.4444 (by construction, not an availability claim) |

2/2 PASS: `docs/briefs/evidence/w17/w17-real-5xx-bd16f83a6cd5.json`,
`...-58273c6771b9.json`. The drill restarts the database in a `finally`, and refuses a
`--db-container` that is not a loadtest database.

Not proven: that `HarnessHigh5xxRate` pages (promtool covers the rule; this stack has no
Prometheus), and no availability objective over a window. After the final lint pass
(imports only) the drill was not re-run because the live stack and the shared ports were
in use by another lane.

## Restore evidence review (what it proves, what it does not)

| Evidence | Date | Result |
|---|---|---|
| `pilot-prep/walg-s3-pitr-drill.json` | 2026-10-25 | pass, restore 3 s |
| `pilot-prep/pitr-drill-20261029T140816Z.json` | 2026-10-29 | pass, restore 4 s |
| `w17/pitr.json` | 2026-10-31T06:06:59Z | pass, restore 4 s, `backupRecorded: true`, private compose project |

Each proves a real rewind: row 2 exists only if WAL written after the base backup replays,
and is absent after the target-time promote. Three passes in a week on one script.

Limits that matter to the owner:

- The subject is a **marker table in a scratch cluster**, not the live database. It proves
  the mechanism, not that the live archive restores. (Batch 1 `A12` did a PITR from the live
  file archive in October; that is a separate, older piece of evidence.)
- **`harness_store` (artifact bytes) and `harness_runs` are not covered by any backup or
  restore step** in the runbooks or scripts. The `artifacts` table is restored by PITR, but
  the bytes it points at are not. The runbook itself says to back the store up "with the
  database" and nothing does. This is an open gap, not a pass.
- 4 s on an empty database says nothing about RTO at production size.
- The live base-backup cadence was 6.1 days stale at 05:32 on 2026-10-31 and nothing has
  taken a backup since. Taking one writes to the live archive, so it was left to the
  stack's owner.
- The scheduled monthly drill uses a MinIO image pinned by digest and cached locally; it
  was reported unpullable from public registries during the October S3 drill (not re-checked today), so a pruned cache would break the next run.

## Unit G — backup schedule (installed)

Why the base backup was 6 days stale: **nothing had ever been scheduled.** The runbook's cron lines
were a `/path/to/harness` placeholder; there was no crontab, no launchd agent and no scheduled task,
and the last logical dump was 2026-10-17. Fix: `deploy/backup/nightly.sh` (logical dump, live WAL-G
base backup with `--no-deps`, artifact-store snapshot + verify, archive mirror) run by a launchd agent
`com.harness.nightly-backup` at 03:17, installed with `deploy/backup/install-launchd.sh`. It ran by hand
(31 s, 4/4 steps) and again **through launchd itself** (`runs = 1`, exit 0), which is the run that proves
launchd's PATH and Docker access. `HarnessBackupStale` remains the detector if a night is missed.
5 tests (`test_backup_schedule.py`: scripts parse, every step present, `--no-deps` kept, restore refuses an
existing volume, catch-up/retention/mirror guard/off-site warning, launchd catch-up and caffeinate). Evidence `stage2-ops-20261031.json`.

**Follow-up the same day (Unit G2).**
- *Pruning:* `nightly.sh` now sets `WALG_RETENTION_KEEP=7` (the wrapper's existing retention, which
  refuses while run-retention holds exist; 0 holds today). Proved on a **scratch copy** of the archive
  first, never the live one: `delete retain 1 --confirm` removed 3 317 objects (3.7 GB to 54 MB), then
  `restore-walg.sh` fetched the single remaining base in 10 s and replayed to 14:57:35 with the data
  matching live. My prediction that this would also show a before/after difference was wrong (my data
  fix was applied before the target time), so it proves a pruned archive restores, not a rewind. First
  live run with the setting: 4 bases kept, nothing deleted (limit 7). The mirror follows with `--delete`
  only while the source still holds a base backup. Window is 7 days, about 4-5 GB.
- *Sleep:* the agent now runs `nightly.sh --if-due` at 03:17 and hourly under `caffeinate -i`; a run
  happens only if the newest base is over 23 h old, so a missed night catches up on wake or the next tick
  (skip path verified through `launchctl`; due path verified with `DUE_AFTER_HOURS=0`, 4/4 steps). It
  **cannot wake a sleeping Mac**; that needs `sudo pmset repeat wakeorpoweron ...`, a system setting I
  did not change.
- *Off-machine:* a hook exists (`~/harness-backups/offsite.env`, `OFFSITE_RSYNC_TARGET`) and every run
  prints `WARN offsite` and `offsite=NONE` until it is set. **No off-machine copy exists**, because it
  needs a destination and credentials that are the owner's to choose.
- *Owner decisions:* prepared, not made: [`stage2-owner-decisions-2026-10-31.md`](stage2-owner-decisions-2026-10-31.md).

## Unit H — artifact store backup and restore (closed)

`store-backup.sh` snapshots `harness_harness_store` (read-only mount, newest 14 kept);
`store_verify.py` checks every non-deleted `artifacts` row created before the snapshot has its file
with a matching sha256; `store-restore.sh` unpacks into a **new** volume. Result: 127/127 rows verified;
a one-byte tamper on a copy is caught (exit 1); a restore into a scratch volume has a tree hash
identical to live. The store is run inside `nightly.sh` straight after the base backup. Honest limit: the
blobs are content-addressed and write-once, so a restore holds everything that existed at snapshot time,
but there is no single transaction spanning the database and the store; a blob written between the two
steps (seconds) would be in the database restore but not the snapshot, and the verifier would report it.

## Unit I — the two firing alerts (explained, one data fix, one product bug open)

`HarnessRunQueueStalled` (value 5 824 987 s, **67 days**) and `HarnessEventBusLag` (5 559 s) were both
caused by **one row**: `run_claims` `run-3b3c1c3049c0`, `queued` since 2026-08-25, `project_id = ''`. The
claim query `JOIN`s `projects ON rc.project_id = p.id`, so a claim with no project can never be claimed;
the run has no `agent_runs`, attempt or event rows. Its presence also made `harness_queue_depth > 0`,
which is the "runs are active" guard on EventBusLag, so an idle system looked like a stalled one. The
alerts were telling the truth about a stuck claim; the 93-minute bus lag is just the idle gap.

Action (reversible, one row): `state = 'queued'` to `'orphaned'`, the queue's own parking state, which
nothing claims and the gauges ignore. Original row in the evidence file. Prometheus then showed **no
active alerts**.

**Open product bug (not fixed, needs a decision):** both enqueue callers in `service.py` (lines ~1333 and
~2011) pass `run_data.get("projectId") or ""`, so a run started without a project is still queued under
`''`, silently never runs, and will fire this alert again. Returning `False` from `enqueue_run` would not
help (the callers read that as "already queued"). The options are to reject such a start with a 400, or to
give it a real claimable path; that is a behaviour change in the orchestrator the concurrent W23 lane is
editing, so I left it.

## Verification

- `section1`: **1233 passed, 63 skipped** (was 1225; +3 volume-isolation, +5 backup-schedule tests), `ruff check src tests scripts` clean.
- `promtool`: 19 rules, unit tests SUCCESS (`scripts/test-alerts.sh`).
- `section4` observability unit tests: 15 passed (Unit B).
- Image provenance checked *inside* the image: `observe_replay_age` present,
  `harness_event_replay_age_seconds` defined, 2 call sites in `app.py`.

## Deployment note — why an isolated stack, and one hazard

The shared stack was redeployed by the concurrent lane to **`vw23ready1`** (11 backend
containers + worker-daemon) ~15 min before this work; the frontend is still
`vs1coherent`. Rebuilding it would have clobbered another builder's running
deployment, so confirmation ran against a **single-revision build of `0bc3128`** in
the compose `loadtest` profile — its own Postgres and its own identity/orchestrator,
project `harness-w17`, ports 18001/18006. Image `harness-backend:vs2fresh`,
`sha256:29f5446f…`. Torn down: 0 containers, 0 networks, volume removed.

Two things that need recording because they will bite the next builder:

- `loadtest` needs `PROBE_MASTER_KEY` set (the documented M18-C4 gotcha — an empty
  `HARNESS_MASTER_KEY` from `x-backend-env` overrides the dev fallback and identity
  crash-loops on `master_key must be non-empty`).
- ~~The `loadtest` profile's `down -v` targets the shared `harness_runs` volume.~~ **Fixed
  in Unit E.** Any throwaway project must still export `HARNESS_RUNS_VOLUME`; the four
  existing scripts do.

## Where Stage 2 actually stands

| Exit item | Status | By whom |
|---|---|---|
| Failure-outcome measurement (W17 drills) | Terminal-event retry, cross-replica NOTIFY, durable admission, Linux shell cleanup, MCP cleanup, **real 5xx generation (Unit F)**: **done**. | engineering |
| Backup/alert verification | **Closed as mechanism**: backup gauge + alert state verified live; routing verified to a live target; three PITR drill passes. Base backup taken, restore from the live archive verified, nightly schedule installed and run through launchd, artifact store now backed up and restore-verified (Units G, H). **Still open**: the page destination is unset and the receiver is dead (owner); no off-machine copy of any backup (hook ready, destination is the owner's); a product bug that can re-create a stuck queue claim (Unit I). | engineering did its half |
| SLO *measurement* (eligible-request denominator, bounded-lag histogram, both alerted on) | **Closed** — Units B and C. | engineering |
| Named SLO adopted, on-call owner, escalation, page destination, public-status decision | **Cannot be closed by engineering.** The roadmap: "No engineering milestone implicitly closes these decisions." | human owner |
| Measured capacity on the target topology, real provider | **Cannot be closed here.** Requires the approved budget and a target topology; only the laptop dry-engine figure exists. | human owner |

**Stage 2 exit: NOT met.** Everything engineering could close without a decision or a
spend is closed and verified at a named revision, including both items the previous
revision of this record left open (the real-5xx drill and the volume hazard). What
remains: a human owner (SLO adoption, on-call, page destination, status page), an
approved budget and target topology (capacity). The operational items found while
reviewing the restore evidence (stale base backup, no schedule, no store backup, two unexplained
alerts) are now closed or explained; what is left there is an off-machine copy,
and the projectless-run enqueue bug (Unit I). Inventing any of the human decisions would be the
failure mode this repo exists to prevent.

**Live-stack caveat at the time of writing.** Around 13:22 UTC every backend container of
the live `harness` project was stopped within 1.4 s of each other while another lane was
building a separate `harnessfresh` project. I did not stop them and did not restart them.
`scripts/e2e/w17_backup_freshness_verify.py` therefore **failed** at that moment
(`w17-backup-freshness-1793453049.json`: series absent, `HarnessBackupNeverRan` still
`pending`). That is the orchestrator being down, not a backup regression; the passing
reading at 05:32 stands for the cadence finding.

**Re-run after the stack came back (live, read-only): PASS** — `w17-backup-freshness-1793453952.json`.
Newest base backup is 532 317 s (~6.2 days) old, `HarnessBackupStale` firing, `HarnessBackupNeverRan`
quiet. The same reading also showed `HarnessEventBusLag` and `HarnessRunQueueStalled` firing a few
minutes after the redeploy. **Correction:** I first called these possible restart transients. They were not:
they were already firing in the 05:32 reading and are explained under Unit I.

**Live base backup taken (2026-10-31 13:46 UTC, at the user's instruction).** `walg run --rm
--no-deps` (so no second cluster could archive into the live prefix) against the live `db`,
prefix `/tmp/harness-walg-live`: `base_000000010000001C00000087`, 4 s, 48 MB, LSN 1C/87000028 to
1C/87000158, `backup_runs` id 3, `pg_stat_archiver` 3165 archived / **0 failed**. Before it, that
prefix held 3 252 WAL segments and **no `basebackups_005` directory**, i.e. WAL with no base to
replay it onto: the live archive could not have been restored from. Re-running the freshness
verifier 111 s later: `harness_backup_last_success_timestamp_seconds` advanced, `HarnessBackupStale`
**inactive** (`w17-backup-freshness-1793454490.json`, PASS). That is the *fresh -> clears* leg that
was previously covered only by promtool and a PG test.

**Restore from the live archive: PASS** (`live-archive-restore-20261031.json`). `restore-walg.sh` fetched this base (11 s) and
replayed live WAL to 14:40:00 UTC (last commit 14:39:34) in a scratch container, paused, never promoted. `backup_runs` row 3,
written after the base backup finished, is present (WAL replay on top of the base), `schema_migrations` 71/0072 and the counts of
users 614, teams 616, projects 1031, agent_events 177 441 and artifacts 131 equal live. Scratch container and volume removed; live
volume intact; no timeline-history file appeared in the live archive. Caveat: the stack was near idle from 13:21, so equal counts
show completeness, not a rewind (the marker drills prove the rewind). This closes the live-archive gap above.

Still not shown: any recurring schedule. One manual backup does not
make the cadence healthy; the nightly job that should have produced one is still unexplained.
`HarnessEventBusLag` and `HarnessRunQueueStalled` were still firing at that reading.
