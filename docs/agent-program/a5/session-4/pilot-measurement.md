# A5-S4 — Pilot measurement instruments (draft, not results)

Status: **instrument design only.** No pilot has run. No ROI, productivity
uplift, willingness-to-pay or retention numbers exist and none may be inferred
from this document. This file is the opt-in, privacy-conscious measurement plan
the S4 acceptance requires.

## What is measured (all from already-frozen events)

Source: `harness_contracts.events.EventType` stream + cost-ledger rows. No new
telemetry is added to the product for measurement; the pilot reads what the
harness already emits.

| Instrument | Formula | Denominator | Why it resists gaming |
| --- | --- | --- | --- |
| Accepted change | `pr_opened` AND last `review_passed` with `findings.passed=true` | runs | model `engine_completed` claim is excluded by construction |
| Claimed-vs-accepted gap | claimed completions − accepted changes | claimed completions | large gap = model claims outrunning review |
| Rework rate | runs with >1 `execution_started` or any `review_failed` | started runs | failed reviews stay visible |
| Time to first useful change (TTFUC) | `pr_opened.ts − plan_proposed.ts` for accepted runs | accepted runs | blocked/abandoned runs do not inflate the sample |
| Approval latency | `plan_approved.ts − plan_proposed.ts` | approved runs | human gate cost is explicit |
| Cost per accepted change | sum of run costs / accepted runs | accepted runs | per-task cost is reported separately; storage/host costs are out of scope |
| Cost per claimed completion | sum of costs of `engine_completed` runs / claimed completions | claimed completions | paired with accepted number, never alone |
| Abandonment rate | planned, never started, >24h old (or stalled >24h without verdict/PR/block) | planned runs | silent drop-off is counted |

Percentiles are nearest-rank, documented in `harness_telemetry.metrics`.

## Pilot design (proposed)

- **Opt-in.** Teams opt in per project; the archive is team-scoped by default,
  and `TEAM_FORBIDDEN` is returned rather than silently filtering.
- **Baseline window.** Two weeks of the team's own pre-pilot runs (event stream
  already stores them) before comparing; no cross-company benchmarks.
- **Primary question.** "For accepted changes of comparable size, does TTFUC
  change?" Secondary: rework rate, cost per accepted change.
- **Stopping rule.** Report medians with sample counts; if n(accepted) < 10,
  report as "insufficient sample", not as a trend.
- **No individual scoring.** Metrics are computed per run and per team. The
  dashboard has no per-developer view by design; pilot interviews are the only
  place individual experience is discussed, with consent.

## Privacy and consent checklist

- [ ] Team owner signs off on archive retention window (default 90 days).
- [ ] Goal/event text is scrubbed at ingest; raw text is not retained
      (`scrub_text` covers tokens, keys, passwords, bearer values, emails).
- [ ] Access is scoped to `principal.teamId`; admins are named in the pilot
      record.
- [ ] No model prompts, source contents or file bodies beyond the existing
      event payloads are collected for measurement.
- [ ] Exit: deleting the archive DB removes all pilot data; purge job tested.

## Developer experience instrument (qualitative, optional)

Short monthly survey (5 items, 1–5), kept separate from the product dashboards:

1. The plan I approved matches what I meant. (1–5)
2. Review feedback caught issues I cared about. (1–5)
3. I trusted the PR without re-reading everything. (1–5)
4. Waiting for approval/review was a bottleneck this week. (1–5, reversed)
5. I would keep using this on real work. (1–5)

Reported as distributions with n, never as a single "productivity score".

## Honest limits

- Fixture data in this repo is synthetic; metric correctness is tested, real
  value is unmeasured.
- Events only capture the harness-visible timeline; human context work outside
  the run is invisible.
- Cost data depends on the provider ledger being complete; missing rows
  understate cost, never overstate it.
- A pilot with one team at one company is a case study, not evidence of
  general ROI.
