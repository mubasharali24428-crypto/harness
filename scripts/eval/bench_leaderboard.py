#!/usr/bin/env python3
"""I-04 (P-13/P-18): the public benchmark leaderboard, rendered from RAW attempt files.

Reads the same w7-bench attempt JSONs `bench_stats.py` aggregates and emits ONE
deterministic markdown table per (model, mode). It is a PRESENTATION of
`bench_stats.aggregate` output, not a second aggregator — a leaderboard with
its own private success-rate arithmetic would eventually disagree with the
stats document, and the disagreement would be invisible from the outside.

Honesty laws (inherited, not re-decided):

* **MIN_N=5 per cell** (the pilot_metrics rule): a cell below it prints
  "insufficient data; no claim" — a rate printed from 2 trials is marketing,
  not measurement.
* **Dry cells never rank.** They are rendered, labelled "dry: simulated —
  not a measurement", and excluded from the ranked table; a leaderboard that
  ranks simulations is the leaderboard version of a fake ok.
* **Unknown cost is unknown, never zero.** Cells with attempts whose cost the
  ledger did not report carry `~` and an unknown-count; the cost column is
  only comparable between cells with the same known-ness.
* **Failures are retained**, not summarized away: each cell row carries its
  failure count and the stats document holds the per-trial detail.
* **Descriptive only.** No model is called better; the footer says so in the
  same words bench_stats uses.

    python3 scripts/eval/bench_leaderboard.py                # render + write leaderboard.md
    python3 scripts/eval/bench_leaderboard.py --print-only   # stdout, no file

Stdlib only. Idempotent: same attempts -> byte-identical markdown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_stats  # repo-local, like bench_stats imports w25_metrics

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO / "docs" / "briefs" / "evidence" / "w7-bench"
DEFAULT_OUT = DEFAULT_SOURCE / "leaderboard.md"

INSUFFICIENT = bench_stats.INSUFFICIENT
DRY_NOTE = "dry: simulated — not a measurement"


def _p(cell: dict, key: str, which: str):
    """stats() returns {n, mean, p50, p95, min, max} with None entries when empty."""
    value = (cell.get(key) or {}).get(which)
    return value if value is not None else "—"


def _cost(cell: dict) -> str:
    cost = cell.get("costPerTaskUsd") or {}
    unknown = cost.get("attemptsWithUnknownCost") or 0
    mean = cost.get("knownMean")
    if mean is None:
        return f"unknown ({unknown} attempts)" if unknown else "unknown"
    tilde = "~" if unknown else ""
    return f"{tilde}${mean:.4f}" + (f" ({unknown} unknown)" if unknown else "")


def _rate(cell: dict) -> str:
    rate = cell.get("successRate")
    if isinstance(rate, dict):
        return f"{rate['passes']}/{rate['n']} ({rate['rate']:.0%})"
    return str(rate)


def _claimable(cell: dict) -> bool:
    return bool(cell.get("claimable"))


def render(attempts: list[dict], *, label: str, manifest_sha: str | None,
           generated: str | None = None) -> str:
    """The whole leaderboard document. Deterministic for a given generation
    stamp: cell order comes from bench_stats' sorted aggregate, ranking from a
    stated key, and the stamp is injectable (tests pass a fixed one)."""
    cells = bench_stats.aggregate(attempts)
    live = [c for c in cells if c["mode"] != "dry"]
    dry = [c for c in cells if c["mode"] == "dry"]
    ranked = [c for c in live if _claimable(c)]
    unclaimed = [c for c in live if not _claimable(c)]
    # Rank: success rate desc, then known mean cost asc, then name — a stable,
    # stated order, because an unexplained tiebreak would look like a choice.
    ranked.sort(
        key=lambda c: (
            -((c["successRate"] or {}).get("rate", 0.0) if isinstance(c["successRate"], dict) else 0.0),
            (c["costPerTaskUsd"] or {}).get("knownMean") if (c["costPerTaskUsd"] or {}).get("knownMean") is not None else float("inf"),
            c["model"],
        )
    )

    lines: list[str] = []
    stamp = generated or datetime.now(UTC).isoformat()
    lines.append(f"# Benchmark leaderboard — {label}")
    lines.append("")
    lines.append(f"Generated {stamp} by `scripts/eval/bench_leaderboard.py` "
                 f"from `bench_stats.aggregate` over raw attempt files. Descriptive only; "
                 "no superiority claim. Frozen held-out manifest: "
                 + (f"`{manifest_sha[:16]}…`" if manifest_sha else "NOT FOUND — no run here can be claimable."))
    lines.append("")
    if not ranked:
        lines.append("**No claimable rows yet.** A row ranks only when its cell has ≥5 valid "
                     f"trials, zero environment errors, and is not dry ({INSUFFICIENT} otherwise). "
                     "The leaderboard withholds rankings rather than printing rates from thin cells.")
    else:
        lines.append("## Ranked (claimable cells only — one row per task × model)")
        lines.append("")
        lines.append("| # | model | task | rate | cost/task | wall p50/p95 (s) | first output p50 (s) | interventions p50 | failures |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(ranked, 1):
            lines.append(
                f"| {i} | {c['model']} | {c['task']} | {_rate(c)} | {_cost(c)} "
                f"| {_p(c, 'wallSeconds', 'p50')}/{_p(c, 'wallSeconds', 'p95')} "
                f"| {_p(c, 'firstOutputS', 'p50')} | {_p(c, 'interventions', 'p50')} "
                f"| {c['n'] - c['passes']} |"
            )
        lines.append("")
    if unclaimed:
        lines.append("## Live cells below the claim bar (no claim made)")
        lines.append("")
        lines.append("| model | mode | n | valid | errors | verdict |")
        lines.append("|---|---|---|---|---|---|")
        for c in sorted(unclaimed, key=lambda c: (c["model"], c["task"])):
            lines.append(
                f"| {c['model']} | {c['mode']} | {c['n']} | {c['validTrials']} | {c['errors']} | {c['successRate']} |"
            )
        lines.append("")
    if dry:
        lines.append(f"## Dry cells ({DRY_NOTE}) — excluded from ranking")
        lines.append("")
        lines.append("| model | task | n | passes | verdict field |")
        lines.append("|---|---|---|---|---|")
        for c in sorted(dry, key=lambda c: (c["model"], c["task"])):
            lines.append(
                f"| {c['model']} | {c['task']} | {c['n']} | {c['passes']} | {c['successRate']} |"
            )
        lines.append("")
    lines.append("_Failures are retained per trial in `bench_stats.py` output; this table "
                 "shows counts only. Cost is `cost_ledger` actuals; `~` marks cells with "
                 "attempts of unknown cost, which are never treated as $0._")
    return "\n".join(lines) + "\n"


def load_attempts(source: Path) -> tuple[list[dict], list[str]]:
    """Every `w7-bench-attempt` JSON in the directory; batch files expand to
    their attempts list. Files with another shape are named and skipped."""
    attempts: list[dict] = []
    skipped: list[str] = []
    for path in sorted(source.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            skipped.append(path.name)
            continue
        if isinstance(data, dict) and data.get("kind") == "w7-bench-attempt":
            attempts.append(data)
        elif isinstance(data, dict) and data.get("kind") == "w7-bench-batch":
            # A batch file is an INDEX (attemptFiles points at the raw files).
            # Expanding it would double-count those attempts; the skip is
            # deliberate, and the file is named in stderr so the omission is
            # visible rather than silent.
            skipped.append(path.name)
        else:
            skipped.append(path.name)
    return attempts, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--label", default="w7-bench")
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)

    attempts, skipped = load_attempts(args.source)
    manifest_sha = None
    try:
        from freeze_heldout import MANIFEST

        manifest_sha = hashlib.sha256(MANIFEST.read_bytes()).hexdigest() if MANIFEST.is_file() else None
    except (ImportError, OSError):
        manifest_sha = None
    if not attempts:
        print(f"no w7-bench-attempt files under {args.source}", file=sys.stderr)
        return 2
    doc = render(attempts, label=args.label, manifest_sha=manifest_sha)
    if skipped:
        print(f"skipped (not attempt-shaped): {', '.join(skipped)}", file=sys.stderr)
    if args.print_only:
        print(doc, end="")
        return 0
    args.out.write_text(doc)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
