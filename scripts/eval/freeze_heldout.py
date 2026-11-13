#!/usr/bin/env python3
"""P-13 (W7): freeze the held-out split before any benchmark run.

The corpus already flags held-out tasks (``heldOut: true``, validated by
``run_eval.check_corpus``). What it lacked is a FROZEN manifest: the exact task
ids and a content hash, pinned going forward, so the split cannot be quietly
changed after seeing scores. This writes/verifies that manifest.

Caveat: these held-out tasks were already exercised in earlier baselines
(docs/briefs/evidence/eval/ and w33-rerun/, from 2026-10-28 on, W25/W33). The
freeze locks the split from now on; it does NOT show the tasks were never seen
or tuned against.

    python3 scripts/eval/freeze_heldout.py --write   # once, before any run
    python3 scripts/eval/freeze_heldout.py --check   # drift gate (exit 1 on change)

Preregistration rule: thresholds in docs/eval/PROTOCOL.md and this split may not
be loosened after results are seen. Re-freezing is an owner decision recorded in
the week ledger; earlier results stay bound to the old hash. No model, no Docker,
no network — this only reads the corpus and hashes bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "docs" / "eval" / "corpus.json"
MANIFEST = REPO / "docs" / "eval" / "heldout-frozen.json"

RULE = (
    "Preregistered: thresholds in docs/eval/PROTOCOL.md and this held-out split "
    "may not be loosened after results are seen. Held-out tasks are reported, "
    "never optimised against. Re-freezing requires an owner decision recorded in "
    "the week ledger; results from before a change stay bound to the old hash. "
    "This locks the split going forward only: the held-out tasks were already run "
    "in earlier baselines, so the freeze does not establish they were never seen "
    "or tuned against."
)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def task_hash(task: dict) -> str:
    return _sha(json.dumps(task, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def build_manifest(corpus_doc: dict, corpus_bytes: bytes) -> dict:
    held = [t for t in corpus_doc["tasks"] if t.get("heldOut") is True]
    return {
        "schema": 1,
        "kind": "heldout-frozen-manifest",
        "frozen": True,
        "frozenAt": datetime.now(UTC).isoformat(),
        "corpusRevision": corpus_doc.get("revision"),
        "corpusSha256": _sha(corpus_bytes),
        "rule": RULE,
        "tasks": [
            {"id": t["id"], "kind": t.get("kind"), "cost": t.get("cost"),
             "sha256": task_hash(t)}
            for t in sorted(held, key=lambda x: x["id"])
        ],
    }


def check_manifest(manifest: dict, corpus_doc: dict, corpus_bytes: bytes) -> list[str]:
    problems: list[str] = []
    if not manifest.get("frozen"):
        problems.append("manifest is not marked frozen")
    # The gate is the held-out split only (per-task hashes + ids). Whole-corpus
    # hash/revision changes are informational (corpus_changed), not blocking, so
    # adding/editing a non-held-out task does not trip the gate.
    held = {t["id"]: t for t in corpus_doc["tasks"] if t.get("heldOut") is True}
    frozen = {t["id"]: t for t in manifest.get("tasks", [])}
    for tid in sorted(set(frozen) - set(held)):
        problems.append(f"{tid}: frozen held-out task no longer held-out in corpus")
    for tid in sorted(set(held) - set(frozen)):
        problems.append(f"{tid}: new held-out task not in frozen manifest")
    for tid in sorted(set(held) & set(frozen)):
        if task_hash(held[tid]) != frozen[tid].get("sha256"):
            problems.append(f"{tid}: held-out task content changed since freeze")
    return problems


def corpus_changed(manifest: dict, corpus_bytes: bytes) -> bool:
    """Informational: corpus bytes differ from the freeze. Not a gate — only the
    held-out split blocks; unrelated corpus edits are allowed."""
    return manifest.get("corpusSha256") != _sha(corpus_bytes)


def main() -> int:
    ap = argparse.ArgumentParser(description="Freeze/verify the held-out benchmark split")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true", help="freeze the split (refuses to overwrite)")
    g.add_argument("--check", action="store_true", help="verify the frozen split against the corpus")
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--manifest", default=str(MANIFEST))
    a = ap.parse_args()

    corpus_path = Path(a.corpus)
    corpus_bytes = corpus_path.read_bytes()
    doc = json.loads(corpus_bytes)
    man_path = Path(a.manifest)

    if a.write:
        if man_path.exists():
            print(json.dumps({"ok": False, "error": "already frozen; use --check to verify. "
                              "Re-freeze only by owner decision (record it in the week ledger)."},
                             indent=2))
            return 1
        man = build_manifest(doc, corpus_bytes)
        man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "frozen": len(man["tasks"]),
                          "corpusSha256": man["corpusSha256"],
                          "manifest": str(man_path)}, indent=2))
        return 0

    if not man_path.exists():
        print(json.dumps({"ok": False, "error": "no frozen manifest; run --write before any run"},
                         indent=2))
        return 1
    man = json.loads(man_path.read_text(encoding="utf-8"))
    problems = check_manifest(man, doc, corpus_bytes)
    print(json.dumps({"ok": not problems, "heldOut": len(man.get("tasks", [])),
                      "corpusChanged": corpus_changed(man, corpus_bytes),
                      "problems": problems}, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())