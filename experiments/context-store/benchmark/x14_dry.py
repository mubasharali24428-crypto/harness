"""X-14 dry benchmark: flat chunks (section11 style) vs plain graph edges vs simplicial faces.

Same embedder for every variant (section11 HashingEmbedding, 256-d), same frozen corpus, no
paid models, no network. Writes one JSON artifact with the git revision.

    cd experiments/context-store && PYTHONPATH=.:../../section11/src \
        ../../section11/.venv/bin/python benchmark/x14_dry.py --out <file.json>

Metrics per variant:
  hit@1 / hit@3   expected fact's unit is among the top-k returned units
  whole@3         the top-3 units together carry all three entities AND the provenance file
                  of the expected fact (the "three orphaned edges" question)
  tokens@3        len(json of top-3 units) / 4 — a stub estimate, not a tokenizer
  latency_us      mean retrieval time per query (median of 5 repeats), Python, this machine
Cache-read rates are N/A: no provider is called.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import time
from pathlib import Path

from harness_context.embeddings import HashingEmbedding

from simplicial import SimplicialStore, cosine, make_face, poincare_rescore

HERE = Path(__file__).resolve().parent
K = 3


def _text(f: dict) -> str:
    return " ".join([f["fact"], *f["entities"]])


async def build(corpus: dict) -> dict:
    emb = HashingEmbedding(256)
    facts = corpus["facts"]
    vecs = await emb.embed([_text(f) for f in facts])
    # flat chunks: one unit per fact (what section11 would store as a chunk)
    flat = [{"fact_id": f["id"], "entities": f["entities"], "fact": f["fact"], "v": v}
            for f, v in zip(facts, vecs, strict=True)]
    # plain graph: each fact dissolves into three pairwise edges carrying the fact text
    edges = []
    for f in facts:
        a, b, c = f["entities"]
        for pair in ((a, b), (a, c), (b, c)):
            edges.append({"fact_id": f["id"], "entities": list(pair), "fact": f["fact"]})
    edge_vecs = await emb.embed([" ".join([e["fact"], *e["entities"]]) for e in edges])
    for e, v in zip(edges, edge_vecs, strict=True):
        e["v"] = v
    prov = {"reviewer": "owner", "timestamp": "frozen", "revision": corpus["version"]}
    stores = {"simplicial_flat": SimplicialStore(max_faces=len(facts)),
              "simplicial_hyperbolic": SimplicialStore(rescore=poincare_rescore, max_faces=len(facts))}
    face_fact = {}
    for f, v in zip(facts, vecs, strict=True):
        for s in stores.values():
            face = make_face(*f["entities"], f["fact"], prov, v)
            face_fact[face.digest] = f["id"]
            s.add(face)
    return {"emb": emb, "flat": flat, "edges": edges, "stores": stores, "face_fact": face_fact}


def _top(units: list[dict], q) -> list[dict]:
    return sorted(units, key=lambda u: -cosine(q, u["v"]))[:K]


def _whole(units: list[dict], fact: dict) -> bool:
    ents = {e for u in units if u["fact_id"] == fact["id"] for e in u["entities"]}
    return set(fact["entities"]) <= ents  # entity set includes the provenance file


async def run(corpus: dict) -> dict:
    w = await build(corpus)
    facts = {f["id"]: f for f in corpus["facts"]}
    qvecs = await w["emb"].embed([q["q"] for q in corpus["queries"]])

    def retrieve(variant: str, q) -> list[dict]:
        if variant == "flat_chunks":
            return _top(w["flat"], q)
        if variant == "plain_graph_edges":
            return _top(w["edges"], q)
        return [{"fact_id": w["face_fact"][f.digest], "entities": list(f.entities), "fact": f.fact}
                for f in w["stores"][variant].retrieve(q, k=K)]

    out = {}
    for variant in ("flat_chunks", "plain_graph_edges", "simplicial_flat", "simplicial_hyperbolic"):
        rows = []
        for qd, qv in zip(corpus["queries"], qvecs, strict=True):
            times = []
            for _ in range(5):
                t0 = time.perf_counter()
                top = retrieve(variant, qv)
                times.append((time.perf_counter() - t0) * 1e6)
            ids = [u["fact_id"] for u in top]
            slim = [{k: u[k] for k in ("entities", "fact")} for u in top]
            rows.append({"q": qd["q"], "expect": qd["expect"], "top": ids,
                         "hit1": ids[:1] == [qd["expect"]], "hit3": qd["expect"] in ids,
                         "whole3": _whole(top, facts[qd["expect"]]),
                         "tokens3": len(json.dumps(slim)) // 4,
                         "latency_us": statistics.median(times)})
        n = len(rows)
        out[variant] = {
            "hit@1": sum(r["hit1"] for r in rows) / n, "hit@3": sum(r["hit3"] for r in rows) / n,
            "whole@3": sum(r["whole3"] for r in rows) / n,
            "tokens@3_mean": statistics.mean(r["tokens3"] for r in rows),
            "latency_us_mean": round(statistics.mean(r["latency_us"] for r in rows), 1),
            "per_query": rows,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    corpus = json.loads((HERE / "corpus.json").read_text())
    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True,
                         cwd=HERE).stdout.strip()
    result = {"protocol": corpus["version"], "revision": rev, "embedder": "HashingEmbedding(256)",
              "queries": len(corpus["queries"]), "facts": len(corpus["facts"]),
              "variants": asyncio.run(run(corpus))}
    Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
    for name, v in result["variants"].items():
        print(f"{name:24s} hit@1={v['hit@1']:.2f} hit@3={v['hit@3']:.2f} "
              f"whole@3={v['whole@3']:.2f} tokens@3={v['tokens@3_mean']:.0f} "
              f"latency_us={v['latency_us_mean']}")


if __name__ == "__main__":
    main()
