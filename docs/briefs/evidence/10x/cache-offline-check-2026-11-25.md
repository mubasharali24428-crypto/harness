# S-02 / S-01 offline cache check (2026-11-25)

Branch `claude/project-thread-4hfm82-cache`, stacked on `w28-s3-memory-prefix` (PR 39).
Cloud only, zero spend. Report: `cache-offline-check-2026-11-25.json` (this folder).

## What it is

`scripts/eval/cache_offline_check.py` (`make cache-offline-check`) replays a recorded
five-attempt run, `scripts/eval/fixtures/cache-replay-v1.json`, through the **real**
executor seam (`OrchestratorService._execute`, capturing engine, throwaway SQLite) and
then through the **real** `scripts/eval/bench_stats.py`. The attempts are: first attempt,
resume after a pause with a rule edited mid-run, a guidance turn with a memory note
reviewed mid-run, a retry after leader approval, and a final guidance turn.

CI runs it through `section4/tests/unit/test_cache_offline_check.py` (section4 pytest leg).

## Result: PASS (5/5)

| Check | Result |
|---|---|
| C1 same `promptPrefix` sha256 on all 5 engine results, equal to sha256 of the goal's first `bytes` bytes | PASS, 1 distinct digest |
| C2 identical 442 prefix bytes on every goal; mid-run rule and memory edits never reach a later goal; guidance lands after the task text | PASS, 0 edits leaked |
| C3 simulated byte-prefix cache: common prefix with the previous attempt covers the stable part (prefix + task = 522 bytes) | PASS, reusable bytes 522 / 534 / 534 / 535 |
| C4 bench_stats cache split equals hand-computed values | PASS, pooled 0.6183, per attempt 0.0 / 0.7857 / 0.7674 / 0.7765 / 0.75, nWithUsage 5, 1 prefix digest |
| C5 control: with the freeze removed (pre-S-02 re-render), C1 and C4 fail | PASS (detected): 3 digests, reusable bytes drop to 27 after the mid-run rule edit |

The unit wrapper also proves the check fails on a wrong expected ratio (exit 1).

## What this does and does not show

* **Shows (verified offline):** the frozen prefix gives byte-identical leading bytes across
  turns of one run even when rules/memory change mid-run, volatile data stays after the
  task, and `bench_stats` computes the S-01 cache-read split correctly.
* **Does not show:** any real provider cache-hit ratio, cost saving or TTFT change. The
  fixture's usage numbers are **synthetic** (shaped like `run_metering` usage). The SDK's
  own system prompt and tool schemas are outside this repo and are not checked.
* Real cache-read ratios need a **live paid batch on the owner's Mac** (`bench_trials.py`
  against a real provider, 5 trials minimum for any claim, per `MIN_N`). Not run.

## Commands (cloud container)

```
make cache-offline-check                                        -> PASS (exit 0)
section4/.venv/bin/python scripts/eval/cache_offline_check.py --out docs/briefs/evidence/10x/cache-offline-check-2026-11-25.json -> exit 0
cd section4 && ./.venv/bin/python -m pytest tests/unit/test_cache_offline_check.py tests/unit/test_prompt_prefix.py -q -> 19 passed (exit 0)
section4/.venv/bin/python -m pytest scripts/eval/test_bench_split.py scripts/eval/test_w7_bench.py -q -> 33 passed (exit 0)
cd section4 && ./.venv/bin/ruff check src tests -> clean (exit 0)
```

Boundaries unchanged: no new write path, no prompt content added, no geometry in prompt
bytes, no migration, no `contracts/` change.
