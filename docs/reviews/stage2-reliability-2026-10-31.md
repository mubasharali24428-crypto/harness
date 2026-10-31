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

## Verification

- `section1`: **1225 passed, 63 skipped**, `ruff check src tests scripts` clean.
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
- **The `loadtest` profile's `down -v` tries to remove the shared, explicitly-named
  `harness_runs` volume.** It was refused only because the live stack still held it.
  Running that profile while the main stack is *down* would delete another project's
  run artifacts. Worth a compose fix, not a workaround.

## Where Stage 2 actually stands

| Exit item | Status | By whom |
|---|---|---|
| Failure-outcome measurement (W17 drills) | Terminal-event retry, cross-replica NOTIFY, durable admission, Linux shell cleanup, MCP cleanup: **done**. Real 5xx generation: **open**. | engineering (partly) |
| Backup/alert verification | **Closed as mechanism**: backup gauge + alert state verified live; routing verified to a live target. **Open as operations**: the real destination is unset and the current receiver is dead. | engineering did its half |
| SLO *measurement* (eligible-request denominator, bounded-lag histogram, both alerted on) | **Closed** — Units B and C. | engineering |
| Named SLO adopted, on-call owner, escalation, page destination, public-status decision | **Cannot be closed by engineering.** The roadmap: "No engineering milestone implicitly closes these decisions." | human owner |
| Measured capacity on the target topology, real provider | **Cannot be closed here.** Requires the approved budget and a target topology; only the laptop dry-engine figure exists. | human owner |

**Stage 2 exit: NOT met.** Everything engineering could close is closed and verified
at a named revision; what remains is a human decision and a spend authorisation, and
inventing either would be the failure mode this repo exists to prevent. The two
concrete engineering follow-ups inside Stage 2 that are *not* human-gated: the real-5xx
drill, and the `harness_runs` volume hazard above.
