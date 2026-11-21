# Two-week plan: release candidate for a private design-partner pilot

Written 2026-11-06. Day 1 is the day you start; the plan is by day number, not date. Baseline: branch `w28-s1-delegate`
at `7c1acf4` (D1 committed locally), PR #24 open with CI green on `19f5cae`. Verdict today: **NOT READY**.
Facts here come from `STATUS.md`, `docs/briefs/PROJECT-STATE.md`, `docs/roadmap-3.0.md`, the hy4 hand-backs and git;
re-verify any number before relying on it. Durations are targets, not guarantees.

## 0. What "done" means on Day 14

Two weeks can deliver a **release candidate for a private design-partner pilot**. It cannot deliver "production
ready": the penetration test, counsel review, a real pilot baseline, a live identity provider, Hermes rights and the
owner's release decision depend on other people (section 8). They must be *started* on Day 1 so they run in parallel.

Exit checklist (every line needs evidence in the repo, not a claim):

1. All work merged to `master`, no unpushed commits, stale worktrees and Oct 17 branches resolved. Tagged `rc-1`.
2. **One** image tag set live. `scripts/ops/stack_tags.py` shows no drift. Image digests recorded.
3. CI green on `master`, including the nightly lane.
4. Shell gate and read-confinement: the owner's option chosen and implemented; every remaining strict xfail either
   fixed or listed as an accepted risk with the owner's name. The `./..` scope question closed.
5. Drills pass on the *deployed* tag: W28 two-agent with real model output, W13 runner (43/43) and exec (16/16), W27 denial.
6. Benchmark: at least 5 trials per task per model, held-out tasks, p50/p95 latency, cost from `cost_ledger`, every
   failure retained, same-conditions comparison with opencode.
7. Ops: `run_smoke.py --real` passes; an alert reaches a real webhook or pager endpoint; a cloud-bucket restore drilled
   (or listed open); Grafana dashboard imported; accessibility run on all pages plus one manual screen-reader walk.
8. Docs: `STATUS.md`, `PROJECT-STATE.md`, capability matrix and the W33 readiness pack refreshed at the final revision,
   with a known-limits list.
9. External gates started, each with a named owner and a date (section 8).

## 1. The model switchboard (read this once, then use the role letters)

Roles are fixed; the model behind a role can change. Each role has a **preference order**. Use the first model that is
available *and* has quota left. Moving down the list never changes what the role is allowed to do.

| Role | Job | Preference order |
|---|---|---|
| **R: Reviewer / integrator** | Rulings, merges, deploys, spend, final docs, security decisions | 1. Claude Pro (Claude Code, strongest model you have) → 2. Codex on GPT Plus (strongest model) → 3. **pause** merges/deploys, keep prep going; never hand R to B or G |
| **S: Second reviewer / implementer** | Adversarial review; bounded implementation in section4/section5 in its own worktree | 1. Codex on GPT Plus → 2. Claude Pro (a cheaper model than R if you can pick) → 3. hy4 for tests only, with R reviewing every line |
| **B: Bulk bounded worker** | Tests, characterisation, drills scripts, decision-note drafts, evidence packs, in the hy4 master-prompt format | 1. hy4 → 2. hy3 → 3. best free model in opencode → 4. Qwen 3.8 Flash → 5. Codex (tests only, if quota is spare). Order set by the Day 1 calibration test |
| **G: Grinder** | Doc drafts, inventories, lint/format, log triage, summary scripts | 1. Qwen 3.8 Flash (free) → 2. free model in opencode → 3. hy3 → 4. hy4 → 5. a cheap model on OpenRouter credit |
| **X: Runtime calls** (the harness's own model calls, benchmark, smoke) | Anything that spends | OpenRouter free credit first (stack default `anthropic/claude-haiku-5.5`; text-only tasks may use `deepseek/deepseek-v4-flash`; vision tasks need Haiku or `qwen/qwen3.7-flash`) → the $10 → stop |

Hard rules that no fallback overrides:

* **Security-path code** (`section5/**`, `policy.py`, `shell_gate.py`, confinement, `agent_tree.py`'s `_covered`) is written
  only by S or R. B and G may only write tests, notes and drafts that R/S review.
* **Only R merges, deploys and spends.** The live stack is shared; one deployer.
* **Spend is run by R (or you), never by B or G.** B and G prepare the commands.
* **Degraded modes.** R unavailable for a day: freeze merges and deploys, keep B/G preparing. R and S both out: stop
  the clock and tell the owner; do not promote B to R. All of B out: S writes the tests directly. X credit exhausted:
  use the $10 under the stop-loss in section 5, then stop.

**Day 1 calibration:** give hy4, hy3 and Qwen the same small task (pin the four `./..` rows from
`hy4-master-prompt-batch5-2026-11-06.md`, task E1). Rank them by: followed the rules, test has a real negative control,
no invented facts, time taken. That ranking replaces the guessed B and G orders above.

## 2. Task type to role

| Task type | Role |
|---|---|
| Security design choice, merge, deploy, paid run, `STATUS.md` edits | R |
| Production edits in section4/section5 | S (R reviews every diff) |
| Adversarial review of a diff before merge | S, then R |
| Tests with a negative control, characterisation tables, drill scripts | B |
| Decision-note drafts, evidence packs, runbook edits, matrix-row drafts | B or G, R verifies |
| Branch/worktree inventory, doc cross-checks, summary scripts, formatting | G |
| Benchmark and smoke execution | R runs, X pays, G writes the summariser |

## 3. Working agreements (they prevent the drift hy4 hit)

* **One integration branch** `rc/two-week` cut from `master` on Day 1. R merges into it; it merges to `master` on Day 13.
* **One worktree per agent account**: `../harness-wb-<name>` via `git worktree add`. Worktree `.venv` dirs are symlinks to
  master's and import master's sources; set `PYTHONPATH=<wt>/contracts/src:<wt>/section1/src:<wt>/section4/src`.
  Two agents never edit the same file. Account-to-worktree map is kept in `docs/plans/agent-map.md`.
* **Batches in the hy4 format**: Assumptions line, numbered plan, hand-back per task (files changed, commands with exit
  codes, negative control with `cmp`, what is NOT verified), stop conditions, a `/private/tmp` list. R reviews and commits.
* **Findings stay private**: security findings go in hand-backs and one decision note, never in `STATUS.md`,
  `PROJECT-STATE.md`, commit text, PR text or test docstrings beyond a one-line reason.
* **Commit by explicit path only.** Never `git add -A`, never `down -v`, never commit an auto-injected
  `<claude-mem-context>` block in `AGENTS.md`. Run each section's tests from its own directory.
* **Daily 15-minute check (R):** `git fetch`, `git status` and `git log` in every worktree; `docker ps` image tags;
  PR and CI state; quota table; `STATUS.md` delta. If any of these disagree with the docs, fix the docs that day.
* **Quota rhythm:** Claude Pro and GPT Plus run on rolling windows. Start each day with the heavy review work, keep prompts
  bounded, and leave B/G batches for the hours when R/S are cooling down.

## 4. The 14 days

Each line: task, then **primary model** and (fallback). Roles are in section 1.

### Week 1: stabilise, secure, finish the core

**Day 1: baseline and calibration**
* Push `7c1acf4`, wait for CI, merge PR #24, sync `master`, cut `rc/two-week`, create worktrees. **Claude Pro** (Codex)
* Measure real limits of every subscription and the OpenRouter balance; fill the table in section 5. **You** with **Claude Pro**
* Inventory branches and worktrees: which Oct 17 Codex patches are unique and unsuperseded. **Qwen** (hy3)
  Decide merge / port / delete per branch. **Claude Pro** (Codex)
* Calibration task E1 on all three B/G models; rank them. **hy4, hy3, Qwen**, ranked by **Claude Pro**
* Start the external gates (section 8). **You**
* Exit: `master` has everything, CI green, quota table filled, B/G order set.

**Day 2: one consistent stack, and hy4 batch 5**
* Redeploy a single tag set per `docs/runbooks/redeploy-worker-and-orchestrator.md`; run `scripts/ops/stack_tags.py`;
  rollback to `vw25b1` is refused by design. **Claude Pro** (Codex, with you watching)
* Run batch 5 tasks D2 (real-boundary shell-gate tests) and E1. **hy4** (hy3, Qwen)
  D3 (read-confinement note), D4 (status drafts), D5 (append corrections). **hy3** (hy4, Qwen)
* Review D2 and D3 hand-backs. **Claude Pro** (Codex)
* Draft `docs/known-limits.md` from `PROJECT-STATE.md` section 4. **Qwen** (hy3)
* Exit: one tag set live, drift check clean, D2/D3 reviewed, no secrets or private files touched.

**Day 3: decide the security option**
* Owner picks read-confinement option A, B, C or D from the D3 note. **You**, advised by **Claude Pro** and a second opinion
  from **Codex** (S). Record the decision with a blank-owner line filled in.
* Settle the `_covered` early-return branch and the `./..` rows. **Claude Pro** (Codex)
* Exit: decision recorded; the xfails each map to a fix or an accepted risk.

**Days 3-4: implement the security fix**
* Implement the chosen option in `section5` and the `_covered` change, in its own worktree, tests first. **Codex** (Claude Pro)
* Flip the xfails and add rows for the new behaviour; negative control against each fix. **hy4** (hy3)
* Review every diff, run `make lint`, `make static-gates`, the section5 and section4 suites. **Claude Pro** (Codex)
* Exit: security tests green on Linux and macOS paths, or the macOS gap documented per option C.

**Day 5: finish delegation (W28)**
* Bind the assignment scope to the child's engine; stop re-emitting `paused` on a repeated stop. **Codex** (Claude Pro)
* Tests and a negative control for both. **hy4** (hy3)
* Update the synthesis docs and `.env.example` wording. **Qwen** (hy3), reviewed by **Claude Pro**
* Exit: section4 suite green; no new `OperationalError` swallowing; no scope that steps outside the workspace.

**Day 6: remote execution (W13) and recovery fixes**
* Close the W13 model-driven remote-host loop gap and the lost-lease items still open. **Codex** (Claude Pro)
* Drill scripts for cancel, lost lease and replay. **hy4** (hy3)
* Fix the `pitr_drill` hazard where `down -v` can name live volumes; guard the live backup prefix. **Codex** (Claude Pro)
* Exit: drills written and run locally; hazards guarded by tests.

**Day 7: integrate, redeploy, week 1 gate**
* Merge all week 1 work into `rc/two-week`, redeploy once, run the full drill sweep (W28 two-agent with real output on
  OpenRouter credit, W13 43/43 and 16/16, W27 denial, tag drift). **Claude Pro** (Codex), X pays
* Triage any failure; write what is not done. **Claude Pro**
* **Go / no-go for week 2.** If the sweep fails, Day 8 becomes a repair day and the cut list in section 6 applies.

### Week 2: prove it, make it operable, document it honestly

**Day 8: benchmark protocol and first runs**
* Freeze the W25/W26 protocol (tasks, trials, held-out set, metrics) before running. **Claude Pro**
* Scripts to collect time to first output, tool calls, cost, p50/p95, cold and warm starts. **hy4** (hy3), summariser **Qwen**
* Run n=5 on the stack default (Haiku), 3 concurrent. **Claude Pro** runs, X pays
* Exit: raw JSON for every attempt, failures included, `cost_ledger` totals recorded.

**Day 9: second model, held-out tasks, comparison**
* Second model (`deepseek/deepseek-v4-flash` for text tasks), vision tasks on Haiku or `qwen/qwen3.7-flash`. **Claude Pro** runs, X pays
* Same-conditions comparison with opencode: same task, model, budget, permissions. Prepare the harness. **hy3** (hy4)
* Analyse results; do not loosen preregistered thresholds. **Claude Pro** (Codex as reviewer)
* Exit: results table with failures; comparison done or listed open.

**Day 10: operations A**
* `run_smoke.py --real` with the OpenRouter key; it overwrites `docs/briefs/evidence/p1/*`, so wait about 2 minutes between
  runs. **Claude Pro** runs, X pays
* Alert routing to a real webhook endpoint (a free Telegram, Slack or ntfy channel) and `scripts/test-alerts.sh`. **Codex** (Claude Pro),
  runbook **Qwen**
* Grafana dashboard import (pulling an image needs your authorization). **You** approve, **Claude Pro** executes
* Exit: an alert received end to end; dashboard imported and looked at.

**Day 11: operations B and accessibility**
* Cloud-bucket restore drill (a free-tier bucket you create); record the result either way. **You** create the bucket,
  **Claude Pro** runs the drill (Codex)
* Load probe: `PROBE_ORCH_REPLICAS=2 scripts/loadtest/run_probe.sh run <vus> 60 <label>` on the target topology. **Claude Pro** (Codex)
* Automated accessibility checks across all pages. **hy4** (hy3, Qwen). Manual VoiceOver walk of the key flows, 1 to 2 hours.
  **You**
* Exit: restore result, capacity numbers, a11y report with the manual findings.

**Day 12: documents and evidence**
* Refresh matrix rows from evidence; flip a row only with an evidence file. Drafts **Qwen** (hy3), checks **Claude Pro**,
  spot-check **Codex**
* Rewrite `STATUS.md`, `PROJECT-STATE.md`, known limits; refresh the W33 readiness pack and W29 capability review at the
  final revision. **Claude Pro** (Codex)
* Exit: `test_program_docs.py` passes, every claim traceable to a file or run.

**Day 13: freeze**
* No new features. Fix only what the drills show. Final redeploy of one tag set, full drill sweep, CI plus nightly, tag
  `rc-1`, record image digests and a database dump. **Claude Pro** (Codex)
* Exit: the checklist in section 0, items 1 to 8.

**Day 14: buffer and hand-off**
* Slip day for anything that failed. Fresh-eyes read of the readiness pack for gaps. **Codex** (Claude Pro)
* Hand the pack to an independent reviewer; plan the next 4 to 8 weeks of external gates. **You**
* Retrospective: record what each model was good and bad at, for the next batch plan. **Claude Pro**

## 5. Quota and budget plan

Fill this in on Day 1; it drives every fallback decision.

| Resource | Limit (measure) | Used | Rule |
|---|---|---|---|
| Claude Pro | weekly allowance, **renews weekly** (renewal weekday: ?) | | R work only. Two allowances in 14 days: spend about 70% by Day 6, keep about 30% for the Day 7 sweep; repeat in week 2 with a reserve for the Day 13 deploy |
| GPT Plus (Codex) | weekly allowance, **renews weekly** (renewal weekday: ?) | | S work. Front-load Days 3-6 (security fix, delegation, remote execution); week 2 needs far less |
| hy4 / hy3 accounts (WorkBuddy, free) | ? per account | | One account per worktree; rotate on a rate limit |
| Qwen 3.8 Flash (QoderWork, free) | ? | | Free grinding; no security work |
| Free models in opencode | ? | | Third bulk lane; pick the best one on the Day 1 calibration |
| OpenRouter credit | $ balance: ? | | Pays all X calls first |
| Cash | $10 | | After the free credit only |

* **Align renewals to the plan.** If a weekly allowance renews mid-week, move that tool's heavy work to just after the
  renewal (Claude Pro: Day 3 security fix review and Day 7 sweep; Codex: Days 3-6 and Day 13). Free-model lanes do not
  touch these allowances, so push volume work (tests, drafts, inventories, summaries) there first.
* **Free-model data terms (check on Day 1).** Free tiers often log prompts or use them for training. This is a private
  product: send free models no secrets, no `.env` contents, no real private files, and no security-finding detail beyond
  what the hy4 prompts already allow in private hand-backs. Decide per provider whether security-path work (D2 findings)
  may go through a free lane at all; if not, S/R do it.
* **$10 stop-loss:** about $4 for benchmark reruns, $3 for smoke and load probes, $3 reserve. Check `cost_ledger` after
  every paid run; stop a run that exceeds its cap. Earlier whole-session spend was cents, so this should be ample;
  if a run costs more than expected, find out why before running again.
* Subscriptions (Claude Pro, GPT Plus) do **not** give API access. Only OpenRouter and the $10 pay for harness runtime calls.
* DeepSeek is cheap but check your network can pay and reach it first; otherwise stay on OpenRouter.

## 6. Cut list (drop in this order if behind)

1. Cloud-bucket restore: document as open.
2. Grafana import: document as open.
3. Opencode comparison: document as open.
4. Manual VoiceOver walk: keep the automated run only.
5. Pager integration: webhook only.

**Never cut:** the security fix or an owner-signed accepted risk, the single consistent redeploy, the drill sweep,
honest docs, and the external gates starting on Day 1.

## 7. Deferred on purpose (documented as known limits)

Multi-host shared storage, Hx language limits (cost enforcement, mid-run revocation, full failure replay) unless a use
case demands them, additional languages, Stripe, and the Scheduler/OR-Tools pilot beyond what is already accepted.

## 8. External gates you start on Day 1 (they set the real calendar)

| Gate | Owner to name | Start by |
|---|---|---|
| Penetration test (parts 52, 58) | | Day 1 |
| Counsel review of terms and pricing | | Day 1 |
| Pilot cohort and measured baseline (part 03) | | Day 1 |
| Live SSO identity provider test (part 37) | | Day 3 |
| SLOs, error budgets, on-call owner | | Day 5 |
| Hermes rights clearance | | Day 5 |
| Independent review of the readiness pack | | Day 12 |
| Release decision (W20) | | After Day 14 |

## 9. Batch prompt skeleton for B and G

1. Role, hard limits, and a pointer to the previous prompt it builds on.
2. Expected start state: HEAD, `git status --short`, and what counts as drift (stop and write `BLOCKED:` otherwise).
3. Tasks in order, each with accept checks and a negative control.
4. What the reviewer keeps (merges, deploys, spend, rulings).
5. Stop conditions, hand-back location, and the "findings stay private" rule.
