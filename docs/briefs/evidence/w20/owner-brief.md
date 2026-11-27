# W20 owner brief — release readiness (prepared 2026-10-30, evening)

**This page records no decision.** The decision record in
[`docs/archive/m2-w20-plan.md`](../../../archive/m2-w20-plan.md#decision-record) is untouched and
blank; an authorized owner who did not write the code fills it in. This is the
evidence summary for that owner. Every claim links to the full table and raw
files in the [W20 evidence README](README.md#owner-readiness-refill-prepared-2026-10-30-evening-for-the-authorized-owner).

## Where things stand

* Verdict on file is unchanged: **not ready for production or a general pilot**
  (`STATUS.md`, the readiness audit). A **limited design-partner pilot** is what
  the evidence can support, once its gates close.
* Of the 24 W20 checklist lines, four were re-measured on a current revision:
  local gates, `agent_events` partitions and the commit of the vision fixes pass;
  the image-vs-HEAD hash check now **fails** (reasons below). Six more passed
  only at an **older revision or model**, three were not run, four are not met,
  six are yours to decide and one cannot be judged by engineering. Nothing that
  passed at an old revision was marked as passing now.
* The one that matters most: **the held-out vision gate passed on
  `anthropic/claude-haiku-5.5` at `9e34f42` plus the fixes later committed as
  `53924be`. It has not been re-run since.** Seven runtime files changed after
  it (engine, confinement, worker, daemon, identity app), and the engine is on
  every agent run's path. A re-run costs roughly $0.05 (six vision runs were
  $0.0456 when measured).
* The running stack is `vw22fix2`. A W22 follow-up build (`vw22fix3`) exists but
  is not deployed, and its source edits are uncommitted, so `run_eval.py
  --verify-only` reports `verified: false` right now (`engine.py` differs
  between the worktree and the running image). The W13 commit at 23:24 also
  moved HEAD past what is running, and `verify-only` hashes only three files per
  target, so it cannot see that. Treat "deployed equals HEAD" as untrue until
  the stack is rebuilt from a committed tree.
* Checks in this pass ran on `git archive` exports of committed `e9e5589` and
  `55ad4b1`: Ruff clean everywhere, all suites pass except three intermittent or
  export-only test failures that I could not tie to a product defect (see the
  [verification log](refill-20261030/verification-log.md)).

## Questions for you

**1. Who is on call, and who owns the status page?**
No person or roster is named anywhere in the repo (W17 runbook: "no public
status page or assigned publisher"; W19 lists roles, not people). Options:
(a) name an on-call owner and a status-communication owner now; (b) accept a
pilot with the founders/engineers as informal on-call and say so to partners;
(c) hold the pilot until (a). Evidence: `docs/runbooks/w17-reliability.md`,
`docs/m2-w19-plan.md` open-gates table.

**2. Which SLO targets do you adopt?**
Proposed, never adopted: availability 99.5% over 30 days, control-plane p95
≤ 250 ms, event freshness ≥ 99% within 5 s (`docs/runbooks/slo-and-capacity.md`).
Options: (a) adopt as written; (b) adopt looser pilot targets; (c) publish no
targets and promise none. Evidence: dry-engine load, one host, no model calls —
8 users: control p95 180 ms (meets), 100 users: 1.4 s (misses), engine wait p95
27 s ([`../w17/README.md`](../w17/README.md), W17 still in progress). The
availability SLI is incomplete and the freshness instrumentation does not prove
the objective, so attainment is **unknown**, not met.

**3. Do you approve spend for real-provider measurements, and how much?**
Needed for: a real-agent capacity figure (none exists), the W25 benchmark, and
re-running the gate at the current revision. Observed costs: Haiku vision run
≈ $0.0076, code-search ≈ $0.004; the whole W20 day ≈ $0.226 against an
authorized ≈ $2.16; the ledger holds $0.289 in total. Options: (a) approve only
the gate re-run (≈ $0.05–0.15); (b) add a capped capacity run (you set the cap —
cost scales with agents × task length, and I have no measured figure for real
tasks); (c) approve none and keep every speed/capacity claim to fixtures.

**4. Which provider/model do you pin, with what cost ceiling?**
Options: (a) pin `anthropic/claude-haiku-5.5` ($0.10/M in, $0.50/M out via
OpenRouter): passes vision 3/3 + 3/3 and the rest of the held-out set once;
(b) keep `qwen/qwen3.7-flash`: cheaper ($0.003–0.005 per vision run) but failed
vision 0/3 and 26.6% layout error; (c) another model: the vision tasks must be
re-run first (W20 limitation). No fallback model is configured, so a provider
outage blocks runs (W22); a fallback is your call and must never be silent.
Per-task budgets in the corpus today are small (for example 0.05 USD for
`code-search-heldout`, 0.15 for `w11-vision`). Evidence:
`README.md` follow-up section, `ledger` rows for `anthropic/claude-haiku-5.5`.

**5. Is a limited design-partner pilot acceptable?**
Engineering reading, not a decision: **(b) accept it after specific gates
close**. The gates are: gate re-run on the deployed final images; W22 follow-up
committed and deployed; a restore drill on the real deployment; on-call named
(Q1); SLO stance (Q2); model pinned (Q4). **(a) accept now** means accepting the
residuals below in writing. **(c) defer** if you want an independent review or
the W7 editor task first.

## Other gates that only a person can close

| Gate | State |
|---|---|
| Counsel on pricing | amounts still `[PLACEHOLDER: counsel review]` |
| Package publish (PyPI/npm) credentials | no decision found |
| Hermes rights | W6 audit: written evidence absent; launch claim must exclude Hermes or wait |
| Independent release-candidate review (W18) | unfinished; reviewer unassigned |
| External security review | not scheduled |
| SSO/OIDC need-by (R-45) | shipped, OIDC drill 14/14 on 2026-10-25; the pilot need-by is yours |
| Backup-freshness alerting; restore on the real deployment | scratch restores pass; real deployment not drilled; the scheduled restore first fires 2026-11-01T12:00Z |

## Risks carried, in short

1. **W7:** the `.vsix` installs and activates in a real VS Code window (four
   commands registered); a task run end to end in that window was **not** run, and
   the packaging config that produced it is uncommitted.
2. **W13:** the execution leg was committed at 23:24 (`55ad4b1`) and the commit
   itself says the gate "stays PARTIAL": same host, test commands only, no agent
   loop. It is not proven with a real customer-controlled runner.
3. **W17:** still in progress in another session; no SLO met or owned; the load
   result is a dry engine on one host.
4. **W22 residuals:** after a worker-daemon kill, an orphan can keep running up
   to grace + interval (about 15 minutes) while lifecycle says
   `mutationPossible: false`; the frontend `replayTruncated` handling is being
   fixed and is uncommitted; one unexplained replay-ordering failure in 9 runs.
5. **Sample sizes:** live gates are n = 1 to 3, mostly one observation per
   failure mode; no rate can be quoted.
6. **Speed:** claims come from fixtures only (tens of seconds per small task).
7. **Spend is partly unknown:** runs killed or stopped mid-turn have no ledger
   row, so recorded W22 spend ($0.0086) is a floor.
8. **Model attribution** for the passing gate rests on the README and ledger
   rows; the raw runner files label the model `qwen/…` or "the stack
   DSH_MODEL" (see README caveats).

## Where to record the decision

`docs/m2-w20-plan.md` → "Decision record": reviewer, decision (pilot / full
release / defer and replan), date. Left blank on purpose.
