"""X-07 plain sentence + X-21 severity grid: golden fixtures (read, write, rm, curl, .env)."""

import pytest

from harness_orchestrator.approval_copy import grid, plain

GOLDEN = [
    ("read", ["src/a.py"], "The agent wants to read 1 file (src/a.py).",
     [("file_read", "low")], "low"),
    ("modify", ["src/a.py", "src/b.py", "src/c.py"],
     "The agent wants to change 3 files (src/a.py, src/b.py and 1 more).",
     [("file_write", "moderate")], "moderate"),
    ("delete", ["build/out.bin"], "The agent wants to delete 1 file (build/out.bin).",
     [("file_write", "elevated")], "elevated"),
    ("network", ["scripts/upload.sh"],
     "The agent wants to send data over the network involving 1 file (scripts/upload.sh).",
     [("network", "elevated")], "elevated"),
    ("modify", ["deploy/.env"],
     "The agent wants to change 1 file (deploy/.env). This touches a secrets or credentials file.",
     [("file_write", "moderate"), ("credentials", "critical")], "critical"),
]


@pytest.mark.parametrize(("action", "paths", "sentence", "cells", "dominant"), GOLDEN)
def test_golden(action, paths, sentence, cells, dominant):
    assert plain(action, paths) == sentence
    g = grid(action, paths)
    assert [(c["family"], c["band"]) for c in g["cells"]] == cells
    assert g["dominantBand"] == dominant
    assert all(c["label"] for c in g["cells"])  # never colour alone: every cell has text


def test_config_file_adds_config_row():
    g = grid("modify", [".github/workflows/ci.yml"])
    assert ("config", "elevated") in [(c["family"], c["band"]) for c in g["cells"]]


def test_unknown_is_unknown_never_safe():
    assert "unknown" in plain("teleport", ["x"])
    assert grid("teleport", ["x"]) == {"cells": [], "dominantBand": "unknown"}
    assert "unknown files" in plain("modify", [])
