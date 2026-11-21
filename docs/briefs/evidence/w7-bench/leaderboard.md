# Benchmark leaderboard — w7-bench

Generated 2026-11-21T14:53:58.361918+00:00 by `scripts/eval/bench_leaderboard.py` from `bench_stats.aggregate` over raw attempt files. Descriptive only; no superiority claim. Frozen held-out manifest: `e02dc7bc8aed9a5b…`

**No claimable rows yet.** A row ranks only when its cell has ≥5 valid trials, zero environment errors, and is not dry (insufficient data; no claim otherwise). The leaderboard withholds rankings rather than printing rates from thin cells.
## Dry cells (dry: simulated — not a measurement) — excluded from ranking

| model | task | n | passes | verdict field |
|---|---|---|---|---|
| dry-fake-model | stale-edit | 10 | 4 | dry: simulated, no rate |
| dry-fake-model | tenant-isolation-api | 5 | 2 | dry: simulated, no rate |

_Failures are retained per trial in `bench_stats.py` output; this table shows counts only. Cost is `cost_ledger` actuals; `~` marks cells with attempts of unknown cost, which are never treated as $0._
