#!/usr/bin/env python3
"""M19-B1 — summarize baseline sweep CSVs into a BASELINE.md-ready table.

Reads scripts/loadtest/results/baseline{8,16,32,64}_stats.csv (locust --csv
output) and prints per-level journey/engine/control-plane percentiles plus
throughput. Read-only.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
LEVELS = [8, 16, 32, 64]
ROWS = ("journey-total", "engine-wait", "control-plane", "login", "project")


def pct(cols: dict[str, str], key: str) -> float:
    raw = cols.get(key, "")
    try:
        return float(raw)
    except ValueError:
        return float("nan")


def main() -> None:
    hdr = (
        f"{'level':>5} {'name':<14} {'reqs':>6} {'fails':>6} {'p50':>7} "
        f"{'p95':>7} {'mean':>8} {'max':>8} {'rps':>7}"
    )
    print(hdr)
    print("-" * len(hdr))
    for level in LEVELS:
        path = RESULTS / f"baseline{level}_stats.csv"
        if not path.exists():
            print(f"{level:>5}  MISSING {path.name}")
            continue
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                if row["Name"] not in ROWS and row["Name"] != "Aggregated":
                    continue
                if row["Type"] not in ("FLOW", ""):
                    continue
                print(
                    f"{level:>5} {row['Name']:<14} {row['Request Count']:>6} "
                    f"{row['Failure Count']:>6} {pct(row, '50%'):>7.0f} "
                    f"{pct(row, '95%'):>7.0f} {float(row['Average Response Time']):>8.1f} "
                    f"{float(row['Max Response Time']):>8.0f} "
                    f"{float(row['Requests/s']):>7.2f}"
                )
        print()


if __name__ == "__main__":
    sys.exit(main())
