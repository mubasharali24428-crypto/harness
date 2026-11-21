#!/usr/bin/env python3
"""I-04 (P-13/P-18): the public benchmark leaderboard, rendered from RAW attempt files.

Reads the same w7-bench attempt JSONs `bench_stats.py` aggregates and renders ONE
deterministic page per format (markdown + self-contained HTML — no JS, no
external assets, droppable on any static host when P-18/P-D5 make it public).
It is a PRESENTATION of `bench_stats.aggregate` output, not a second aggregator
— a leaderboard with its own private success-rate arithmetic would eventually
disagree with the stats document, and the disagreement would be invisible from
the outside. The sections (ranked / below-bar / dry) are computed ONCE and both
renderers consume that data, so the two formats cannot disagree either.

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

    python3 scripts/eval/bench_leaderboard.py                 # write leaderboard.md + .html
    python3 scripts/eval/bench_leaderboard.py --format md     # markdown only
    python3 scripts/eval/bench_leaderboard.py --print-only    # stdout, no files

Stdlib only. Idempotent for a given generation stamp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_stats  # repo-local, like bench_stats imports w25_metrics

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO / "docs" / "briefs" / "evidence" / "w7-bench"
DEFAULT_OUT_MD = DEFAULT_SOURCE / "leaderboard.md"
DEFAULT_OUT_HTML = DEFAULT_SOURCE / "leaderboard.html"

INSUFFICIENT = bench_stats.INSUFFICIENT
DRY_NOTE = "dry: simulated — not a measurement"
CLAIM_BAR = (
    "A row ranks only when its cell has ≥5 valid trials, zero environment "
    f"errors, and is not dry ({INSUFFICIENT} otherwise). The leaderboard "
    "withholds rankings rather than printing rates from thin cells."
)


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


def board(attempts: list[dict]) -> dict:
    """The computed sections, once — both renderers consume this dict."""
    cells = bench_stats.aggregate(attempts)
    live = [c for c in cells if c["mode"] != "dry"]
    ranked = [c for c in live if c.get("claimable")]
    # Rank: success rate desc, then known mean cost asc, then name — a stable,
    # stated order, because an unexplained tiebreak would look like a choice.
    ranked.sort(
        key=lambda c: (
            -((c["successRate"] or {}).get("rate", 0.0) if isinstance(c["successRate"], dict) else 0.0),
            ((c["costPerTaskUsd"] or {}).get("knownMean")
             if (c["costPerTaskUsd"] or {}).get("knownMean") is not None else float("inf")),
            c["model"],
        )
    )
    return {
        "ranked": ranked,
        "unclaimed": sorted(
            (c for c in live if not c.get("claimable")),
            key=lambda c: (c["model"], c["task"]),
        ),
        "dry": sorted(
            (c for c in cells if c["mode"] == "dry"),
            key=lambda c: (c["model"], c["task"]),
        ),
    }


def _header(label: str, manifest_sha: str | None, stamp: str) -> list[str]:
    return [
        f"Generated {stamp} by `scripts/eval/bench_leaderboard.py` "
        "from `bench_stats.aggregate` over raw attempt files. Descriptive only; "
        "no superiority claim. Frozen held-out manifest: "
        + (f"`{manifest_sha[:16]}…`" if manifest_sha else "NOT FOUND — no run here can be claimable."),
    ]


def _footer() -> str:
    return ("Failures are retained per trial in `bench_stats.py` output; this table "
            "shows counts only. Cost is `cost_ledger` actuals; `~` marks cells with "
            "attempts of unknown cost, which are never treated as $0.")


def render_markdown(board: dict, *, label: str, manifest_sha: str | None,
                    generated: str | None = None) -> str:
    stamp = generated or datetime.now(UTC).isoformat()
    lines: list[str] = [f"# Benchmark leaderboard — {label}", ""]
    lines.extend(_header(label, manifest_sha, stamp))
    lines.append("")
    ranked, unclaimed, dry = board["ranked"], board["unclaimed"], board["dry"]
    if not ranked:
        lines.append(f"**No claimable rows yet.** {CLAIM_BAR}")
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
        for c in unclaimed:
            lines.append(
                f"| {c['model']} | {c['mode']} | {c['n']} | {c['validTrials']} | {c['errors']} | {c['successRate']} |"
            )
        lines.append("")
    if dry:
        lines.append(f"## Dry cells ({DRY_NOTE}) — excluded from ranking")
        lines.append("")
        lines.append("| model | task | n | passes | verdict field |")
        lines.append("|---|---|---|---|---|")
        for c in dry:
            lines.append(
                f"| {c['model']} | {c['task']} | {c['n']} | {c['passes']} | {c['successRate']} |"
            )
        lines.append("")
    lines.append(f"_{_footer()}_")
    return "\n".join(lines) + "\n"


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["<table>", "<thead><tr>",
           "".join(f"<th>{escape(h)}</th>" for h in headers),
           "</tr></thead>", "<tbody>"]
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def render_html(board: dict, *, label: str, manifest_sha: str | None,
                generated: str | None = None) -> str:
    """Self-contained static page: inline CSS, no JS, no external assets.
    Everything user-visible is escape()d — a model name is data, not markup."""
    stamp = generated or datetime.now(UTC).isoformat()
    ranked, unclaimed, dry = board["ranked"], board["unclaimed"], board["dry"]
    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head><meta charset=\"utf-8\">")
    parts.append(f"<title>Benchmark leaderboard — {escape(label)}</title>")
    parts.append(
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:60rem;"
        "padding:0 1rem;color:#1a1a1a;background:#fff}"
        "table{border-collapse:collapse;margin:1rem 0;width:100%}"
        "th,td{border:1px solid #ccc;padding:.35rem .55rem;text-align:left}"
        "th{background:#f2f2f2}td.num,th.num{text-align:right}"
        ".note{color:#555}.dry{color:#8a6d3b;background:#fcf8e3;padding:.5rem .75rem}"
        ".banner{background:#eef4ff;border:1px solid #b8cdf2;padding:.75rem 1rem}"
        "</style></head><body>"
    )
    parts.append(f"<h1>Benchmark leaderboard — {escape(label)}</h1>")
    parts.append("<p class=\"note\">" + escape(" ".join(_header(label, manifest_sha, stamp))) + "</p>")
    if not ranked:
        parts.append(f"<div class=\"banner\"><strong>No claimable rows yet.</strong> {escape(CLAIM_BAR)}</div>")
    else:
        parts.append("<h2>Ranked (claimable cells only — one row per task × model)</h2>")
        rows = [
            [i, c["model"], c["task"], _rate(c), _cost(c),
             f"{_p(c, 'wallSeconds', 'p50')}/{_p(c, 'wallSeconds', 'p95')}",
             _p(c, 'firstOutputS', 'p50'), _p(c, 'interventions', 'p50'),
             c["n"] - c["passes"]]
            for i, c in enumerate(ranked, 1)
        ]
        parts.append(_html_table(
            ["#", "model", "task", "rate", "cost/task", "wall p50/p95 (s)",
             "first output p50 (s)", "interventions p50", "failures"], rows))
    if unclaimed:
        parts.append("<h2>Live cells below the claim bar (no claim made)</h2>")
        parts.append(_html_table(
            ["model", "mode", "n", "valid", "errors", "verdict"],
            [[c["model"], c["mode"], c["n"], c["validTrials"], c["errors"], c["successRate"]]
             for c in unclaimed]))
    if dry:
        parts.append(f"<div class=\"dry\"><strong>{escape(DRY_NOTE)}</strong> — the cells below "
                     "are simulated and excluded from ranking.</div>")
        parts.append(_html_table(
            ["model", "task", "n", "passes", "verdict field"],
            [[c["model"], c["task"], c["n"], c["passes"], c["successRate"]] for c in dry]))
    parts.append(f"<p class=\"note\">{escape(_footer())}</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def render(attempts: list[dict], *, label: str, manifest_sha: str | None,
           generated: str | None = None) -> str:
    """Markdown rendering (kept as the primary entry for tests and callers)."""
    return render_markdown(board(attempts), label=label, manifest_sha=manifest_sha,
                           generated=generated)


def load_attempts(source: Path) -> tuple[list[dict], list[str]]:
    """Every `w7-bench-attempt` JSON in the directory. Files with another shape
    are named and skipped — including `w7-bench-batch` files, which are INDEXES
    over attempt files: expanding them would double-count those attempts."""
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
        else:
            skipped.append(path.name)
    return attempts, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--format", choices=("md", "html", "both"), default="both")
    parser.add_argument("--out", type=Path, default=None,
                        help="output markdown path (default leaderboard.md next to the attempts)")
    parser.add_argument("--label", default="w7-bench")
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)

    attempts, skipped = load_attempts(args.source)
    if not attempts:
        print(f"no w7-bench-attempt files under {args.source}", file=sys.stderr)
        return 2
    manifest_sha = None
    try:
        from freeze_heldout import MANIFEST

        manifest_sha = hashlib.sha256(MANIFEST.read_bytes()).hexdigest() if MANIFEST.is_file() else None
    except (ImportError, OSError):
        manifest_sha = None
    b = board(attempts)
    if skipped:
        print(f"skipped (not attempt files): {', '.join(skipped)}", file=sys.stderr)
    if args.print_only:
        if args.format in ("md", "both"):
            print(render_markdown(b, label=args.label, manifest_sha=manifest_sha), end="")
        if args.format in ("html", "both"):
            print(render_html(b, label=args.label, manifest_sha=manifest_sha), end="")
        return 0
    if args.format in ("md", "both"):
        out_md = args.out or DEFAULT_OUT_MD
        out_md.write_text(render_markdown(b, label=args.label, manifest_sha=manifest_sha))
        print(f"wrote {out_md}")
    if args.format in ("html", "both"):
        out_html = (args.out or DEFAULT_OUT_MD).with_suffix(".html")
        out_html.write_text(render_html(b, label=args.label, manifest_sha=manifest_sha))
        print(f"wrote {out_html}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
