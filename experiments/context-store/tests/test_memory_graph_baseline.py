"""X-14 memory baseline (x14-memory-dry/1): deterministic, read-only, no prompt bytes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "benchmark"))
sys.path.insert(0, str(HERE.parents[1] / "section4" / "src"))
pytest.importorskip("sqlalchemy")  # case_memory imports it; run with the section4 venv

import memory_graph_baseline as mg

CORPUS = json.loads((HERE / "benchmark" / "memory_corpus.json").read_text())


def test_note_paths_and_ancestors():
    assert mg.note_paths("never edit /etc/nginx/nginx.conf directly.") == ["/etc/nginx/nginx.conf"]
    assert mg.note_paths("payroll/ is forbidden") == ["payroll"]
    assert mg.ancestors("/etc/nginx/nginx.conf") == ["/etc/nginx", "/etc"]
    assert mg.ancestors("README.md") == []


def test_path_score_exact_then_parent_then_grandparent():
    assert mg.path_score("a/b/c.py", ["a/b/c.py"]) == 1.0
    assert mg.path_score("a/b/c.py", ["a/b/d.py"]) == 0.5
    assert mg.path_score("a/b/c.py", ["a/x/d.py"]) == 0.25
    assert mg.path_score("a/b/c.py", ["z/y.py"]) == 0.0


def test_run_is_deterministic_and_bounded():
    first, second = mg.run(CORPUS), mg.run(CORPUS)
    assert first == second
    for variant in first.values():
        for row in variant["perQuery"]:
            assert len(row["cases"]) <= 3
    # production notes = one advisory block for every run (prefix-cache friendly)
    assert first["production"]["distinctNoteBlocks"] == 1


def test_report_writes(tmp_path):
    out = tmp_path / "r.json"
    assert mg.main(["--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["protocol"] == "x14-memory-dry/1"
    assert set(report["variants"]) == {"production", "plain_graph"}
