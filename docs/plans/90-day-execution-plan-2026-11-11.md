# 90-day execution plan: design partners, first revenue, evidence-gated scale

Written 2026-11-11. Day 1 is Tue 11 Nov 2026; Day 90 gate is 9–11 Feb 2027. Baseline: branch `w28-s1-delegate`
at `7c1acf4` (same baseline as `FUTURE PLAN.md`; untracked working docs exist). Verdict today: **NOT READY**
(`STATUS.md` 2026-10-24, reaffirmed `FUTURE PLAN.md` §2). This plan is a time-boxed execution overlay on
`FUTURE PLAN.md` milestones **F + A (thin slice) + §10 pilot**. Milestones **B–E are deferred by this plan**
until pilot-demand evidence exists (decision P-D7). Dates and durations are targets, not guarantees. Every task
starts **Not started**; evidence states are the ones defined in `FUTURE PLAN.md` §2. Facts here come from
`STATUS.md`, `FUTURE PLAN.md`, `PRICING.md`, the two-week RC plan, git, and one live test run
(2026-11-11: `section1/tests/test_pricing_page_matches_enforced_caps.py`, 13/13 passed); re-verify any number
before relying on it.

## 0. What "done" means at the Day-90 gate

Exit checklist — every line needs evidence in the repo, not a claim:

1. **≥2 signed design-partner SOWs** by Day 28; **3–5 teams** running by Day 83.
2. **≥$1K MRR collected**, or **≥3 written pay-intents at named prices** (email/SOW, not verbal).
3. **Payment rail live** with a recorded test transaction (P-D2). Billing stays flag-off until then
   (`STATUS.md`: Stripe unavailable in Pakistan).
4. **Linux sandbox evidence on the deployed tag**: dedicated-worker review + Landlock/egress drills;
   every A-01 shell-scope exception fixed or risk-accepted with the owner's name.
5. **Benchmark published**: held-out tasks, ≥5 trials per task per model, p50/p95 latency, cost from
   `cost_ledger`, every failure retained, same-conditions comparator (matches RC plan §0.6). Raw data +
   methodology public.
6. **13 weekly evidence ledger entries** in `docs/briefs/evidence/90day/`; intervention-rate trend reported.
7. **Multiplayer B go/no-go decision recorded** (P-D7) with the pilot evidence it was based on.

Gate missed → the honest next step is another validation cycle, not more features. Owner decides in writing.

## 1. Operating rules

- **Owner = R** (decisions, merges, deploys, spend, sales, final review). **Fleet = S/B/G** per the role
  switchboard in `docs/plans/two-week-release-candidate-plan-2026-11-06.md` §1. Fleet may draft handbacks;
  only R marks anything `Verified at <revision>`.
- **Owner time cap: ≤60% build/review.** Month 1: ≥40% of owner hours on conversations and distribution.
  The W1 outreach list is the load-bearing wall; everything else is decoration until it happens.
- **Friday ledger:** every week ends with one evidence entry per active task (state, revision, raw result
  link) in `docs/briefs/evidence/90day/week-NN.md`. No entry = task not done, regardless of code existing.
- **Invariants unchanged:** no auto-merge, no silent model downgrade, mandatory human plan approval,
  secrets never in logs/model context, frozen contracts (`AGENTS.md`). Approval envelopes (I-05) are an
  owner *policy* decision within the existing law, not a law change.
- This plan does not override binding decisions. `FUTURE PLAN.md` D-01…D-09 remain owner decisions;
  defaults below are planning assumptions only.

## 2. Month 1 — two signed design partners + pilot-ready slice (W1–W4)

| ID | Wk | Role | Task | Acceptance evidence |
|---|---|---|---|---|
| P-01 | W1 | R | 20 outreach messages: Pakistani export software houses + global regulated SMBs. Pitch: "the trust layer that lets your clients accept AI-written code." Track in a simple ledger (name, date, response, next step). | Ledger with 20 sent; ≥5 conversations booked |
| P-02 | W1 | B/G | F-02 branch/worktree inventory + F-03 deploy manifest (already scoped in `FUTURE PLAN.md` §4). Non-destructive; dispositions only. | Inventory doc with commit IDs and disposition per branch; environment manifest + drift report |
| P-03 | W2 | R | 10 conversations. Draft pilot SOW template + dollar pricing band (P-D4). | 10 conversation notes; SOW template committed; ≥2 verbal LOIs |
| P-04 | W2 | S+B | Linux dedicated sandbox worker review + Landlock/egress drill on ubuntu (A-02 thin slice). Reuse the Oct drill that found the two allow-list holes. | Drill output at exact revision on intended host; holes fixed or risk-accepted by name |
| P-05 | W3 | R | Close 2 design partners: signed SOW, success criteria, consent, support contact. Decide D-01 (P-D1). Rename shortlist + trademark check (P-D3). | 2 signed SOWs in `docs/legal/` or pilot dir; decision records |
| P-06 | W3 | S+B | A-01 shell argument/path scope dispositions + negative tests; A-06 one reviewed image set, digests recorded. | Negative-test results on deployed tag; `stack_tags.py` no drift |
| P-07 | W4 | R | Payment rail decision (P-D2): verify Paddle/FastSpring Pakistan seller eligibility vs US LLC + Stripe; start paperwork for the chosen path. Onboard partner #1. | Decision record with eligibility evidence; partner boots stack and completes 1 governed task unaided (tutorial journey) |
| P-08 | W4 | B+G | A-07 green gates at the release revision (CI, section suites, frontend, pg-probe); terms packet to counsel (A-11 slice, uses existing `docs/legal/` packet). | Gate output at exact revision; counsel engagement started with named contact and date |

**Month-1 kill criterion:** <2 signed partners after ≥20 real conversations → the wedge is wrong.
Stop feature work; revisit positioning with the conversation notes before building anything else.

## 3. Month 2 — live supervised pilot, measured (W5–W8)

| ID | Wk | Role | Task | Acceptance evidence |
|---|---|---|---|---|
| P-09 | W5 | R | Both partners live, supervised. Daily intervention log; weekly review call. Target ≥10 real tasks/week/team. | Task log with counts, interventions, denials, cost/task from `cost_ledger` |
| P-10 | W5 | S+B | Fix **only** the top-3 friction items from W5 logs. No speculative features. | 3 fixes with before/after evidence at revision |
| P-11 | W6 | S+B | I-09 verification-first default + I-10 cross-agent pre-review digest (see §6). | Done-signal requires test evidence; human review time measured before/after |
| P-12 | W6 | R | Measure and record: acceptance rate, time-to-first-output, cost/task, intervention trend. | Week-6 ledger with raw numbers, not summaries |
| P-13 | W7 | B/G | Benchmark harness (A-08 / RC plan §0.6): held-out tasks, ≥5 trials, latency, cost, failures retained, comparator. Light week (holidays): async fleet work only. | Reproducible benchmark scripts + raw results committed |
| P-14 | W8 | R | Mid-pilot go/no-go per partner. Collect "would pay $X" **in writing** at a named price. | ≥1 written pay-intent; go/no-go records |
| P-15 | W8 | S+B | I-02 GitHub verdict bot MVP: plan-approved-by, sandbox boundary, tests, cost, risk flags on every agent PR. | Bot comment on a real partner PR; negative test: no secrets in comment |

**Month-2 gate:** ≥10 tasks/week/team sustained, intervention rate trending down, ≥1 written pay-intent.

## 4. Month 3 — first revenue + scale decision (W9–W13)

| ID | Wk | Role | Task | Acceptance evidence |
|---|---|---|---|---|
| P-16 | W9 | R | Convert pilot → paid contract (even $500/mo). Live test transaction on the chosen rail. | Signed contract; test transaction record; first invoice sent |
| P-17 | W9 | S+B | I-01 client-facing trust portal: read-only auditor view for the partner's end-client (every change, approver, test evidence, cost). | Partner's end-client completes a review session; cross-member negative tests pass |
| P-18 | W10 | R | 2 new partners via case study + referrals. Publish benchmark (P-13) with raw data + methodology. | Published results link; ≥1 referral intro; case study with real numbers |
| P-19 | W10 | B+G | I-03 compliance evidence export: audit pack from the event log (SOC2/ISO-style AI change-management artifact). | Sample pack reviewed by partner; retention/permissions checks pass |
| P-20 | W11 | R | Pricing v2 from real cost/task data. Decide open-source flip (P-D5): public repo + launch, or recorded reasons to stay private. | Pricing decision record; repo public or decision recorded |
| P-21 | W11 | S+B | I-05 risk-tiered approval envelopes **only if P-D6 approved**: policy-pre-approved low-risk classes, risky paths still gated, manual merge preserved. | Policy doc + negative tests proving risky paths still require approval |
| P-22 | W12 | R | 3–5 teams total. First invoice **collected**. | Cash received record; team roster with consent |
| P-23 | W12 | S+B | I-08 denial-learning loop: every human correction becomes a project rule + regression test (extends W27 repeated-denial stop). | Repeat-denial count → 0 on pilot tasks; rules linked to corrections |
| P-24 | W13 | R | **Day-90 gate (§0).** Evidence review vs `FUTURE PLAN.md`; multiplayer B go/no-go (P-D7); SOC2 gap list; next-quarter plan. | Dated decision record per gate line; next plan committed |
| P-25 | W13 | S/B | Stabilization only: flake fixes, ledger completeness, STATUS.md refresh at final revision. | Green gates at final revision; STATUS.md known-limits list current |

## 5. Ideas backlog (10x levers)

Priority: **do-first = I-01, I-02, I-05, I-09.** All are fleet-buildable on existing architecture; none
require multiplayer. Effort is a rough fleet-day estimate (S ≤3, M ≤10, L >10), not a commitment.
State for all: **Not started** unless a later evidence entry says otherwise.

| ID | Axis | Idea | Anchor in existing code | Effort | Scheduled |
|---|---|---|---|---|---|
| I-01 | Demand | Client trust portal — read-only end-client auditor view; turns every export shop into a reseller | Section 1 event bus/audit + Section 8 review; scoped read-only role | M | P-17 |
| I-02 | Demand | GitHub verdict bot — governance on every agent PR; Marketplace distribution | Section 2 github service + `harness_contracts` events | M | P-15 |
| I-03 | Demand | One-click compliance evidence pack (AI change-management artifact for auditors) | Section 1 audit + `siem_sink.py` export patterns | M | P-19 |
| I-04 | Demand | Public benchmark leaderboard — raw intervention/cost data as marketing | A-08 harness + `cost_ledger` | S | P-13/P-18 |
| I-05 | Efficiency | Risk-tiered approval envelopes — pre-approved policy classes; approval latency ↓, manual merge intact | Section 4 plan gate + `budget.py`; contracts plan schema | M | P-21 (gated by P-D6) |
| I-06 | Efficiency | Golden plan reuse — cached approved plans for recurring task types, drift detection | Section 4 engine + Section 11 context index | M | Backlog (post-gate) |
| I-07 | Efficiency | Explicit cheap-model routing — draft/exploration cheap, synthesis strong; recorded, never silent | Section 3 `ModelRouter` (downgrade-recording already exists) | S | Backlog |
| I-08 | Efficiency | Denial-learning loop — corrections become project rules + regression tests | W27 repeated-denial stop + project rules (`docs/extensions.md`) | S | P-23 |
| I-09 | Effectiveness | Verification-first runs — done-signal requires failing-test→green evidence | Section 5 `verify.tests` seam (Hx M-track) | S | P-11 |
| I-10 | Effectiveness | Cross-agent adversarial pre-review — cheap reviewer digests risk before human review | Section 8 review + S-role adversarial pattern | M | P-11 |
| I-11 | Effectiveness | Rollback-native artifacts — every run ships revert commit + migration-compat note | Section 5 `diff_engine.py` + forward-only migration law | S | Backlog |
| I-12 | Effectiveness | Outcome pricing meter — price per accepted task from ledger actuals | `cost_ledger` NUMERIC(12,6) + `contracts/budget.py` chain | M | Backlog (informs P-20) |

## 6. Owner decisions needed (defaults are planning assumptions, not approvals)

| ID | Decision | Default until decided |
|---|---|---|
| P-D1 | D-01: self-host first vs cloud first | Self-host first (recommendation from pilot economics); preserve both options; no new cloud-only dependency |
| P-D2 | Payment rail: Paddle/FastSpring (verify PK seller eligibility) vs US LLC + Stripe | Verify W1, decide W4; billing flag-off until decided; no customer payment data before terms approval |
| P-D3 | Rename (collision with Harness Inc, harness.io — same category) | Shortlist + trademark check W3; no public launch under the colliding name |
| P-D4 | Pilot pricing dollars | $500–2K/mo band per partner SOW; counsel review per `PRICING.md` before any quote |
| P-D5 | Open-source flip (MIT already chosen; repo private, 0 stars) | Decide W11; repo stays private until then |
| P-D6 | Approval-envelope policy (I-05) | Not enabled; requires written owner policy + negative tests before any low-risk class skips per-run approval |
| P-D7 | Multiplayer B go/no-go | Deferred; W13 decision from pilot demand evidence, not from plan momentum |

## 7. Dependencies and order

`P-01 outreach ∥ P-02 inventory → P-03/P-05 SOWs → P-04/P-06 Linux+security slice → P-07 onboard →
P-09…P-12 measured pilot → P-14 pay-intent → P-16 paid → P-18/P-20 publish+pricing → P-24 gate`.

External dependencies start Day 1 in parallel: counsel (P-08), payment/entity paperwork (P-07), partner
recruitment (P-01). Benchmark scripts (P-13) may be built any time after P-06. Ideas I-06/I-07/I-11/I-12
stay backlog until the Day-90 gate justifies them.

## 8. Source index

- Repo: `STATUS.md` (readiness, Stripe/Pakistan constraint, Landlock drill history), `FUTURE PLAN.md`
  (milestones, evidence states, D-01…D-09), `PRICING.md` (enforced vs declared caps, pilot offer,
  counsel placeholders), `AGENTS.md` (invariants), `docs/plans/two-week-release-candidate-plan-2026-11-06.md`
  (role switchboard, benchmark bar, RC gates), `docs/legal/` (review packet), `docs/pilot-remediation/`.
- Idea anchors: `section4/src/harness_orchestrator/{engine,budget}.py`,
  `section1/src/harness_identity/{sso,scim,siem_sink}.py`, `section5/src/harness_sandbox/`
  (`confinement.py`, `docker_runner.py`, `egress.py`, `diff_engine.py`), `contracts/src/harness_contracts/`.
- External (checked 2026-11-11): Public First "Pakistan 2026 — The AI Opportunity"
  (aiopportunity.publicfirst.co/pakistan-2026/); MoR comparisons (paddle.com; fastspring.com/blog/paddle-alternatives/;
  ebrands.com/blog-posts/merchant-of-record-companies-compared). Pakistan eligibility for any rail is
  **unverified** — P-D2 requires primary evidence before decision.
- Live verification 2026-11-11: pricing-caps test 13/13 pass at `7c1acf4` working tree; GitHub repo
  `mubasharali24428-crypto/harness` private, 0 stars, 0 forks; 817 commits, one human contributor.

## 9. Maintenance

R updates task evidence states every Friday and after each partner milestone; fleet handbacks are inputs,
not verification. Owner approves scope/priority changes by decision record. Link decisions and raw results;
keep superseded plans as historical references. This file does not certify implementation, readiness,
revenue, or dates.