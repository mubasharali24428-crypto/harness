"""P3.1: the recorded orchestrator specs and UI calls must match the router."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("spec_drift", ROOT / "scripts" / "spec_drift.py")
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)


def test_repo_specs_and_ui_calls_match_router(capsys):
    assert drift.main() == 0, capsys.readouterr().out


def test_drift_is_detected(tmp_path, monkeypatch, capsys):
    stale = json.loads(drift.FRAGMENT.read_text())
    del stale["paths"]["/api/v1/orchestrator/runs/{runId}/stop"]
    f = tmp_path / "openapi.json"
    f.write_text(json.dumps(stale))
    monkeypatch.setattr(drift, "FRAGMENT", f)
    monkeypatch.setattr(drift.sys, "argv", ["spec_drift.py"])
    assert drift.main() == 1
    assert "missing" in capsys.readouterr().out


def test_new_unspecced_ui_call_fails(monkeypatch, capsys):
    monkeypatch.setattr(drift.sys, "argv", ["spec_drift.py"])
    monkeypatch.setattr(drift, "other_ui_calls", lambda: {"/api/v1/brand/new/thing"})
    assert drift.main() == 1
    assert "(new gap): /api/v1/brand/new/thing" in capsys.readouterr().out


def test_known_gap_that_is_now_served_must_be_removed(monkeypatch, capsys):
    monkeypatch.setattr(drift.sys, "argv", ["spec_drift.py"])
    monkeypatch.setattr(drift, "KNOWN_UNSPECCED", {"/api/v1/billing/state"})
    assert drift.main() == 1
    assert "remove it: /api/v1/billing/state" in capsys.readouterr().out


def test_known_unspecced_is_empty():
    assert drift.KNOWN_UNSPECCED == set()
