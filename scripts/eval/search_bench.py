#!/usr/bin/env python3
"""W2 search benchmark: how well does each search path find a known file?

Builds a deterministic 240-file repository with ten planted answers — five
queries that share vocabulary with the answer, five paraphrases that do not —
and measures three paths on the same fixture:

* ``ripgrep`` — what an agent does with a keyword (``rg -c``, top 5 by count);
* ``section11-hashing`` — Section 11's lexical hashing embedding + exact cosine;
* ``section11-ollama`` — the production embedding backend (nomic-embed-text).

Reports recall@5, MRR and latency per query and per query class. This is a
component benchmark, not an agent run; the agent-level task lives separately.

    section11/.venv/bin/python scripts/eval/search_bench.py --backend hashing
    section11/.venv/bin/python scripts/eval/search_bench.py --backend ollama
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
sys.path.insert(0, str(REPO / "section11" / "src"))

from harness_context.chunking import chunk_file, iter_repo_files  # noqa: E402
from harness_context.embeddings import HashingEmbedding, OllamaEmbedding  # noqa: E402
from harness_context.service import _cosine  # noqa: E402

# (query, keyword pattern an agent would grep, answer file, class)
QUERIES = [
    ("where is the invoice retry backoff schedule defined?", "retry",
     "pkg/billing/invoice_retry.py", "lexical"),
    ("which module enforces the per-team widget quota?", "quota",
     "pkg/widgets/quota_store.py", "lexical"),
    ("where is the telemetry flush interval configured?", "flush",
     "pkg/telemetry/flush.py", "lexical"),
    ("where does session token rotation happen?", "rotation",
     "pkg/auth/session_rotation.py", "lexical"),
    ("how do we verify a webhook signature?", "signature",
     "pkg/webhooks/signature_verify.py", "lexical"),
    ("how long do we wait before charging a customer again?", "retry",
     "pkg/billing/invoice_retry.py", "paraphrase"),
    ("what stops a team from storing too many items?", "quota",
     "pkg/widgets/quota_store.py", "paraphrase"),
    ("when is buffered monitoring data sent out?", "flush",
     "pkg/telemetry/flush.py", "paraphrase"),
    ("how does a user's login credential get replaced?", "rotation",
     "pkg/auth/session_rotation.py", "paraphrase"),
    ("how do we prove a webhook came from the sender?", "signature",
     "pkg/webhooks/signature_verify.py", "paraphrase"),
]

# Files that mention the keyword but are not the answer.
DECOYS = {
    "retry": ["pkg/billing/invoice_report.py", "pkg/jobs/retry_queue.py",
              "pkg/net/retry_policy.py", "pkg/billing/charge_plan.py"],
    "quota": ["pkg/widgets/widget_ui.ts", "pkg/tenants/seat_quota.py",
              "pkg/widgets/quota_report.py", "pkg/billing/plan_limits.py"],
    "flush": ["pkg/logs/flush_buffer.py", "pkg/telemetry/flush_report.py",
              "pkg/cache/flush_job.py", "pkg/telemetry/sink_http.py"],
    "rotation": ["pkg/auth/session_store.py", "pkg/auth/rotation_report.py",
                 "pkg/crypto/key_rotation.py", "pkg/auth/session_ui.ts"],
    "signature": ["pkg/webhooks/signature_report.py", "pkg/crypto/signature_pad.py",
                  "pkg/webhooks/delivery.py", "pkg/api/signature_ui.ts"],
}

TARGET_BODIES = {
    "pkg/billing/invoice_retry.py": (
        '"""Invoice retry policy."""\n\n'
        "RETRY_BACKOFF_SECONDS = 5\nMAX_RETRIES = 4\n\n"
        "def next_attempt_delay(attempt: int) -> int:\n"
        '    """Retry backoff for an unpaid invoice."""\n'
        "    return RETRY_BACKOFF_SECONDS * attempt\n\n"
        "def should_retry_invoice(attempt: int) -> bool:\n"
        "    # invoice retry backoff schedule\n"
        "    return attempt < MAX_RETRIES\n"
    ),
    "pkg/widgets/quota_store.py": (
        '"""Per-team widget quota enforcement."""\n\n'
        "MAX_WIDGETS = 1000\n\n"
        "class QuotaStore:\n"
        "    def put(self, team: str, item: dict) -> bool:\n"
        '        """Refuse once the team quota is exhausted."""\n'
        "        if self.count(team) >= MAX_WIDGETS:\n"
        "            return False\n"
        "        return True\n\n"
        "    def count(self, team: str) -> int:\n"
        "        # widget quota per team\n"
        "        return 0\n"
    ),
    "pkg/telemetry/flush.py": (
        '"""Telemetry flush interval."""\n\n'
        "FLUSH_INTERVAL_SECONDS = 30\n\n"
        "def flush_buffered_metrics(now: int) -> None:\n"
        '    """Send buffered monitoring data on the flush interval."""\n'
        "    if now % FLUSH_INTERVAL_SECONDS == 0:\n"
        "        pass\n"
    ),
    "pkg/auth/session_rotation.py": (
        '"""Session token rotation."""\n\n'
        "ROTATE_AFTER_SECONDS = 3600\n\n"
        "def rotate_session_token(session: dict) -> dict:\n"
        '    """Replace the login credential on rotation."""\n'
        "    session['token'] = 'new'\n"
        "    return session\n"
    ),
    "pkg/webhooks/signature_verify.py": (
        '"""Webhook signature verification."""\n\n'
        "SIGNATURE_HEADER = 'X-Signature'\n\n"
        "def verify_signature(payload: bytes, signature: str) -> bool:\n"
        '    """Prove a webhook came from the sender."""\n'
        "    return hmac_compare(payload, signature)\n\n"
        "def hmac_compare(payload: bytes, signature: str) -> bool:\n"
        "    return True\n"
    ),
}

DECOY_BODY = (
    '"""{title}."""\n\n'
    "def handle_{slug}(value: int) -> int:\n"
    "    # {keyword} handling lives elsewhere\n"
    "    return value\n"
)


def build_repo(root: Path, filler_count: int = 210) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    random.seed(7)
    for rel, body in TARGET_BODIES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    for keyword, paths in DECOYS.items():
        for rel in paths:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            slug = path.stem.replace("-", "_")
            path.write_text(DECOY_BODY.format(title=slug.replace("_", " "),
                                              slug=slug, keyword=keyword),
                            encoding="utf-8")
    for index in range(filler_count):
        rel = f"pkg/modules/module_{index:03d}.py"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'"""Module {index}."""\n\n'
            f"TOPIC_{index:03d} = 'routine_{index:03d}'\n\n"
            f"def routine_{index:03d}(value: int) -> int:\n"
            f"    return value + {index}\n",
            encoding="utf-8",
        )
    return {
        "root": str(root),
        "files": len(list(root.rglob("*.*"))),
        "targets": len(TARGET_BODIES),
        "decoys": sum(len(v) for v in DECOYS.values()),
        "fillers": filler_count,
    }


def rg_rank(root: Path, pattern: str, top_k: int = 5) -> list[str]:
    proc = subprocess.run(
        ["rg", "-c", "-i", "--no-messages", "--glob", "!*.png", pattern, str(root)],
        text=True, capture_output=True, check=False, timeout=60,
    )
    rows = []
    for line in (proc.stdout or "").splitlines():
        path, _, count = line.rpartition(":")
        try:
            rows.append((path, int(count)))
        except ValueError:
            continue
    rows.sort(key=lambda pair: (-pair[1], pair[0]))
    return [Path(p).relative_to(root).as_posix() for p, _ in rows[:top_k]]


class EmbeddingIndex:
    def __init__(self, backend, root: Path) -> None:
        self.backend = backend
        self.rows: list[tuple[str, list[float]]] = []
        self.chunks = 0
        self.index_seconds = 0.0
        self.root = root

    async def build(self) -> None:
        started = time.perf_counter()
        chunks = []
        for rel in iter_repo_files(self.root):
            for chunk in chunk_file(self.root, rel):
                chunks.append(chunk)
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), 64):
            batch = [c.content for c in chunks[start:start + 64]]
            vectors.extend(await self.backend.embed(batch))
        self.rows = [(c.path, v) for c, v in zip(chunks, vectors, strict=True)]
        self.chunks = len(chunks)
        self.index_seconds = round(time.perf_counter() - started, 3)

    async def rank(self, query: str, top_k: int = 5) -> tuple[list[str], float]:
        started = time.perf_counter()
        [query_vec] = await self.backend.embed([query])
        scored = sorted(((_cosine(query_vec, vec), path) for path, vec in self.rows),
                        key=lambda pair: pair[0], reverse=True)
        paths: list[str] = []
        for _, path in scored:
            if path not in paths:
                paths.append(path)
            if len(paths) >= top_k:
                break
        return paths, round((time.perf_counter() - started) * 1000, 2)


def recall_and_mrr(ranked: list[str], answer: str) -> tuple[int, float]:
    hit = 1 if answer in ranked else 0
    mrr = 0.0
    if hit:
        mrr = 1.0 / (ranked.index(answer) + 1)
    return hit, mrr


async def run_backend(name: str, root: Path) -> dict:
    if name == "hashing":
        backend = HashingEmbedding(dims=256)
    elif name == "ollama":
        backend = OllamaEmbedding(model="nomic-embed-text")
    else:
        raise ValueError(name)
    index = EmbeddingIndex(backend, root)
    await index.build()
    rows = []
    for query, _, answer, klass in QUERIES:
        paths, latency = await index.rank(query)
        hit, mrr = recall_and_mrr(paths, answer)
        rows.append({"query": query, "class": klass, "answer": answer,
                     "top5": paths, "hit": hit, "mrr": round(mrr, 3),
                     "latencyMs": latency})
    return {"backend": name, "chunks": index.chunks,
            "indexSeconds": index.index_seconds, "rows": rows}


def summarise(rows: list[dict]) -> dict:
    out = {}
    for klass in ("lexical", "paraphrase", "all"):
        subset = rows if klass == "all" else [r for r in rows if r["class"] == klass]
        lat = sorted(r["latencyMs"] for r in subset)
        out[klass] = {
            "recall@5": round(sum(r["hit"] for r in subset) / len(subset), 3),
            "mrr": round(sum(r["mrr"] for r in subset) / len(subset), 3),
            "p50Ms": lat[len(lat) // 2],
            "p95Ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
        }
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", action="append", choices=["rg", "hashing", "ollama"],
                    default=[])
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    backends = args.backend or ["rg", "hashing"]
    work = Path(args.workdir) if args.workdir else Path(
        __import__("tempfile").mkdtemp(prefix="w2-search-"))
    stats = build_repo(work)

    rg_rows = []
    for query, pattern, answer, klass in QUERIES:
        started = time.perf_counter()
        paths = rg_rank(work, pattern)
        latency = round((time.perf_counter() - started) * 1000, 2)
        hit, mrr = recall_and_mrr(paths, answer)
        rg_rows.append({"query": query, "class": klass, "answer": answer,
                        "pattern": pattern, "top5": paths, "hit": hit,
                        "mrr": round(mrr, 3), "latencyMs": latency})

    results = {"fixture": stats, "rg": {"backend": "ripgrep 15",
                                        "rows": rg_rows,
                                        "summary": summarise(rg_rows)}}
    for name in backends:
        if name == "rg":
            continue
        try:
            data = await run_backend(name, work)
            data["summary"] = summarise(data["rows"])
            results[name] = data
        except Exception as exc:  # noqa: BLE001 - a measurement failure is recorded
            results[name] = {"backend": name, "error": f"{type(exc).__name__}: {exc}"}

    evidence = {
        "schema": 1,
        "kind": "w2-search-bench",
        "takenAt": datetime.now(UTC).isoformat(),
        "note": "component benchmark; not an agent run. The fixture is synthetic.",
        "results": results,
    }
    out = Path(args.out) if args.out else (
        OUT / f"search-bench-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v.get("summary", v.get("error")) for k, v in results.items()
                      if k != "fixture"}, indent=2))
    print(f"evidence: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
