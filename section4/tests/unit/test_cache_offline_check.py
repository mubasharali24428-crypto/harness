"""S-02 / S-01 offline cache check, run in the section4 CI leg.

Drives ``scripts/eval/cache_offline_check.py`` (a recorded five-attempt run replayed
through the real executor seam, then through the real bench_stats). No network, no
model, no Docker, no Postgres. Real provider cache-hit ratios are NOT measured here:
they need a live paid batch on the owner's Mac.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "eval" / "cache_offline_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("cache_offline_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(mod):
    return json.loads(mod.FIXTURE.read_text())


def test_offline_cache_check_passes_and_writes_a_report(tmp_path, capsys):
    mod = _load()
    out = tmp_path / "report.json"
    assert mod.main(["--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["verdict"] == "PASS"
    assert set(report["checks"]) == {
        "C1_same_digest_every_attempt",
        "C2_identical_prefix_bytes_no_mid_run_edits",
        "C3_simulated_prefix_cache_covers_stable_part",
        "C4_bench_stats_cache_split",
        "C5_control_without_freeze_is_detected",
    }
    assert all(c["pass"] for c in report["checks"].values())
    assert "owner's Mac" in report["scope"]  # never claims a live cache number


def test_frozen_replay_sends_identical_prefix_bytes_every_attempt():
    mod = _load()
    fx = _fixture(mod)
    rep = mod.replay(fx, freeze=True)
    digests = {r["promptPrefix"]["sha256"] for r in rep["results"]}
    assert len(rep["goals"]) == len(fx["attempts"]) and len(digests) == 1
    n = rep["results"][0]["promptPrefix"]["bytes"]
    heads = {g.encode()[:n] for g in rep["goals"]}
    assert len(heads) == 1
    assert len({g for g in rep["goals"]}) > 1  # the volatile tail did change


def test_the_check_fails_without_the_freeze():
    mod = _load()
    fx = _fixture(mod)
    checks = mod.evaluate(fx, mod.replay(fx, freeze=False))["checks"]
    assert not checks["C1_same_digest_every_attempt"]["pass"]
    assert not checks["C3_simulated_prefix_cache_covers_stable_part"]["pass"]
    assert checks["C4_bench_stats_cache_split"]["distinctPrefixDigests"] > 1


def test_a_wrong_expected_cache_split_is_caught(tmp_path):
    mod = _load()
    fx = copy.deepcopy(_fixture(mod))
    fx["expected"]["pooledCacheReadRatio"] = 0.7  # what a formula bug might print
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(fx))
    assert mod.main(["--fixture", str(bad)]) == 1
