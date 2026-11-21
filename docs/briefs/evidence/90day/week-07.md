# Week 07 — P-13 benchmark harness: engineering delivery and independent acceptance

Opened 2026-11-13. Base checkout: `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5`,
branch `w28-s1-delegate`, with uncommitted changes. **Not a clean CI-certified release.**
Engineering only. **No paid benchmark run happened in this pass** — no provider call, no
model spend, no Docker, no partner contact. The Docker daemon is down here; the live trial
path and the paid comparator were therefore NOT exercised. The dry pipeline and the offline
tests were run (zero spend); raw commands and exit codes are in §5.

Plan row: `docs/plans/90-day-execution-plan-2026-11-11.md:70` (P-13, W7, B/G): "Benchmark
harness (A-08 / RC plan §0.6): held-out tasks, ≥5 trials, latency, cost, failures retained,
comparator." Protocol bar: `docs/plans/two-week-release-candidate-plan-2026-11-06.md:22`
(§0.6 item 6) and Day 8/Day 9 (`:142-152`).

## 1. Scope and states

| Work | Role | State | Evidence / limitation |
|---|---|---|---|
| Frozen held-out split manifest | B | **Implemented — verified offline** | §3.1; `freeze_heldout.py --write/--check` ran, exit 0; drift test passes |
| N≥5 trial runner — dry mode | B | **Implemented — verified (zero spend)** | §3.2; dry batch ran, 10 raw attempt JSONs, failures/timeouts retained |
| N≥5 trial runner — live mode | B | **Implemented — unverified** | §3.2; reuses `run_eval.execute` + `w25_metrics` ledger join; needs Docker + paid spend; NOT run |
| Offline stats (p50/p95, N<5 refusal) | B | **Implemented — verified offline** | §3.3; tests + dry stats ran, exit 0 |
| Comparator same-conditions budget fix | B | **Implemented — unverified** | §3.4; `compare_harness.py` edited + py_compile only; paid script, NOT run |
| Offline tests (no network/Docker) | B | **Implemented — verified** | §5; 7 passed |
| Paid benchmark trials (n≥5 per model) | X / owner | **Not started — owner-gated** | Needs the deployed stack, Docker and OpenRouter spend; engineering cannot spend |
| Publish benchmark (P-18), superiority claim | R / owner | **Not started — owner-gated** | No rate/superiority claim is made here; dry numbers are a pipeline exercise |
| Mid-pilot pay-intent (P-14) | R / owner | **Not started — owner-gated** | Out of scope for P-13 engineering |

## 2. Audit gap table (read-only, item 1)

What existed before this pass, with file:line evidence. "Present" = reusable as-is;
"partial" = exists but incomplete; "missing" = added this pass.

| Requirement | State | Evidence |
|---|---|---|
| Held-out split | **Partial** | `heldOut` bool per task + ≥5 gate: `run_eval.py:387-388,412-414`; semantics `PROTOCOL.md:60`. 6 tasks flagged true in `corpus.json`. **No frozen manifest / content hash / preregistration lock** (no "frozen" anywhere in `docs/eval`). |
| n≥5 independent trials | **Missing** | `--repeats` is retry-until-first-pass: `run_eval.py:283-334` (break at `:332-333`), help text `:458-459`; `PROTOCOL.md:154-156` says it "cannot support a rate". W25 reached n=5/6 only by manual repeated invocations (`w25-metrics-haiku-block.json` summary[0].n=5). |
| p50/p95 latency | **Present** | nearest-rank `pct()`/`stats()`: `w25_metrics.py:48-58`; applied to wall/start/first-tool/engine/tool/ledger latency `:170-175`; n<20 caveat `:184-185`. |
| cost_ledger source | **Partial** | joined post-hoc by run id via Docker `w25_metrics.py:69-71`, time-window fallback `:75-80,102-104`; unknown→null never zero `:116`. **Not in the per-attempt raw JSON** (`run_eval.py` attempt keys `:319-331` have no cost/runId); no offline path. |
| Failures retained | **Present** | all attempts incl FAIL/TIMEOUT kept `run_eval.py:313-331`; ERROR results `:649-658`; rule `PROTOCOL.md:164-168`; failures list `w25_metrics.py:168-169`. |
| Comparator same-conditions | **Partial** | same task goal+fixture `compare_harness.py:53-64,113-115`; same grader (verifier block unchanged) `:55-57`; same model pin `:37,119`; same price `:38,87`; differences recorded `notSameAsHarness :138-141`; absolute verdict, no relative win `:136` (cf. `run_eval.py:559`). **Budget NOT enforced/recorded**: `--timeout` default 600 `:94` vs corpus 1200 for both tasks; no budget field in evidence. |
| N<5 refusal + intervention count | **Missing** | `w25_metrics.py:190-192` prints `pass={passes}/{n}` at any n; no MIN_N rule. No intervention count. Rule to mirror: `pilot_metrics.py:43` (MIN_N=5), `:131` (insufficient→no claim); interventions = `run_collab_events` `redirect.%` `pilot_metrics.py:171-173`. |

## 3. What was added (reuse first; only the missing pieces)

### 3.1 Frozen held-out manifest — `scripts/eval/freeze_heldout.py`, `docs/eval/heldout-frozen.json`
- `--write` builds a manifest from the corpus's `heldOut: true` tasks: each task id + a
  SHA-256 of its canonical JSON, plus the corpus file hash and revision, `frozen: true`,
  `frozenAt`, and the preregistration `rule`. It **refuses to overwrite** an existing freeze.
- `--check` recomputes the per-task hashes and the held-out set/ids; a changed/added/removed
  held-out task blocks (exit 1). Whole-corpus hash/revision change is **informational**
  (`corpusChanged: true`), not blocking, so editing a non-held-out task does not trip the gate.
  `bench_trials.main()` runs this check before any run and refuses on held-out drift (exit 2).
- Frozen this pass: **6 held-out tasks** — `code-search-heldout`, `stale-edit`,
  `tenant-isolation-api`, `w11-vision`, `w14-design-heldout`, `w8-crash-cleanup`.
  `corpusSha256 = ee69ba757b32306207769f229616d1cd122219c476f2386c3b6511d04fff2d53`,
  `frozenAt = 2026-11-13T05:21:41Z`. No tasks were invented; all six already exist with drivers.
  The four `cost: live` ones still need paid runs to validate (owner-gated); the manifest only
  freezes the split.
- **Caveat (corrects "frozen before any run"):** the freeze locks the split **going forward
  only**. These six tasks were already exercised in earlier baselines — **137 earlier evidence
  files** under `docs/briefs/evidence/eval/` and `w33-rerun/` reference at least one of them
  (65 `baseline-*.json`, 62 driver-native/metrics, 10 `w33-rerun`; from 2026-10-28 on, W25/W33).
  So the freeze does **not** show they were never seen or tuned against. A `caveat` key was added
  to `heldout-frozen.json` (top-level only; no task hash or `corpusSha256` changed), and the
  `freeze_heldout.py` docstring/RULE now say "going forward". (The fix request estimated 76–81
  files; the measured count is 137 by the stated method.)
- Rule recorded verbatim in the manifest: thresholds in `PROTOCOL.md` and this split may not
  be loosened after results are seen; re-freezing is an owner decision recorded in the ledger.

### 3.2 N-trial runner — `scripts/eval/bench_trials.py`
- Runs **N independent trials** (default 5, `MIN_TRIALS = 5`; argparse rejects <5), one raw
  JSON per attempt under `docs/briefs/evidence/w7-bench/`. Each attempt records: run id,
  model, `startedAt`/`finishedAt`, `durationSec`, `exitCode`, `timedOut`, `verdict`,
  `driverVerdict`, `failedChecks`, `firstOutputS`, `toolCalls`, `interventions`, `costUsd`,
  `costSource`, budget, git revision, and the frozen `manifestSha256`. Failures, timeouts and
  denials are all written (never dropped).
- **Live mode** reuses `run_eval.execute(task, repeats=1, ...)` per trial (independent, not
  retry-until-pass) and joins the durable ledger via `w25_metrics.evidence_run_id` /
  `attempt_metrics` (cost_ledger by run id) plus a `run_collab_events redirect.%` count for
  interventions. When the ledger is unreachable, cost/interventions are **null, never zero**.
  Live-spend tasks require `--include-live`; live mode requires `--model`.
- **Dry mode** (`--dry`) uses a deterministic fake model (`dry-fake-model`), zero spend, no
  Docker, no network, no real driver. Every file is labelled `mode: "dry"`,
  `simulation: true`, `costUsd: 0.0` with `costSource: "dry-fake: ... (not a measurement)"`.
  The dry outcome cycle exercises PASS/FAIL/TIMEOUT/DENIED so retention is testable. The batch
  summary is `claimable: false` and notes "not a capability claim".

### 3.3 Offline stats — `scripts/eval/bench_stats.py`
- Reads the raw attempt JSONs (no Docker/network), groups per `(task, model, mode)`, and
  reports p50/p95 wall latency, time-to-first-output, tool-call count, cost per task
  (known mean/total + `attemptsWithUnknownCost`), success rate **with denominator**, and
  intervention count. Reuses `w25_metrics.pct/stats` (nearest-rank; **with n<20 the p95 is just
  the slowest attempt** — caveat now in the output note). The CLI prints `cost partial` when
  `attemptsWithUnknownCost > 0`, so a mean over known rows is never read as a full-cell mean.
- **`MIN_N = 5`** (same rule as `pilot_metrics.MIN_N`): below 5 per live cell it prints
  `"insufficient data; no claim"` for both `successRate` and `comparison`, and `claimable`
  is false. **Dry cells** report `successRate = "dry: simulated, no rate"` (n, passes and the
  failure list kept) and are never claimable even at n≥5. Descriptive only; no superiority claim.

### 3.4 Comparator budget fix — `scripts/eval/compare_harness.py` (minimal; record-only)
- `--timeout` now defaults to the **corpus task's `timeoutSeconds`** (1200 for both
  `refactor`→`multi-file-refactor` and `tests-written`), via a new `harness_budget()` reader,
  instead of the old hardcoded 600. An explicit override is recorded as a difference.
- Evidence now carries a `budget` block (`timeoutSeconds`, `corpusTimeoutSeconds`, the harness
  budget, `sameTimeout`, `differences`) and appends budget differences to `notSameAsHarness`
  (timeout override; harness tool-call cap N vs opencode uncapped; harness cost cap vs opencode
  priced-post-hoc/uncapped). Task, model, grader and price equalisation and the absolute
  (non-superiority) verdict are unchanged. Record only — no enforcement change.
- **Not run** (paid; needs `OPENROUTER_API_KEY` + opencode + spend). py_compile only.

### 3.5 I-04 leaderboard renderer — `scripts/eval/bench_leaderboard.py` (added 2026-11-21)

The plan's I-04 ("public benchmark leaderboard — raw intervention/cost data as
marketing", effort S, scheduled P-13/P-18) had no artifact. This is the
presentation layer over `bench_stats.aggregate` — **no second aggregation
arithmetic**: rows are cells, ranking is a stated key (success rate desc, known
mean cost asc, name), and every honesty law is inherited, not re-decided:

- MIN_N=5 / zero-environment-error / not-dry gates a "claimable" row; anything
  else prints `insufficient data; no claim` and stays out of the ranked table;
- dry cells render, labelled `dry: simulated — not a measurement`, and never
  rank (the current corpus is dry-only, so the committed leaderboard correctly
  shows **"No claimable rows yet"** rather than ranking simulations);
- unknown cost renders `unknown`/`~`, never `$0`; failures appear as counts;
  batch files (`w7-bench-batch`) are skipped BY DESIGN as indexes — expanding
  them would double-count their attempt files (named in stderr either way);
- deterministic for a given generation stamp; 15 offline tests
  (`test_bench_leaderboard.py`), 41/41 across the eval suite.

**Public-page form (2026-11-21, same day — completing the I-04 slice):** the
leaderboard now renders BOTH formats from the SAME computed sections
(`board()` is the single ranking pass; the two renderers consume it, so the
formats cannot disagree). The HTML is a self-contained static page — inline
CSS, no JS, no external assets, all names `escape()`d so a hostile model name
cannot inject — droppable on any static host when P-18/P-D5 publish it.
`make bench-leaderboard` regenerates both after each P-13 batch lands.
Committed artifacts: `docs/briefs/evidence/w7-bench/leaderboard.md` +
`leaderboard.html`. A live leaderboard still needs the owner-gated P-13 spend
(≥5 trials per cell); publication itself is the owner's (P-18, and P-D5 for
public-repo context).

## 4. What was read (audit sources)

`docs/eval/PROTOCOL.md`, `docs/eval/corpus.json` (41 tasks, revision m2-w8),
`scripts/eval/run_eval.py`, `w25_metrics.py`, `compare_harness.py`, `with_model.sh`,
`tasks/code_change_task.py` (seams), W25 raw `w25-metrics-haiku-block.json` +
`baseline-conc-haiku-1-1.json`, `section4/.../pilot_metrics.py` (MIN_N + redirect query),
and the plan rows cited above. `scripts/eval/` and `docs/eval/` were clean (tracked,
unmodified) before this pass, so `compare_harness.py` was editable under the "don't touch
other dirty files" rule.

## 5. What was run — raw commands, output, exit codes

Host: darwin arm64; Python 3.13 (section1 venv for the eval tests); source `7c1acf4` working
tree (uncommitted). Zero spend; no Docker; no network.

```text
$ python3 -m py_compile scripts/eval/{freeze_heldout,bench_trials,bench_stats,test_w7_bench,compare_harness}.py
PYC_EXIT=0

$ python3 scripts/eval/freeze_heldout.py --write
{"ok": true, "frozen": 6, "corpusSha256": "ee69ba75...fff2d53", "manifest": ".../heldout-frozen.json"}
FREEZE_WRITE_EXIT=0

$ python3 scripts/eval/freeze_heldout.py --check
{"ok": true, "heldOut": 6, "corpusChanged": false, "problems": []}
CHECK_EXIT=0

$ python3 scripts/eval/bench_trials.py --task stale-edit --task tenant-isolation-api --trials 5 --dry
mode=dry model=dry-fake-model trialsPerTask=5 counts={attempts:10, pass:4, fail:4, timeout:2, denied:2}
claimable=false  note="DRY pipeline exercise ... not a capability claim."
-> 10 attempt files in docs/briefs/evidence/w7-bench
DRY_EXIT=0

$ python3 scripts/eval/bench_stats.py --label w7-dry
stale-edit            dry-fake-model mode=dry n=5 success=dry: simulated, no rate wall p50=2.05 p95=2.55 ttfo p50=0.35 cost/task=0.0 claimable=False
tenant-isolation-api  dry-fake-model mode=dry n=5 success=dry: simulated, no rate wall p50=1.95 p95=2.45 ttfo p50=0.35 cost/task=0.0 claimable=False
-> docs/briefs/evidence/w7-bench/w7-stats-w7-dry.json
STATS_EXIT=0

$ cd scripts/eval && ../../section1/.venv/bin/python -m pytest test_w7_bench.py -q -rxXs
8 passed in 0.21s
TEST_EXIT=0

$ cd section4 && .venv/bin/python -m pytest tests/unit/test_review_summary.py tests/test_done_signal.py -q
81 passed, 11 warnings in 1.09s
S4_EXIT=0

$ cd section8 && .venv/bin/python -m pytest tests/unit/test_pre_review.py -q
12 passed in 0.01s
S8_EXIT=0

$ python3 scripts/check_env_knobs.py
env knob catalogue complete: 63 runtime reads, all in .env.example      ENV_EXIT=0
$ python3 contracts/ts/generate.py --check
TS mirror up to date.                                                   TS_EXIT=0
$ make guard-isolation
GUARD[isolation] OK: 283 src file(s) across 10 section(s)               ISO_EXIT=0
$ git diff --stat -- contracts
(empty)                                                                 CONTRACTS_EXIT=0
```

**Test-note (honest):** in the first W7 pass one test failed on my own wrong expected substring
(`"no capability claim"` vs the code's `"not a capability claim"`); I corrected the substring
(not weakened) and re-ran to 7 passed. This fix pass added a non-held-out-corpus-edit test and
updated the dry-cycle count tests: **8 passed**. The dry evidence was regenerated (§9) so the
on-disk files match the new cycle; no assertion was weakened or deleted.

**section4 note:** the focused suite above (`test_review_summary.py` + `test_done_signal.py`)
passed 81. The two Docker-dependent real-container tests elsewhere in section4 are **outside
this selection** and were NOT run (Docker daemon down); no claim is made about them.

The dry numbers (p50/p95, cost 0.0; `successRate="dry: simulated, no rate"`) are a **pipeline
exercise on a fake model**, not a measurement of the product and not a capability claim.
`claimable=False` on every dry cell.

## 6. Invariants honored

- **No paid/provider call, no Docker, no network** in anything run here. Dry mode is fake and
  labelled; live mode and the comparator were not executed.
- **Frozen contracts untouched** (`git diff --stat -- contracts` empty); TS mirror up to date.
- **No superiority claim**: stats are descriptive; dry cells never claimable; comparator keeps
  an absolute verdict and records every same-conditions difference.
- **Failures retained**: every attempt (PASS/FAIL/TIMEOUT/DENIED) is its own raw JSON; the
  stats cell keeps a failures list.
- **N<5 → no claim**: `bench_stats.MIN_N = 5` mirrors `pilot_metrics.MIN_N`; below it,
  "insufficient data; no claim" replaces any rate or comparison.
- **Preregistration**: the held-out split is hash-frozen **going forward** (the six tasks were
  already run in 137 earlier evidence files, so the freeze does not show they were never seen or
  tuned against — see §3.1 caveat); thresholds may not be loosened after results are seen; the
  runner refuses on held-out manifest drift (whole-corpus edits are informational, not blocking).
- **Other lanes untouched**: `docs/TUTORIAL.md` (pre-existing diff, SHA-1 of its git diff
  `4b6a900c…` unchanged),
  `week-04.md`, contracts/, docker and all other dirty files were not modified. No commit,
  push, `git add -A`, reset, checkout or stash.

## 7. Test-agent handoff — execute and attach raw results

Bootstrap first. From the repo root (offline parts need no Docker):

```sh
python3 scripts/eval/freeze_heldout.py --check                 # must be ok:true, problems:[]
cd scripts/eval && ../../section1/.venv/bin/python -m pytest test_w7_bench.py -q -rxXs
python3 scripts/eval/bench_trials.py --task stale-edit --task tenant-isolation-api --trials 5 --dry
python3 scripts/eval/bench_stats.py --label w7-dry             # dry cells claimable=False
```

Then the **owner-gated live** acceptance (deployed stack up, Docker running, OpenRouter spend
approved, $10 stop-loss). Never customer data without consent:

```sh
# n>=5 independent live trials per task per model; cost/interventions joined from cost_ledger:
python3 scripts/eval/bench_trials.py --task multi-file-refactor --task tests-written \
  --trials 5 --model anthropic/claude-haiku-5.5 --include-live
python3 scripts/eval/bench_trials.py --task code-search-heldout --trials 5 \
  --model anthropic/claude-haiku-5.5 --include-live          # held-out, reported not tuned
# second model for text tasks (RC plan Day 9), then stats per cell:
python3 scripts/eval/bench_stats.py --label w7-live
# same-conditions comparator (paid; records budget/permission differences, no superiority claim):
OPENROUTER_API_KEY=... python3 scripts/eval/compare_harness.py --task refactor --scratch <dir>
```

Record command, exit code, raw output, run ids, host, Python version, source hash, the frozen
`manifestSha256`, and every failure/timeout/denial. Below N=5 per cell report "insufficient
data; no claim". Do not loosen preregistered thresholds after seeing results. Do not weaken
assertions. Check `cost_ledger` after every paid run; stop a run that exceeds its cap.

## 8. Owner gates that engineering cannot complete

- Running the paid live trials (X spends; owner approves provider/budget/data/run scope).
- Publishing the benchmark with raw data + methodology (P-18) and any success-rate or
  superiority claim — none is made here.
- Mid-pilot go/no-go and written pay-intent (P-14).
- Independent test/security acceptance and release go/no-go at the tested revision.

**No paid benchmark run happened.** No commits, pushes, provider calls, Docker operations,
partner messages, customer-data uploads or signatures were made in this pass. The dry
pipeline and offline tests are verified; the live trial path and the paid comparator are
Implemented — unverified, pending the test agent/owner.

## 9. Follow-up fix pass (2026-11-13) — verified gaps closed

Docs and small script edits only; **no paid run happened**, no Docker, no network. Re-ran the
dry pipeline and offline tests from `scripts/eval`; raw results updated in §5.

1. **Held-out "frozen before any run" was false (docs).** The six held-out tasks already appear
   in **137 earlier evidence files** (65 baselines, 62 driver-native/metrics, 10 w33-rerun; from
   2026-10-28 on, W25/W33). `freeze_heldout.py` docstring/RULE now say the freeze locks the split
   **going forward** and does not show the tasks were never seen/tuned. A top-level `caveat` key
   was added to `heldout-frozen.json` without changing any task hash or `corpusSha256`
   (`--check` still ok, `corpusChanged:false`). §3.1/§6 corrected; TUTORIAL.md wording changed to
   "SHA-1 of its git diff `4b6a900c…`". (The fix request estimated 76–81 files; measured 137.)
2. **Drift gate blocked unrelated corpus edits (code).** `check_manifest` no longer blocks on the
   whole-corpus hash/revision; the gate is the per-task hashes + held-out set/ids. `corpusSha256`
   is informational (`corpusChanged` in `--check` output; a non-blocking note in `bench_trials`).
   A changed/added/removed held-out task still blocks. New test: adding a non-held-out task →
   `check_manifest` returns `[]`; the held-out tamper test still fails.
3. **Smaller items.** (a) dry `successRate="dry: simulated, no rate"` (n/passes/failures kept);
   (b) note adds "with n<20 the p95 is just the slowest attempt"; (c) dry cycle reordered to
   PASS,FAIL,TIMEOUT,DENIED,PASS so the default 5-trial batch exercises denial (counts now
   4 pass / 4 fail / 2 timeout / 2 denied per 10 attempts); (d) cost field `attemptsWithUnknownCost`
   + CLI `cost partial` when >0; (e) `compare_harness.py` records the harness `maxToolCalls` cap as
   a difference (record only).
4. **Regenerated dry evidence.** Deleted the 12 stale dry files, re-ran the dry batch + stats so
   the on-disk evidence matches item 3. Counts/commands/exit codes updated in §5.

Tests: **8 passed** (was 7; +1 non-held-out corpus-edit case). section4 focused suite 81 passed;
section8 pre_review 12 passed; env-knobs 63; TS mirror up to date; isolation 283/10; contracts
diff empty; TUTORIAL.md git-diff SHA-1 still `4b6a900c`. The live trial path and paid comparator
remain **Implemented — unverified** (not run). No paid benchmark run happened.