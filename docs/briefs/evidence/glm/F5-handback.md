# Hand-back: F5
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: f7af86d
Status: DONE
Token use (estimate): 48000

## Files changed (uncommitted)
- `scripts/eval/bench_stats.py` (edit): pure `split_run` + `split_stats` (`scripts/eval/bench_stats.py:96`), additive optional `split` key on `cell_stats` when attempts carry `events` (`scripts/eval/bench_stats.py:87`).
- `scripts/eval/test_bench_split.py` (new): 13 fixture tests, exact numbers.
- `docs/briefs/evidence/glm/F5-handback.md` (new): this file.

## Checks run
- `cd scripts/eval && ../../section1/.venv/bin/python -m pytest test_bench_split.py -q -rxXs` → `13 passed in 0.01s` (last 15 lines shown in session; 13 dots, no failures).
- `cd scripts/eval && ../../section1/.venv/bin/python -m pytest test_w7_bench.py -q -rxXs` → `13 passed in 1.08s`.
- Byte-identity proof (used): `cell_stats` output on five fixture cells (pass sample, n<5, one FAIL, all ERROR, dry) is JSON-identical to a copy of the pre-change function (`BYTE_IDENTICAL`). Existing attempts have no `events` key, so the new `split` key is absent and old keys are unchanged. `test_w7_bench.py` also still passes and asserts exact p50/p95/rate.

## Checks not run
- Full section4 suite (forbidden: other lanes may be in it).
- Any live `agent_events` replay / model call / Docker (task forbids).
- CLI `main()` on the committed w7-bench directory (would overwrite `w7-stats-*.json`; dry attempts there have no `events`, so output would not change, but I did not write that file).

## Decisions I made (reviewer may overrule)
- Event names: model = `plan_approved`→`execution_started` (the span `w25_metrics.run_rows` reads as the LLM call, `scripts/eval/w25_metrics.py:80`); tool = `tool_call`→`tool_result`; turn stop = `engine_completed` or `engine_failed`.
- Human wait: the eventless gap from a turn stop to the next `execution_started`. Time from the previous event to the stop is harness overhead, not human wait.
- Turns: closed `execution_started`→`engine_completed`/`engine_failed` pairs. An unfinished last turn is not counted.
- Open intervals are dropped (no `now` clamp, unlike `executing_seconds` in `section4/src/harness_orchestrator/execution.py:270`).
- `cacheReadTokens` is a truncated int sum of `payload.usage.cacheReadTokens` or `payload.cacheReadTokens` (run_metering shape, `section4/src/harness_orchestrator/run_metering.py:74`).
- `split` is added to `cell_stats` only when at least one attempt has an `events` list, so existing output stays byte-identical.
- Timestamps are epoch seconds (float). ISO strings would need a second parser; not added.
- Nested tool calls are not double-counted (second `tool_call` while one is open is ignored).

## Open questions
- Should `main()` include `split` in the stdout line, or only in the JSON? I left stdout unchanged for byte-identity.
- Should ISO-8601 `ts` be accepted in addition to epoch seconds?

## Unrelated issues seen
- None in the files I touched. `w7-bench` dry artifacts on disk have no per-run event logs yet.

## Known limits of this change
- Split only appears when the attempt JSON carries an `events` list; `bench_trials.py` does not write that field today.
- `tool_result` is assumed to be the stop event; if the live log uses a different type, tool time will read 0.
- Overhead is residual (`wall - model - tool - human`), so mislabeled events land there rather than crashing.
- No persistence, no CLI flag, no new dependency.
