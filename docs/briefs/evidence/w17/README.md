# W17 isolated load and restore evidence — 2026-10-30

## Dry-engine load

Source snapshot: Git `35f3ce5` plus the uncommitted W17 event-bus change
(`eventbus.py` SHA-256 `97c6dd7cd0587e7fed4f1fc2859439b609e83567898b661f47dc8031a9b7986d`).
Snapshot dependencies came from cached backend image
`sha256:5973222fefd58569010b807d5e1822d74edffccfa5194f4acf979d1bc976aa7c`;
snapshot source was placed on `PYTHONPATH`. This was not a normal clean image
build. Compose used a uniquely named project and network, a scratch database
and store, one identity and one orchestrator, Postgres 18, and a dry echo
engine. Host: macOS 26.7.2; Docker Desktop. The orchestrator limit was 1 CPU
and 768 MiB, database 1 CPU and 1 GiB, identity 384 MiB. Executor width 8,
per-team claim cap 4, 0.5-second worker poll. One Locust process generated
closed-loop journeys, each using a team; this is not an open-loop arrival test.

| Offered users | Ramp | Hold including ramp | Final journeys | Failures | Control p50/p95 | Engine wait p50/p95 | Journey p50/p95 |
|---:|---:|---:|---:|---:|---|---|---|
| 8 | 4/s | 60 s | 436 | 0 | 99/180 ms | 1.1/1.3 s | 1.2/1.5 s |
| 100 | 10/s | 75 s | 379 | 0 | 0.92/1.4 s | 25/27 s | 26/28 s |

Numbers are Locust's approximate percentiles and final console summary;
the generator was allowed 90 seconds to drain. The hundred-user maximum
journey was about 82 seconds. Periodically written CSV files in this directory
stop before final drain (430 and 371 journey rows respectively), so their
sample counts and maxima differ from the final console summary. Empty failure
and exception CSVs record no reported failures at their last write.

During the hundred-user run one instantaneous database sample had 90 queued and
8 running claims. A contemporaneous Docker stats sample showed database 49.62%
CPU / 163.1 MiB, orchestrator 101.17% CPU / 108.8 MiB and identity 14.05%
CPU / 81.38 MiB. CPU percentages are Docker's host-relative instantaneous
measure, not a time series. After load stopped, a later sample had 17 queued
and 12 running claims; the queue eventually reached zero. Claim state is not
the same as active engine threads. No precise drain-time measurement or
independent duplicate-terminal audit was collected.

This demonstrates a substantial latency increase at the offered 100-user level
in this one configuration, not a maximum safe user count, a production SLO
measurement, or capacity for real model calls. No current real-agent load,
browser/process saturation, or multi-host performance is inferred.

## Scratch restore

[`pitr.json`](pitr.json) records the MinIO/WAL-G point-in-time drill: a scratch
Postgres restored rows `1,2` from the point before the synthetic deletion,
then promoted; restore took 5 seconds in this local exercise. A separate
project, network, database, bucket, scratch container and volume were used.
The snapshot drill script was modified to use those unique names and a scratch
env file; it did not run the live cutover instructions. The backup wrapper
warned that it could not record the backup in `backup_runs`, so monitoring of
backup freshness was not proven. Local MinIO is not a cloud backup or a live
primary recovery test.


## Re-run 2026-10-31: isolated restore drill (`pitr.json`, `pitr.log`)

`scripts/pitr_drill.sh` itself (no modified copy) ran in its own compose project
`harness-pitr-20261031t060635z`: restored rows `1,2` at the target, the delete was
absent, promoted; base backup 3 s, fetch 2 s, replay-to-pause 1 s, restore 4 s. The
wrapper now records the backup (`backup_runs` row count 1, no WARN; the drill applies
migration 0059 to its scratch cluster and fails otherwise). The earlier `pitr.json`
dated 2026-10-30 is superseded by this one. Cleanup left no container, volume or
network of the project; the live `harness` containers kept running throughout.
Still not shown: offsite or provider storage, a live-primary cutover, that the
deployed freshness alert fires, or that the W15 scheduled automation triggers (its
last known state is the account-credit blocker; the next scheduled firing recorded in
`docs/m2-w15-plan.md` is 2026-11-01T12:00Z, which is the first chance to observe it).

## Load ladder, dry engine (`ladder-e9e5589-run1/`, `ladder-e9e5589-run2/`)

Revision `e9e5589` (`git archive`, so uncommitted work is not in the build), image
`harness-backend:vw17-e9e5589`, scratch Postgres 18, one identity and one orchestrator
replica, dry echo engine (50 ms), executor 8, per-team claim cap 4, worker poll 0.5 s,
rungs 4/8/16/32/64/100 closed-loop users (one team each), 60 s hold, spawn 4/s up to
20 users and 10/s above. Zero provider spend. The Mac was also running other
development sessions, Docker workloads and a UI, so absolute numbers are noisy: the
same revision shows control p95 at 100 users of 1.5 s in run 1 and 2.7 s in run 2.
Treat the shape (queue wait growing with offered users, control plane staying
sub-second to low seconds) as indicative and the absolute values as not reproducible.

* **Run 1** (`ladder-e9e5589-run1/SUMMARY.md`) used the driver as first written, which
  rebuilt the image on every rung, so the image behind each rung is not proven to be
  one immutable build, and it kept no service logs. Journeys 293/414/455/450/397/347,
  one failed journey (rung 16).
* **Run 2** (`ladder-e9e5589-run2/SUMMARY.md`) builds once, runs rungs with
  `--no-build`, records the image id (`sha256:02695acd…`) and, per rung, container
  image, restarts and OOM state (no restart, no OOM) plus the WARNING/ERROR lines of the service logs (`warnings_*.log`; the raw logs were 58 MB and are not kept). The only warning in any rung is the dry engine's "model 'stub' is priced by neither the catalog nor the rate card"; no ERROR was logged. Journeys
  269/321/219/196/212/221. Rung 4 shows 179 failures: all are HTTP 429
  `team run-start budget exhausted`, the product's per-team limit of 60 run starts per
  minute in a fixed window (`HARNESS_TEAM_RUN_START_LIMIT`). Four closed-loop users
  generate about 70 starts per team per minute, so whether a window overflows depends on
  phase; run 1 stayed under it. The refused starts have a plan and approval but never an
  `execution_started`, which is why that run's audit shows `runs` larger than `started`.
  This is the guard working, not a lost run, but it means the low rungs are partly a test
  of the rate limit. Pace the journeys (`HARNESS_TASK_PACING_S`) or raise the limit to
  measure the engine path alone.

**Terminal-transition audit** (`scripts/loadtest/audit_terminals.sql`, read-only, run on
the scratch database after the queue drained; negative control: injected duplicate,
conflicting and lost terminal events in a rolled-back transaction were all flagged).
Over 2,357 runs in run 1 and 1,438 started runs in run 2: no duplicate start, duplicate or
conflicting engine terminal, duplicate review terminal, terminal without a start,
duplicate event seq or leftover claim. Exactly **one lost terminal transition** in run 1
(rung 16, run `run-7ad9f9568ea7`): `execution_started` and `model_routed` are in the log,
no `engine_completed`/`engine_failed`, the claim row is gone, and the client sat in
`executing` until its 180 s timeout (`lost_run_events.txt`). It did not recur in run 2
(0 of 1,438) and run 1 kept no orchestrator log, so the cause is **not established**. A
code reading is consistent with it: `OrchestratorService._publish_safe` swallows any
publish exception and the claim is released regardless, so one failed terminal write is
unrecoverable. `section4/tests/unit/test_w17_terminal_publish.py` demonstrates that
behaviour (strict xfail) but does not prove it caused this run. Fixing it belongs in
`service.py`, which carries another lane's uncommitted work.

Not measured: real-provider capacity or cost, open-loop arrivals, multiple replicas, a
production-like host, or any SLO attainment. Orchestrator CPU was at about one core
from the 16-user rung up (its 1.0 CPU limit), so the dry-engine ceiling here is partly
that container limit.
