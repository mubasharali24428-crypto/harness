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
    return out


def aggregate(attempts: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for a in attempts:
        groups.setdefault((a["taskId"], a["model"], a.get("mode", "live")), []).append(a)
    return [cell_stats(t, m, mode, xs) for (t, m, mode), xs in sorted(groups.items())]


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