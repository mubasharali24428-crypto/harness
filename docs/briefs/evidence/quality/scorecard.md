# Platform and testing scorecards

Owner of this file: `docs/briefs/platform-testing-master-prompt-2026-11-21.md` §3. Every phase
updates it. A point counts only with evidence at a recorded revision; "carried" evidence from an
older revision earns at most partial credit and says so.

## Revision this scorecard was measured at

| Field | Value |
|---|---|
| Date | 2026-11-21 (Q0) |
| Measured revision | `42f337fc` on `w28-s1-delegate`, in a detached worktree (other lanes committed six times on the shared checkout during Q0, from `8349f93` to `42f337fc`; the pinned worktree keeps every count at one SHA). Not pushed |
| Deployed RC tag | `vv90r6` (`harness-rc`; per `docker ps` all backends on `harness-backend:vv90r6`, frontend `harness-frontend:vv90r6`) |
| CI of record | **this Mac** (`make gate` components + per-suite runs, Q0 report §3). GitHub Actions is blocked by account billing: every job at `b9890e1` failed in 2 s with "The job was not started because recent account payments have failed or your spending limit needs to be increased" (`q0-gh-actions-billing-2026-11-21.txt`) |

## How these numbers relate to the "88%" and "70%" estimates

The 2026-11-21 estimates (platform ≈88%, testing ≈70%) weighted the 61-row capability matrix
and the size and greenness of the suites. This scorecard scores something different: the
**remaining gates** in prompt §3, each worth points only when its full-marks condition is met
with evidence. Most of the work already done (50 verified matrix rows, ~6,000 green tests) is the
floor these gates build on, not points inside them. So the Q0 gate scores below are low on purpose,
and are not a regression.

## 3a. Platform and backend

| # | Gate | Pts | Q0 score | Why (evidence) |
|---|---|---|---|---|
| PL-1 | Matrix is current | 8 | **2** | Header counts agree with the 61 rows today (recount in Q0 report §4: 50 / 1 / 8 / 0 / 2) and `test_program_docs.py` is green at `42f337fc`, but no row has been re-verified since 2026-10-21 and at least two rows conflict with newer evidence (row 55 vs `f81dd7a` Grafana import; row 26 vs `pilot-prep/real-result.json`) |
| PL-2 | Backend gaps the UI needs | 14 | **2** | (e) `deliveredUpTo` on collab history shipped 2026-11-16 (`evidence/ui/u-open-items-report-2026-11-15.md` §2026-11-16), not re-verified at HEAD: 1.5 of 2.8. (b) client-side "team key set" only: 0.5. (a), (c), (d) not built (`platform-open-items.md` P-02…P-06) |
| PL-3 | Engine correctness limits | 14 | **0** | None of the five items closed; `engine.py:1178` confirmed to use `self.workspace` (P-07…P-11) |
| PL-4 | Metering and quotas | 10 | **1** | OpenRouter A/B booked ledger cost (`perf-audit-2026-11-21.md` §5b), but which transports report usage is unmeasured; in-flight spend is an estimate; `--real` row unresolved (P-12…P-14) |
| PL-5 | Delegation and remote execution | 12 | **1** | Delegation built and drilled behind `HARNESS_SUBAGENTS` (W28 S1a/S1b); scope recorded, not bound; two rulings open; no criteria doc; W13 not re-drilled (P-15…P-18) |
| PL-6 | Agent efficiency follow-ups | 8 | **3** | A/B measured and PR #25 open with the two changes (not merged); `pytest` added to `worker.Dockerfile` by the security lane in `42f337fc` (code only, no image built); grep-tool finding open; Landlock cache not started (P-19…P-22) |
| PL-7 | Data lifecycle | 10 | **1** | Script-path holds exist (`test_backup_holds.py`); bucket lifecycle not covered; residency deployment-wide (P-23, P-24) |
| PL-8 | Multi-host readiness | 10 | **1** | Shared one-host volume across two replicas drilled (`evidence/s12/part54.md`); no store interface, no object-store backend, not in backups (P-25, P-26) |
| PL-9 | Full acceptance (row 61) | 14 | **0** | No acceptance run at one revision; components listed in P-27 |
| | **Total** | 100 | **11** | |

## 3b. Testing, eval and benchmark

| # | Gate | Pts | Q0 score | Why (evidence) |
|---|---|---|---|---|
| TE-1 | CI is real again | 10 | **0** | Actions blocked (billing); no `make ci-local`. `make gate` exists but stops at the first failure and writes no junit. Also, ci.yml runs neither `packages/{extensions,ops,telemetry}`, the two connectors, `scripts/**/test_*.py` nor `clients/{python,ts,vscode}` tests (Q0 report §3) |
| TE-2 | Test and skip ledger | 8 | **4** | Ledger drafted in Q0 (`test-ledger.md`): all 132 runtime skips and 4 xfails at `42f337fc` classified, plus a 72-line static marker scan. The prompt's "10 macOS read-scope xfails" are already resolved (no row marked). Open: 2 of the 4 G-row xfails have no disposition; checkpoint-1 skip; live-PG and Linux rows not executed; Playwright specs not ledgered |
| TE-3 | Flake-free | 10 | **0** | No random-order or repeat runs exist; neither `pytest-randomly` nor `pytest-random-order` is installed in any venv |
| TE-4 | Depth on critical modules | 12 | **0** | No coverage tooling, no mutation tool, no Hypothesis tests anywhere in the repo (grep, Q0 report §4) |
| TE-5 | Contract and integration | 8 | **3** | `spec_drift.py` and the TS mirror check green inside `make static-gates`; `make check-ts-dist` green; upgrade rehearsal starts at 0019, not the oldest schema; SDK tests exist but are not consumer tests against running services and are not in CI; live-PG suites not run in Q0 |
| TE-6 | Eval corpus that measures real work | 14 | **2** | `run_eval.py --check`: 41 tasks, 39 implemented, 6 held-out frozen; no task with a test step, no security bait tasks, no real-repo snapshot, no recovery task |
| TE-7 | Live benchmark | 16 | **3** | Preregistration and the $11 cap written (`benchmark-methodology-2026-11-21.md`, `p13-spend-estimate-2026-11-21.md`); `bench_trials.py` refuses n<5; not dry-verified end to end for the expanded corpus, not run |
| TE-8 | Published and reproducible | 8 | **1** | `bench_leaderboard.py` renders ("No claimable rows yet"); no report, no reproduction README |
| TE-9 | Performance and load regression | 8 | **1** | Replay exists only in the perf audit's scratchpad (`/b/replay.py`), no stored baseline/threshold in `scripts/eval/`; load probe and its s12 baseline exist (`scripts/loadtest/run_probe.sh`, `evidence/s12/part54.md`), not re-run on vv90r6 |
| TE-10 | Failure-mode drills as tests | 6 | **1** | Drill scripts exist (`scripts/e2e/w22_restart_replay_drill.py`, `w22_crash_cleanup_drill.py`, `w22_provider_egress_drill.py`) with evidence in `evidence/w22/`; lost-lease and partial-write are not scripted as repeatable assertions against a throwaway stack |
| | **Total** | 100 | **15** | |

## Ceiling (from the prompt, restated)

Owner-only points: TE-1 the last 5 (billing), TE-7 the last 10 (spend), PL-4 the `--real` leg
(budget), PL-5 the rulings, PL-7 residency decision, PL-9 the external caps named in the GO/NO-GO.
