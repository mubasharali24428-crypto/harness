# P-13 benchmark spend estimate (for owner approval, 2026-11-21)

Basis: measured run costs from the 2026-11-05 live sets on this stack
(`STATUS.md` 2026-11-05 entries): Haiku ~$0.0097-0.011 per text task run,
~$0.0224 for a 4-task vision batch; DeepSeek ~15% cheaper, ~50% slower.

| Component | Count | Unit cost (Haiku) | Estimate |
|---|---|---|---|
| Seen tasks, 5 trials | 23 tasks × 5 = 115 runs | ~$0.01/run | ~$1.15 |
| Held-out tasks, 5 trials | 6 × 5 = 30 runs | ~$0.01/run | ~$0.30 |
| Retained failures margin | ×1.3 | | ~$0.40 |
| Vision tasks (Haiku only) | inside the above | higher/token | ~$0.30 |
| **Haiku total** | | | **≈ $2.20 (cap $5)** |
| DeepSeek pass (same shape) | 145 runs | ~$0.006/run | **≈ $1.20 (cap $3)** |
| Comparator (opencode) | 2 tasks × 5 | its own provider | ≈ $1-3 (separate budget) |

**Ask:** approve a hard cap of **$5 (Haiku) + $3 (DeepSeek) + $3 (comparator)**
= **$11 total**, enforced by `bench_trials.py --max-total-usd` per invocation
and checked against `cost_ledger` actuals; the run stops at the cap and keeps
every raw attempt. One Day-1 budget review if a task times out repeatedly
(timeout retries are the main unbounded cost).

What the money buys: the P-13/Day-14 acceptance line (≥5 trials, failures
retained, p50/p95, cost from ledger) and the W25/W26 "improvement shown
against baseline" evidence — the last fleet-buildable item before the RC's
release review.
