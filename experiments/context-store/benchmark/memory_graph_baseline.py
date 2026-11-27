"""X-14 memory baseline: production memory layers vs a plain graph over the same rows.

Protocol ``x14-memory-dry/1`` (frozen corpus ``memory_corpus.json``): synthetic rows shaped
like layer A (``project_memory`` notes) and layer B (``case_memory`` denial cases). Each
query is a new denial stop (tool + denied paths).

Variants:

* ``production`` — what ships today. Cases: ``case_memory.retrieve_similar_cases`` (path
  Jaccard + tool bonus, outcome weight, one-week half-life), top ``MAX_RECALLED``. Notes:
  ``memory_loader`` does no retrieval; every reviewed note (cap 20 / 32 KB) rides in one
  advisory block, identical for every run of the project.
* ``plain_graph`` — the X-14 "plain graph" baseline. Nodes: cases, notes, tools, paths and
  their parent directories. Edges: case-path, case-tool, note-path (paths named in the note
  text), path-parent-dir. Score from the query's seed paths: 1.0 for a shared path node,
  0.5 via a shared parent dir, 0.25 via a shared grandparent dir (best per seed, summed),
  plus 0.25 for a shared tool node (cases only). Top 3 cases and top 3 notes, ties by id.
  No decay, no outcome weight, no embeddings: deliberately plain.

This is a baseline for the deferred X-12 candidate (DEFER stands); it adds nothing to any
prompt, writes nothing and is imported by no section. Usage (section4 venv):

    PYTHONPATH=.:../../section4/src ../../section4/.venv/bin/python \
        benchmark/memory_graph_baseline.py --out <report.json>
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from harness_orchestrator import case_memory
from harness_orchestrator.memory_loader import build_memory_block

HERE = Path(__file__).resolve().parent
K = 3
NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)
PATH_RE = re.compile(r"(?:/|\.|\b)[\w.\-]*(?:/[\w.\-]*)+|\b[\w\-]+\.[A-Za-z]{1,5}\b")


def note_paths(text: str) -> list[str]:
    """Path-like tokens named in a note (``a/b.py``, ``payroll/``, ``README.md``)."""
    out = []
    for m in PATH_RE.findall(text):
        p = m.rstrip(".;,").rstrip("/")
        if p and ("/" in m or "." in p):
            out.append(p)
    return sorted(set(out))


def ancestors(path: str) -> list[str]:
    """Parent, then grandparent directory (root excluded)."""
    parts = path.rstrip("/").split("/")
    dirs = ["/".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]
    return [d for d in dirs if d.strip("/")][:2]


def path_score(seed: str, candidate_paths: list[str]) -> float:
    best = 0.0
    seed_anc = ancestors(seed)
    for p in candidate_paths:
        if p == seed:
            best = max(best, 1.0)
            continue
        p_nodes = [p, *ancestors(p)]
        if seed_anc[:1] and seed_anc[0] in p_nodes:
            best = max(best, 0.5)
        elif seed_anc[1:2] and seed_anc[1] in p_nodes:
            best = max(best, 0.25)
    return best


def graph_rank(items: list[dict], q: dict, use_tool: bool) -> list[tuple[float, str]]:
    scored = []
    for it in items:
        s = sum(path_score(seed, it["paths"]) for seed in q["paths"])
        if use_tool and it.get("tool") == q["tool"]:
            s += 0.25
        if s > 0:
            scored.append((round(s, 4), it["id"]))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[:K]


def _cases(corpus: dict) -> list[dict[str, Any]]:
    out = []
    for c in corpus["cases"]:
        rec = case_memory.record_case(c["id"], "denials_repeated", c["tool"], c["paths"],
                                      c["outcome"], NOW - timedelta(hours=c["ageHours"]))
        out.append({**rec, "id": c["id"]})
    return out


def _tokens(text: str) -> int:
    return len(text) // 4  # same stub as x14_dry.py; not a tokenizer


def run(corpus: dict) -> dict[str, Any]:
    cases = _cases(corpus)
    notes = [{"id": n["id"], "text": n["text"], "paths": note_paths(n["text"])}
             for n in corpus["notes"]]
    note_text = {n["id"]: n["text"] for n in notes}
    all_notes_block = build_memory_block([
        {"text": n["text"], "source": "user", "createdBy": "u1", "reviewedBy": "u2",
         "createdAt": "2026-11-01 10:00:00+00:00"} for n in corpus["notes"]])

    variants: dict[str, list[dict]] = {"production": [], "plain_graph": []}
    for q in corpus["queries"]:
        prod_cases = [h["runId"] for h in case_memory.retrieve_similar_cases(
            cases, q["paths"], q["tool"], NOW, limit=case_memory.MAX_RECALLED)]
        variants["production"].append({
            "q": q["id"], "cases": prod_cases, "notes": [n["id"] for n in notes],
            "noteBlockTokens": _tokens(all_notes_block), "noteBlock": all_notes_block})
        g_cases = [i for _, i in graph_rank(cases, q, use_tool=True)]
        g_notes = [i for _, i in graph_rank(notes, q, use_tool=False)]
        g_block = "\n".join(note_text[i] for i in g_notes)
        variants["plain_graph"].append({
            "q": q["id"], "cases": g_cases, "notes": g_notes,
            "noteBlockTokens": _tokens(g_block), "noteBlock": g_block})

    expected = {q["id"]: q for q in corpus["queries"]}
    out = {}
    for name, rows in variants.items():
        with_cases = [r for r in rows if expected[r["q"]]["cases"]]
        with_notes = [r for r in rows if expected[r["q"]]["notes"]]
        returned = sum(len(r["cases"]) for r in rows)
        noise = sum(1 for r in rows for c in r["cases"] if c not in expected[r["q"]]["cases"])
        out[name] = {
            "caseHit@3": round(sum(any(c in r["cases"] for c in expected[r["q"]]["cases"])
                                   for r in with_cases) / len(with_cases), 4),
            "caseRecall@3": round(sum(len(set(r["cases"]) & set(expected[r["q"]]["cases"]))
                                      / len(expected[r["q"]]["cases"]) for r in with_cases)
                                  / len(with_cases), 4),
            "caseNoise@3": round(noise / returned, 4) if returned else 0.0,
            "noteRecall": round(sum(len(set(r["notes"]) & set(expected[r["q"]]["notes"]))
                                    / len(expected[r["q"]]["notes"]) for r in with_notes)
                                / len(with_notes), 4),
            "noteTokensMean": round(sum(r["noteBlockTokens"] for r in rows) / len(rows), 1),
            "distinctNoteBlocks": len({r["noteBlock"] for r in rows}),
            "perQuery": [{k: v for k, v in r.items() if k != "noteBlock"} for r in rows],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=False)
    args = ap.parse_args(argv)
    corpus = json.loads((HERE / "memory_corpus.json").read_text())
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                         text=True, check=False, cwd=HERE).stdout.strip() or "unknown"
    result = {"protocol": corpus["version"], "revision": rev, "queries": len(corpus["queries"]),
              "notes": len(corpus["notes"]), "cases": len(corpus["cases"]),
              "variants": run(corpus)}
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
    for name, v in result["variants"].items():
        print(f"{name:12s} caseHit@3={v['caseHit@3']:.2f} caseRecall@3={v['caseRecall@3']:.2f} "
              f"caseNoise@3={v['caseNoise@3']:.2f} noteRecall={v['noteRecall']:.2f} "
              f"noteTokens={v['noteTokensMean']:.0f} distinctNoteBlocks={v['distinctNoteBlocks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
