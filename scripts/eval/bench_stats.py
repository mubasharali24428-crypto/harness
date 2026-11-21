#!/usr/bin/env python3
"""P-13 (W7): offline per-cell benchmark stats over w7-bench attempt JSON.

No Docker, no network: reads the raw attempt files bench_trials.py wrote and
aggregates per (task, model, mode). Reports p50/p95 wall latency, time to first
output, tool-call count, cost per task, success rate WITH denominator, and
intervention count. Below MIN_N=5 per cell it prints "insufficient data; no
claim" instead of a rate or a comparison — the same rule as pilot_metrics.MIN_N
(section4/src/harness_orchestrator/pilot_metrics.py:43). Dry cells are labelled
and never claimable. Descriptive only; no superiority claim. Percentiles reuse
w25_metrics' nearest-rank pct/stats.

    python3 scripts/eval/bench_stats.py --label w7-dry
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from w25_metrics import pct, stats  # nearest-rank percentiles, reused

# F5 (Plan 8.1, S-01): wall-time split per run over the agent_events log.
# Mirrors w25_metrics.run_rows: the engine's billed spans are the timestamps
# of these event types (section4/src/harness_orchestrator/service.py:3077).
MODEL_START = "plan_approved"
MODEL_STOP = "execution_started"
TOOL_START = "tool_call"
TOOL_STOP = "tool_result"
TURN_STOP = "engine_completed"
TURN_START = "execution_started"

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "docs" / "briefs" / "evidence" / "w7-bench"
MIN_N = 5  # same rule as section4 pilot_metrics.MIN_N
INSUFFICIENT = "insufficient data; no claim"


def cell_stats(task: str, model: str, mode: str, attempts: list[dict]) -> dict:
    n = len(attempts)
    passes = sum(1 for a in attempts if a.get("verdict") == "PASS")
    errors = sum(1 for a in attempts if a.get("verdict") == "ERROR")
    valid = n - errors  # ERROR = the driver never produced a verdict (environment), not a product result
    known_cost = [a["costUsd"] for a in attempts if a.get("costUsd") is not None]
    out = {
        "task": task, "model": model, "mode": mode, "n": n, "validTrials": valid, "errors": errors,
        "passes": passes,
        "failures": [{"trial": a.get("trial"), "verdict": a.get("verdict"),
                      "driverVerdict": a.get("driverVerdict"),
                      "failedChecks": a.get("failedChecks")}
                     for a in attempts if a.get("verdict") != "PASS"],
        "wallSeconds": stats([a.get("durationSec") for a in attempts]),
        "firstOutputS": stats([a.get("firstOutputS") for a in attempts]),
        "toolCalls": stats([a.get("toolCalls") for a in attempts]),
        "interventions": stats([a.get("interventions") for a in attempts]),
        "costPerTaskUsd": {
            "knownTotal": round(sum(known_cost), 6) if known_cost else None,
            "knownMean": round(sum(known_cost) / len(known_cost), 6) if known_cost else None,
            "unknownAttempts": n - len(known_cost),
            "attemptsWithUnknownCost": n - len(known_cost),
        },
        "claimable": valid >= MIN_N and errors == 0 and mode != "dry",
    }
    if mode == "dry":
        out["successRate"] = "dry: simulated, no rate"
        out["comparison"] = "dry: simulated, no comparison"
    elif errors:
        out["successRate"] = f"{INSUFFICIENT} ({errors} of {n} trials errored before a verdict: environment, not a result)"
        out["comparison"] = INSUFFICIENT
    elif valid < MIN_N:
        out["successRate"] = INSUFFICIENT
        out["comparison"] = INSUFFICIENT
    else:
        out["successRate"] = {"passes": passes, "n": valid, "rate": round(passes / valid, 4)}
        out["comparison"] = "descriptive only; no superiority claim"
    # F5: additive only. Cells without per-run event logs keep the old shape.
    if any(isinstance(a.get("events"), list) for a in attempts):
        out["split"] = split_stats(attempts)
    return out


def aggregate(attempts: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for a in attempts:
        groups.setdefault((a["taskId"], a["model"], a.get("mode", "live")), []).append(a)
    return [cell_stats(t, m, mode, xs) for (t, m, mode), xs in sorted(groups.items())]


def split_run(events: list[dict] | None) -> dict:
    """F5: pure wall-time split of ONE run from its ordered agent_events.

    Intervals (closed only; no now-clamp, unlike execution.executing_seconds):
      model   — plan_approved -> execution_started (the LLM call span w25 reads)
      tool    — each tool_call -> the next tool_result
      human   — engine_completed/.../engine_failed -> the next turn's
                execution_started (a gap with no events is a human wait)
      overhead — the remaining wall time inside the run
    Open intervals are dropped (a truncated log must not invent time).
    Missing timestamps, equal seq, or a stop without a start are skipped.
    cacheReadTokens sum payload["usage"]["cacheReadTokens"] / payload["cacheReadTokens"].
    """
    ev = sorted(
        (e for e in (events or []) if isinstance(e, dict) and isinstance(e.get("ts"), (int, float))),
        key=lambda e: (e["ts"], e.get("seq") or 0),
    )
    n = len(ev)
    model = tool = human = 0.0
    cache_tokens = 0
    turns = 0
    open_model: float | None = None
    open_tool: float | None = None
    open_turn: float | None = None  # turn in progress (started, not yet completed)
    stop_ts: dict[str, float] = {}
    saw_stop = False
    prev_ts: float | None = None
    for e in ev:
        ts = e["ts"]
        typ = e.get("type")
        pay = e.get("payload")
        if isinstance(pay, dict):
            u = pay.get("usage")
            tok = u.get("cacheReadTokens") if isinstance(u, dict) else pay.get("cacheReadTokens")
            if isinstance(tok, (int, float)):
                cache_tokens += int(tok)
        if typ == TOOL_STOP and open_tool is not None:
            tool += max(0.0, ts - open_tool)
            open_tool = None
        elif typ == TOOL_START and open_tool is None:
            open_tool = ts
        if typ == MODEL_STOP and open_model is not None:
            model += max(0.0, ts - open_model)
            open_model = None
        elif typ == MODEL_START and open_model is None:
            open_model = ts
        if typ in (TURN_STOP, "engine_failed"):
            if open_turn is not None:
                turns += 1
                open_turn = None
            stop_ts[typ] = ts  # human wait opens here, not at the previous event
            saw_stop = True
        elif typ == TURN_START:
            if saw_stop:
                # gap with NO events between turn end and next start = human wait
                human += max(0.0, ts - stop_ts.get(
                    "engine_completed", stop_ts.get("engine_failed", ts)))
                saw_stop = False
            open_turn = ts
        prev_ts = ts
    wall = (ev[-1]["ts"] - ev[0]["ts"]) if n else 0.0
    accounted = model + tool + human
    overhead = max(0.0, wall - accounted)
    return {
        "modelSeconds": round(model, 3),
        "toolSeconds": round(tool, 3),
        "overheadSeconds": round(overhead, 3),
        "humanWaitSeconds": round(human, 3),
        "wallSeconds": round(wall, 3),
        "turns": turns,
        "cacheReadTokens": cache_tokens,
        "nEvents": n,
    }


def split_stats(attempts: list[dict]) -> dict:
    """Per-cell stats over per-run splits. attempts are {"events": [...]} or already-split dicts."""
    runs: list[dict] = []
    for a in attempts or []:
        if isinstance(a, dict) and isinstance(a.get("events"), list):
            runs.append(split_run(a["events"]))
        elif isinstance(a, dict) and "modelSeconds" in a:
            runs.append(a)
    if not runs:
        return {"n": 0, "modelSeconds": stats([]), "toolSeconds": stats([]),
                "overheadSeconds": stats([]), "humanWaitSeconds": stats([]),
                "turns": stats([]), "cacheReadTokens": stats([])}
    return {
        "n": len(runs),
        "modelSeconds": stats([r["modelSeconds"] for r in runs]),
        "toolSeconds": stats([r["toolSeconds"] for r in runs]),
        "overheadSeconds": stats([r["overheadSeconds"] for r in runs]),
        "humanWaitSeconds": stats([r["humanWaitSeconds"] for r in runs]),
        "turns": stats([r["turns"] for r in runs]),
        "cacheReadTokens": stats([r["cacheReadTokens"] for r in runs]),
    }


def load_attempts(dir_path: Path) -> list[dict]:
    out = []
    for f in sorted(dir_path.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("kind") == "w7-bench-attempt":
            out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="P-13 offline benchmark stats (no Docker/network)")
    ap.add_argument("--dir", default=str(BENCH))
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    attempts = load_attempts(Path(a.dir))
    cells = aggregate(attempts)
    out = {"schema": 1, "kind": "w7-bench-stats", "label": a.label,
           "generatedAt": datetime.now(UTC).isoformat(), "minN": MIN_N,
           "note": "nearest-rank p50/p95; with n < 20 the p95 is just the slowest attempt; "
                   "below N=5 per cell: insufficient data; no claim. "
                   "Dry cells are a pipeline exercise, never a capability claim. Descriptive only.",
           "cells": cells}
    path = Path(a.dir) / f"w7-stats-{a.label}.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    for c in cells:
        rate = c["successRate"]
        rate_s = rate if isinstance(rate, str) else f"{rate['passes']}/{rate['n']}={rate['rate']}"
        cost = c["costPerTaskUsd"]
        cost_s = str(cost["knownMean"]) + (" (cost partial)" if cost["attemptsWithUnknownCost"] > 0 else "")
        print(f"{c['task']:24} {c['model']:20} mode={c['mode']:4} n={c['n']} "
              f"success={rate_s} wall p50={c['wallSeconds']['p50']} p95={c['wallSeconds']['p95']} "
              f"ttfo p50={c['firstOutputS']['p50']} cost/task={cost_s} "
              f"claimable={c['claimable']}")
    print("->", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())