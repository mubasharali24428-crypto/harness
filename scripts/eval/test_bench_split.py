"""F5 (Plan 8.1, S-01): wall-time split + turns + cache-read tokens.

Pure fixture tests; no Docker, no network, no model call. Mirrors
test_w7_bench.py: run from scripts/eval with a venv python.

    ../../section1/.venv/bin/python -m pytest test_bench_split.py -q -rxXs
"""
from __future__ import annotations

import bench_stats


def ev(typ, ts, seq=None, payload=None):
    return {"type": typ, "ts": ts, "seq": seq, "payload": payload}


def test_empty_and_missing_ts():
    s = bench_stats.split_run([])
    assert s == {"modelSeconds": 0.0, "toolSeconds": 0.0, "overheadSeconds": 0.0,
                 "humanWaitSeconds": 0.0, "wallSeconds": 0.0, "turns": 0,
                 "cacheReadTokens": 0, "inputTokens": 0, "cacheReadRatio": None,
                 "prefixDigests": 0, "nEvents": 0}
    assert bench_stats.split_run(None)["nEvents"] == 0
    # non-numeric ts dropped, not a crash
    assert bench_stats.split_run([{"type": "tool_call", "ts": "2026-11-14"}])["nEvents"] == 0


def test_model_and_tool_spans():
    events = [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 4.0, 2),          # model = 4
        ev("tool_call", 4.0, 3),
        ev("tool_result", 6.5, 4),                # tool = 2.5
        ev("tool_call", 7.0, 5),
        ev("tool_result", 8.0, 6),                # tool += 1 -> 3.5
        ev("engine_completed", 12.0, 7),
    ]
    s = bench_stats.split_run(events)
    assert s["modelSeconds"] == 4.0
    assert s["toolSeconds"] == 3.5
    assert s["humanWaitSeconds"] == 0.0
    assert s["wallSeconds"] == 12.0
    assert s["turns"] == 1
    # accounted = 4 + 3.5 = 7.5 ; overhead = 12 - 7.5 = 4.5
    assert s["overheadSeconds"] == 4.5
    assert s["cacheReadTokens"] == 0


def test_human_wait_is_the_eventless_gap():
    events = [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 2.0, 2),
        ev("engine_completed", 5.0, 3),           # turn 1 ends
        # 5.0 -> 9.0: no events at all = human wait
        ev("execution_started", 9.0, 4),          # turn 2 starts
        ev("engine_completed", 11.0, 5),           # turn 2 ends
    ]
    s = bench_stats.split_run(events)
    assert s["turns"] == 2
    assert s["humanWaitSeconds"] == 4.0
    assert s["modelSeconds"] == 2.0
    # wall 11; accounted 2+4=6; overhead 5 (the second turn's 2s of work)
    assert s["overheadSeconds"] == 5.0


def test_second_model_span_without_tool():
    events = [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 1.0, 2),           # model 1
        ev("plan_approved", 3.0, 3),
        ev("execution_started", 6.0, 4),           # model 3 -> total 4
        ev("engine_completed", 8.0, 5),
    ]
    s = bench_stats.split_run(events)
    assert s["modelSeconds"] == 4.0
    assert s["turns"] == 1
    assert s["wallSeconds"] == 8.0


def test_open_intervals_are_dropped():
    events = [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 2.0, 2),           # model closed = 2
        ev("tool_call", 3.0, 3),                   # never closed -> 0 tool
        ev("plan_approved", 4.0, 4),               # never closed -> not counted
        ev("engine_completed", 10.0, 5),
    ]
    s = bench_stats.split_run(events)
    assert s["modelSeconds"] == 2.0
    assert s["toolSeconds"] == 0.0
    assert s["turns"] == 1


def test_tool_result_without_start_is_ignored():
    events = [
        ev("tool_result", 1.0, 1),
        ev("plan_approved", 2.0, 2),
        ev("execution_started", 5.0, 3),
        ev("engine_completed", 6.0, 4),
    ]
    s = bench_stats.split_run(events)
    assert s["toolSeconds"] == 0.0
    assert s["modelSeconds"] == 3.0


def test_out_of_order_is_sorted_by_ts():
    events = [
        ev("engine_completed", 10.0, 5),
        ev("tool_result", 6.0, 4),
        ev("tool_call", 4.0, 3),
        ev("execution_started", 3.0, 2),
        ev("plan_approved", 0.0, 1),
    ]
    s = bench_stats.split_run(events)
    assert s["modelSeconds"] == 3.0
    assert s["toolSeconds"] == 2.0
    assert s["turns"] == 1
    assert s["wallSeconds"] == 10.0


def test_cache_read_tokens_both_shapes():
    events = [
        ev("plan_approved", 0.0, 1, {"usage": {"cacheReadTokens": 120}}),
        ev("execution_started", 1.0, 2, {"cacheReadTokens": 30}),
        ev("engine_completed", 2.0, 3, {"usage": {"cacheReadTokens": 2.9}}),  # truncated, not rounded
    ]
    s = bench_stats.split_run(events)
    assert s["cacheReadTokens"] == 152
    assert s["turns"] == 1


def test_engine_failed_counts_a_turn_and_human_after():
    events = [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 1.0, 2),
        ev("engine_failed", 3.0, 3),
        ev("execution_started", 8.0, 4),
        ev("engine_completed", 9.0, 5),
    ]
    s = bench_stats.split_run(events)
    assert s["turns"] == 2
    assert s["humanWaitSeconds"] == 5.0


def test_unfinished_last_turn_not_counted():
    events = [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 1.0, 2),
        # no engine_completed
    ]
    s = bench_stats.split_run(events)
    assert s["turns"] == 0


def test_split_stats_cell():
    a1 = {"events": [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 2.0, 2),
        ev("engine_completed", 4.0, 3),
    ]}
    a2 = {"events": [
        ev("plan_approved", 10.0, 1),
        ev("execution_started", 14.0, 2),          # model 4
        ev("engine_completed", 15.0, 3),
    ]}
    cell = bench_stats.split_stats([a1, a2])
    assert cell["n"] == 2
    assert cell["modelSeconds"]["p50"] == 2.0      # nearest-rank of [2, 4]
    assert cell["turns"]["p50"] == 1
    assert cell["cacheReadTokens"]["max"] == 0
    assert bench_stats.split_stats([])["n"] == 0
    assert bench_stats.split_stats([{"verdict": "PASS"}])["n"] == 0


def test_cell_stats_stays_byte_identical_without_events():
    attempts = [{"taskId": "t", "verdict": "PASS", "durationSec": 10.0, "costUsd": 0.01,
                 "firstOutputS": 1.0, "toolCalls": 5, "interventions": 0, "trial": 1}
                for _ in range(5)]
    cell = bench_stats.cell_stats("t", "m", "live", attempts)
    assert "split" not in cell
    assert cell["successRate"] == {"passes": 5, "n": 5, "rate": 1.0}
    assert cell["wallSeconds"]["p50"] == 10.0


def test_cell_stats_adds_split_only_with_events():
    attempts = [{"taskId": "t", "verdict": "PASS", "durationSec": 10.0, "costUsd": 0.01,
                 "firstOutputS": 1.0, "toolCalls": 5, "interventions": 0, "trial": 1,
                 "events": [ev("plan_approved", 0, 1), ev("execution_started", 1, 2),
                            ev("engine_completed", 3, 3)]}
                for _ in range(5)]
    cell = bench_stats.cell_stats("t", "m", "live", attempts)
    assert cell["split"]["n"] == 5
    assert cell["split"]["modelSeconds"]["p50"] == 1.0
    # old keys still present
    assert cell["successRate"] == {"passes": 5, "n": 5, "rate": 1.0}


# ------------------------------------------------- S-01 cache-read split / S-02 prefix


def test_cache_read_ratio_and_input_tokens_from_events():
    events = [
        ev("plan_approved", 0.0, 1),
        ev("execution_started", 1.0, 2),
        ev("engine_completed", 2.0, 3, {"usage": {"inputTokens": 250, "cacheReadTokens": 750}}),
    ]
    s = bench_stats.split_run(events)
    assert s["inputTokens"] == 250
    assert s["cacheReadTokens"] == 750
    assert s["cacheReadRatio"] == 0.75


def test_prefix_digests_count_distinct_prefixes_per_run():
    same = [
        ev("engine_completed", 1.0, 1, {"promptPrefix": {"sha256": "aa", "bytes": 10}}),
        ev("engine_completed", 2.0, 2, {"promptPrefix": {"sha256": "aa", "bytes": 10}}),
    ]
    moved = same + [ev("engine_completed", 3.0, 3, {"promptPrefix": {"sha256": "bb"}})]
    assert bench_stats.split_run(same)["prefixDigests"] == 1
    assert bench_stats.split_run(moved)["prefixDigests"] == 2
    cell = bench_stats.split_stats([{"events": same}, {"events": moved}])
    assert cell["runsWithMovingPrefix"] == 1


def test_cache_run_unknown_is_none_not_zero():
    assert bench_stats.cache_run({"verdict": "PASS"}) is None
    r = bench_stats.cache_run({"usage": {"outputTokens": 9}})
    assert r == {"inputTokens": None, "cacheReadTokens": None, "cacheReadRatio": None,
                 "prefixSha256": None}
    r = bench_stats.cache_run({"usage": {"inputTokens": 100, "cacheReadTokens": 300},
                               "promptPrefix": {"sha256": "abc", "bytes": 5}})
    assert r["cacheReadRatio"] == 0.75 and r["prefixSha256"] == "abc"


def _attempt(inp, cr, digest="d1"):
    return {"taskId": "t", "verdict": "PASS", "durationSec": 10.0, "costUsd": 0.01,
            "firstOutputS": 1.0, "toolCalls": 5, "interventions": 0, "trial": 1,
            "usage": {"inputTokens": inp, "outputTokens": 1, "cacheReadTokens": cr},
            "promptPrefix": {"sha256": digest, "bytes": 100}}


def test_cell_cache_stats_pooled_ratio_and_prefix_stability():
    attempts = [_attempt(100, 900) for _ in range(4)] + [_attempt(500, 500, "d2")]
    cell = bench_stats.cell_stats("t", "m", "live", attempts)
    cache = cell["cache"]
    assert cache["n"] == 5 and cache["nWithUsage"] == 5
    assert cache["pooledCacheReadRatio"] == round(4100 / 5000, 4)
    assert cache["cacheReadRatio"]["p50"] == 0.9
    assert cache["distinctPrefixDigests"] == 2
    assert cache["claim"] == "descriptive only; no superiority claim"


def test_cell_cache_stats_below_min_n_makes_no_claim():
    cell = bench_stats.cell_stats("t", "m", "live", [_attempt(1, 1)])
    assert cell["cache"]["claim"] == bench_stats.INSUFFICIENT


def test_cell_without_usage_has_no_cache_key():
    attempts = [{"taskId": "t", "verdict": "PASS", "durationSec": 1.0, "trial": 1}]
    assert "cache" not in bench_stats.cell_stats("t", "m", "live", attempts)


def test_bench_trials_keeps_only_usage_and_digest(tmp_path):
    import json

    import bench_trials

    evidence = tmp_path / "ev.json"
    evidence.write_text(json.dumps({
        "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 30, "x": "drop"},
        "promptPrefix": {"sha256": "ff", "bytes": 7, "blocks": ["rules_prompt_block"]},
        "finalResponseExcerpt": "never copied",
    }))
    got = bench_trials.engine_cache_fields(f'{{"evidence": "{evidence}"}}')
    assert got == {"usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 30},
                   "promptPrefix": {"sha256": "ff", "bytes": 7}}
    assert bench_trials.engine_cache_fields("no json here") == {}
    inline = bench_trials.engine_cache_fields('log line\n{"usage": {"inputTokens": 3}}')
    assert inline == {"usage": {"inputTokens": 3}}
