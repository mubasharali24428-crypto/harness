"""Fails when any recorded openapi.json drifts from the routes its app mounts (runs spec_drift.py --check)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "section4/.venv/bin/python"  # the interpreter that can import every service


@pytest.mark.skipif(not PY.exists(), reason="section4 venv missing")
def test_specs_match_routes():
    r = subprocess.run([str(PY), str(ROOT / "scripts/spec_drift.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
