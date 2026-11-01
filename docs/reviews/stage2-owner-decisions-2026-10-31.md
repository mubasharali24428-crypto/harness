# Stage 2 — decisions only an authorized owner can make

Prepared 2026-10-31 by an engineering session so each decision takes minutes, not a meeting. **This
is evidence and a worksheet. Nothing here is adopted, ticked, signed or approved**, and every
"Recommendation" is a suggestion. The roadmap says no engineering milestone implicitly closes these.
The engineering record is [`stage2-reliability-2026-10-31.md`](stage2-reliability-2026-10-31.md).

How to use: fill the `DECISION` line of each block (or write "defer"), then do the "Then" step; each
"Then" is one concrete, checkable action. When 1–5 are filled, Stage 2's exit evidence (named SLO and
on-call owner, verified alert routing, agreed provider budget) can be re-assessed.

---

## 1. Adopt the service objectives

**Proposed in** [`slo-and-capacity.md`](../runbooks/slo-and-capacity.md): API availability ≥ 99.5% of
eligible requests per rolling 30 days · control-plane p95 ≤ 250 ms (5-min windows) · event freshness
≥ 99% within 5 s (5-min windows).

**What exists:** all three have an instrument and an alert (`HarnessHigh5xxRate` / eligible-request
counters, `HarnessRequestLatencySloBurn`, `HarnessEventFreshnessSlo`). Measured so far: freshness 4/4
samples within 8.6–11 ms on one laptop host; a 5xx outage moves the availability counter exactly
(Unit F); latency under dry-engine load 99/180 ms p50/p95 at 8 users. **No 30-day window exists yet**, so
nothing has been *attained*, only instrumented.

**Recommendation:** adopt the three as *pilot objectives* with a 30-day review date, not as a customer
promise; re-baseline on the release candidate first (the runbook says so).

DECISION: adopt the three as pilot objectives  Owner: Mubashar Ali (chat)  Date: 2026-10-31  Review on: 2026-11-30

Recorded by an assistant from the owner's answer in chat; applied in `slo-and-capacity.md`.

**Then:** replace "proposed" in the runbook's status line with the decision and date; nothing in code changes.

## 2. Name the on-call owner and escalation

Four alerts are `severity="page"` and want a human now: `HarnessServiceDown`, `HarnessIsolationViolation`,
`HarnessQueueDepthCritical`, `HarnessWalArchiveFailing`. Fifteen are `warning`.

**Recommendation:** one named primary for the pilot, one named backup, and a stated rule for who is
paged when the primary does not acknowledge within a set time. One person is acceptable for a pilot if
that is written down.

DECISION: Primary Mubashar Ali  Backup none  Escalate after n/a (no escalation) to n/a

Recorded by an assistant from the owner's answer in chat on 2026-10-31; applied in `alerting.md` section 6b.

**Then:** put the names in [`alerting.md`](../runbooks/alerting.md); no code change.

## 3. Choose where pages go

**Today every page goes nowhere:** the running Alertmanager's email receiver points at a Mailpit
catcher that is not running (so nothing is delivered even if an address is set). The mechanism is proven
(`w17_alert_delivery_drill.py`: a synthetic page reached a live sink in 10.4 s through the real routes).

Options: (a) real SMTP + a real address; (b) a webhook (`HARNESS_ALERT_WEBHOOK_URL`: ntfy, Slack,
Discord, PagerDuty-style endpoint); (c) both. **Recommendation:** a webhook to a phone-push service plus
email, because a laptop-hosted stack cannot rely on one path.

DECISION: webhook + email  Destination: not given; the owner sets the URL and address in the gitignored `.env`

Recorded by an assistant from the owner's answer in chat on 2026-10-31. **Not configured or proven**: the delivery drill against the real destination is still owed.

**Then:** set the variables in `deploy/.env`, recreate Alertmanager, run
`python3 scripts/e2e/w17_alert_delivery_drill.py` against the real destination and confirm a human
received it. Do **not** paste the URL or credentials into this repo; put them in the gitignored `.env`.

## 4. Public status page: yes or no

The runbook leaves this open. **Recommendation:** no for the pilot (no external users depend on it
yet); revisit at the first external customer.

DECISION: ______________________

## 5. Capacity target, topology and provider budget

**What is measured:** one laptop, one orchestrator, dry (echo) engine, closed-loop users: 0 failures at
8 and 100 users; journey p95 1.5 s at 8, 28 s at 100 (engine wait dominates); see
`docs/briefs/evidence/w17/README.md`. **What is not:** any real-provider load, any target topology,
open-loop arrival, a soak.

**Needed from the owner (all five, or the experiment cannot honestly start):**

| Question | Answer |
|---|---|
| Target topology (hosts, replicas, DB size) | |
| Provider and model for the experiment | |
| Hard budget cap in USD for the whole experiment | |
| Target concurrent users / runs per day to prove | |
| Who may stop it, and the stop condition | |

**Cost anchor from this repo:** a vision run on the current model cost about $0.0076 (W20, OpenRouter
haiku-class). A cap of `N × per-run cost` plus 2× headroom bounds the spend; for example 500 runs is
about $3.80 before headroom. These are measured anchors, not a quote for your workload.

**Recommendation:** a small capped first experiment (hundreds of runs, single-digit dollars) to learn the
real shape, then a larger one only if it passes.

DECISION: approve a capped real-provider experiment  Budget cap: $5  Approved by: Mubashar Ali (chat, 2026-10-31)

Recorded by an assistant. The experiment has **not started**: topology, target load and the stop condition are still unanswered (the table above), and the live stack is shared with a rebuild.

## 6. Where do off-machine backups go

All backup copies are on this Mac today (the live archive, `~/harness-backups`, the store snapshots).
`nightly.sh` has the hook ready and **warns on every run until it is configured**: create
`~/harness-backups/offsite.env` containing `OFFSITE_RSYNC_TARGET=user@host:/path/` (key-based ssh must
already work for that host). Backups contain user data, so choose a host you control or encrypt first
(WAL-G supports client-side encryption, which needs a key you must also back up elsewhere).

DECISION: Destination ______________________  Encryption: yes / no

## 7. Wake the Mac for the 03:17 backup (one command, needs your password)

The agent now catches up hourly after sleep, but only a scheduled wake makes 03:17 happen on time.
This changes a system power setting, so it is yours to run:

```
sudo pmset repeat wakeorpoweron MTWRFSU 03:15:00
```

(Undo: `sudo pmset repeat cancel`.) On battery with the lid closed this may not wake the machine.

DECISION: run it / no, accept catch-up only
