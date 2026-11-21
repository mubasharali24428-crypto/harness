# Benchmark methodology — P-13 / A-08 (written 2026-11-21, pre-registration)

The bar is the two-week RC plan §0.6 item 6 and the 90-day plan P-13: ≥5
independent trials per task per model, held-out tasks frozen, p50/p95 latency,
cost from `cost_ledger`, every failure retained, same-conditions comparison
with opencode. This document pins the exact commands BEFORE any run, so
results cannot be tuned after the fact. Preregistration rule
(`docs/eval/PROTOCOL.md`): thresholds and the split may not be loosened after
results are seen.

## 1. Frozen split

`docs/eval/heldout-frozen.json` (frozen 2026-11-13, verified 2026-11-21 via
`freeze_heldout.py --check`: 6 held-out tasks, corpus unchanged). The held-out
tasks are reported SEPARATELY from the seen tasks — a held-out result is the
only one that may be called generalization; the seen-task result is a
regression check.

## 2. The run (owner-approval gates model spend)

```sh
python3 scripts/eval/bench_trials.py --task <id> --trials 5 \
  --model anthropic/claude-haiku-5.5 --max-total-usd <CAP>
# one raw JSON per attempt under docs/briefs/evidence/w7-bench/, failures kept
python3 scripts/eval/bench_stats.py <raw-files...>   # rebuildable summary
python3 scripts/eval/compare_harness.py --task refactor --scratch <dir>
```

- `bench_trials.py` REFUSES fewer than 5 trials and runs N INDEPENDENT
  attempts (not retry-until-pass like `run_eval --repeats`).
- Every attempt file is raw and retained — including failures; `bench_stats`
  rebuilds the summary from raw JSONL so a summary is verifiable, not trusted.
- Latency: wall time per attempt recorded in each raw file; report p50/p95
  per task per model from `bench_stats`.
- Cost: from `cost_ledger` actuals, never the rate card's quote alone.
- Comparator: `compare_harness.py` runs the same task in opencode with the
  same timeout budget; its spend is separate and also capped. opencode is
  installed on this machine (`~/.opencode/bin/opencode`).

## 3. Order of operations

1. Owner approves spend (see `p13-spend-estimate-2026-11-21.md`).
2. Run seen tasks (5 trials × task × model), then held-out (same budget).
3. Run the comparator on `refactor` and `tests-written` (the two tasks it
   supports) under the same model class and budget.
4. `bench_stats` over everything; commit raw + summary + a one-page report
   citing revisions: harness at <git SHA>, opencode version, model ids,
   date. Failures stay in the raw files — no cherry-picking.

## 4. What this does NOT claim

Single-model-single-day results measure today's stack, not a durable
benchmark; the held-out caveat from the freeze record stands (earlier W25/W33
baselines had exercised those tasks).
