#!/usr/bin/env python3
"""ooda_tempo.py — OODA cycle-time per decision class (doctrine §7).

Reads wave-graph status tables (the `| Node | Agent | State | Gate verdict |`
tables ATLAS maintains, as in docs/briefs/mission10-graph.md), extracts the
timestamps embedded in gate-verdict notes (e.g. "re-dispatch x1: first attempt
43m explore then provider timeout"), and reports per-class tempo stats:
median + p95 + budget breach list. v1 is heuristic — it measures what the
graphs record; graphs that omit timings simply contribute no samples.

Decision classes (docs/doctrine/mission-command-doctrine.md §7):
  D1 lane-local   dispatch → DONE
  D2 fix-loop     blocker filed → loop closed
  D3 gate verdict delivery → verdict
  D4 merge        last-DONE → merged
  D5 escalation   question raised → answered

Usage:
    python scripts/doctrine/ooda_tempo.py docs/briefs/mission10-graph.md \
        [--budget D1=60,D2=90,D3=20,D4=45,D5=15]   # minutes, p95 targets
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

# "43m", "1h20m", "90 min", "took 12 minutes"
DURATION_RE = re.compile(
    r"(?:(\d+)\s*h(?:our)?s?\s*)?(?:(\d+)\s*m(?:in)?(?:ute)?s?)\b|\b(\d+)\s*minutes?\b",
    re.IGNORECASE,
)
CLASS_HINTS = {
    "D2_fix_loop": ("fix-loop", "fix loop", "re-dispatch", "redispatch", "loop closed"),
    "D3_gate": ("gate verdict", "rerun:", "re-gate", "ATLAS rerun"),
    "D5_escalation": ("escalat",),
}
BUDGET_DEFAULT = {"D1_lane_local": 60, "D2_fix_loop": 90, "D3_gate": 20, "D4_merge": 45, "D5_escalation": 15}


def parse_minutes(text: str):
    m = DURATION_RE.search(text)
    if not m:
        return None
    hours, mins, plain_mins = m.groups()
    if hours or mins:
        return int(hours or 0) * 60 + int(mins or 0)
    return int(plain_mins)


def classify(note: str) -> str | None:
    low = note.lower()
    for cls, hints in CLASS_HINTS.items():
        if any(h in low for h in hints):
            return cls
    return None


def collect(graph_paths: list[Path]):
    """Yield (class, minutes, source) from status-table rows mentioning durations."""
    samples: list[tuple[str, int, str]] = []
    for gp in graph_paths:
        text = gp.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            row = " ".join(cells)
            minutes = parse_minutes(row)
            if minutes is None:
                continue
            cls = classify(row)
            if cls:
                samples.append((cls, minutes, f"{gp.name}:{line.strip()[:70]}"))
    return samples


def report(samples, budgets: dict) -> int:
    by_class: dict[str, list[int]] = {}
    for cls, minutes, _src in samples:
        by_class.setdefault(cls, []).append(minutes)
    print(f"OODA TEMPO — {sum(len(v) for v in by_class.values())} timed sample(s)\n")
    breaches = 0
    for cls in sorted(BUDGET_DEFAULT):  # stable class order even with no data
        vals = sorted(by_class.get(cls, []))
        label = f"{cls} (budget {budgets.get(cls, '—')}min p95)"
        if not vals:
            print(f"{label}: no samples")
            continue
        med = statistics.median(vals)
        p95 = vals[min(len(vals) - 1, round(0.95 * (len(vals) - 1)))]
        flag = ""
        target = budgets.get(cls)
        if isinstance(target, int) and p95 > target:
            flag = "  << BUDGET BREACH"
            breaches += 1
        print(f"{label}: n={len(vals)} median={med:g}min p95={p95}min{flag}")
        for v, src in [(v, s) for c, v, s in samples if c == cls]:
            print(f"    {v}m — {src}")
    print("\nRule (§7): same class breaching two consecutive waves ⇒ mandatory IMPROVE ledger row.")
    return 1 if breaches else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("graphs", nargs="+", type=Path)
    ap.add_argument("--budget", default="", help="e.g. D1=60,D2=90,D3=20")
    ns = ap.parse_args(argv)

    budgets = dict(BUDGET_DEFAULT)
    if ns.budget:
        for pair in ns.budget.split(","):
            k, _, v = pair.partition("=")
            if k.strip() and v.strip().isdigit():
                budgets[k.strip()] = int(v)
    samples = collect(ns.graphs)
    return report(samples, budgets)


if __name__ == "__main__":
    sys.exit(main())
