"""P-13 (W7) offline tests: no network, no Docker, no spend.

Covers p50/p95 on a fixed sample, the N<5 refusal, failure/denial retention, the
dry label, and the frozen held-out manifest drift gate. Run from scripts/eval:

    ../../section1/.venv/bin/python -m pytest test_w7_bench.py -q -rxXs
"""
from __future__ import annotations

import json
from pathlib import Path

import bench_stats
import bench_trials
import freeze_heldout


def _attempt(task="t", model="m", mode="live", trial=1, verdict="PASS", duration=10.0,
             cost=0.01, first=1.0, tools=5, interventions=0):
    return {"kind": "w7-bench-attempt", "taskId": task, "model": model, "mode": mode,
            "trial": trial, "verdict": verdict, "durationSec": duration,
            "costUsd": cost, "firstOutputS": first, "toolCalls": tools,
            "interventions": interventions, "driverVerdict": verdict, "failedChecks": []}


def test_p50_p95_fixed_sample():
    attempts = [_attempt(duration=d, trial=i + 1) for i, d in enumerate([10, 20, 30, 40, 100])]
    cell = bench_stats.cell_stats("t", "m", "live", attempts)
    # nearest-rank: p50 -> ceil(0.5*5)=3rd=30 ; p95 -> ceil(0.95*5)=5th=100
    assert cell["wallSeconds"]["p50"] == 30
    assert cell["wallSeconds"]["p95"] == 100
    assert cell["n"] == 5


def test_n_below_5_refuses_rate_and_comparison():
    attempts = [_attempt(trial=i + 1) for i in range(4)]
    cell = bench_stats.cell_stats("t", "m", "live", attempts)
    assert cell["successRate"] == "insufficient data; no claim"
    assert cell["comparison"] == "insufficient data; no claim"
    assert cell["claimable"] is False
    assert cell["n"] == 4


def test_n5_rate_has_denominator_and_keeps_failure():
    attempts = [_attempt(trial=i + 1, verdict="PASS" if i < 4 else "FAIL") for i in range(5)]
    cell = bench_stats.cell_stats("t", "m", "live", attempts)
    assert cell["successRate"] == {"passes": 4, "n": 5, "rate": 0.8}
    assert cell["claimable"] is True
    assert len(cell["failures"]) == 1  # the failure is retained in the cell


def test_dry_batch_keeps_failures_and_labels_dry(tmp_path):
    tasks = [{"id": "stale-edit", "heldOut": True, "budget": {"timeoutSeconds": 300}}]
    summary = bench_trials.run_batch(tasks, 5, bench_trials.DRY_MODEL, True, tmp_path)
    files = [json.loads(Path(f).read_text()) for f in summary["attemptFiles"]]
    assert len(files) == 5
    assert all(a["mode"] == "dry" and a["simulation"] is True for a in files)
    assert all(a["model"] == "dry-fake-model" for a in files)
    assert all(a["costUsd"] == 0.0 and "dry" in a["costSource"] for a in files)
    verdicts = {a["verdict"] for a in files}
    assert "FAIL" in verdicts and "TIMEOUT" in verdicts  # failures retained
    assert any(a["driverVerdict"] == "DENIED" for a in files)  # denial in the default 5
    assert summary["mode"] == "dry" and summary["claimable"] is False
    assert "not a capability claim" in summary["note"]
    assert all(Path(f).is_file() for f in summary["attemptFiles"])  # one raw JSON per attempt


def test_dry_denial_retained(tmp_path):
    tasks = [{"id": "x", "heldOut": False, "budget": {}}]
    summary = bench_trials.run_batch(tasks, 5, bench_trials.DRY_MODEL, True, tmp_path)
    files = [json.loads(Path(f).read_text()) for f in summary["attemptFiles"]]
    denied = [a for a in files if a["driverVerdict"] == "DENIED"]
    assert len(denied) == 1  # trial 4 in the dry cycle (denial within the default 5)
    assert denied[0]["verdict"] == "FAIL"


def test_dry_stats_never_claimable(tmp_path):
    tasks = [{"id": "t", "heldOut": True, "budget": {}}]
    bench_trials.run_batch(tasks, 5, bench_trials.DRY_MODEL, True, tmp_path)
    cells = bench_stats.aggregate(bench_stats.load_attempts(tmp_path))
    assert len(cells) == 1
    assert cells[0]["mode"] == "dry"
    assert cells[0]["claimable"] is False  # dry never claimable even at n>=5
    assert cells[0]["successRate"] == "dry: simulated, no rate"


def test_frozen_manifest_check_and_drift():
    corpus = {"revision": "test", "tasks": [
        {"id": "a", "heldOut": True, "kind": "agent", "cost": "live"},
        {"id": "b", "heldOut": False, "kind": "infra", "cost": "none"},
    ]}
    raw = json.dumps(corpus).encode()
    man = freeze_heldout.build_manifest(corpus, raw)
    assert man["frozen"] is True
    assert [t["id"] for t in man["tasks"]] == ["a"]
    assert freeze_heldout.check_manifest(man, corpus, raw) == []
    # tamper: change the held-out task content -> drift detected
    corpus2 = json.loads(raw)
    corpus2["tasks"][0]["cost"] = "none"
    raw2 = json.dumps(corpus2).encode()
    problems = freeze_heldout.check_manifest(man, corpus2, raw2)
    assert any("a" in p for p in problems)


def test_non_heldout_corpus_edit_does_not_block():
    corpus = {"revision": "test", "tasks": [
        {"id": "a", "heldOut": True, "kind": "agent", "cost": "live"},
    ]}
    raw = json.dumps(corpus).encode()
    man = freeze_heldout.build_manifest(corpus, raw)
    # add a NON-held-out task: corpus bytes change, but the held-out split is intact
    corpus2 = {"revision": "test", "tasks": [
        {"id": "a", "heldOut": True, "kind": "agent", "cost": "live"},
        {"id": "newcomer", "heldOut": False, "kind": "infra", "cost": "none"},
    ]}
    raw2 = json.dumps(corpus2).encode()
    assert freeze_heldout.check_manifest(man, corpus2, raw2) == []
    assert freeze_heldout.corpus_changed(man, raw2) is True

def test_driver_that_never_produced_a_verdict_is_error_not_fail():
    assert bench_trials.classify_verdict({"verdict": "FAIL", "driverVerdict": None, "failedChecks": []}) == "ERROR"
    assert bench_trials.classify_verdict({"verdict": "FAIL", "driverVerdict": "FAIL", "failedChecks": []}) == "FAIL"
    assert bench_trials.classify_verdict({"verdict": "FAIL", "driverVerdict": None, "failedChecks": ["x"]}) == "FAIL"
    assert bench_trials.classify_verdict({"verdict": "PASS", "driverVerdict": None, "failedChecks": []}) == "PASS"
    assert bench_trials.classify_verdict({"verdict": "TIMEOUT", "driverVerdict": None, "failedChecks": []}) == "TIMEOUT"


def test_errored_trials_never_become_a_success_rate():
    attempts = [_attempt(trial=i + 1, verdict="ERROR") for i in range(5)]
    cell = bench_stats.cell_stats("t", "m", "live", attempts)
    assert cell["errors"] == 5 and cell["validTrials"] == 0
    assert cell["claimable"] is False
    assert "no claim" in cell["successRate"] and "environment" in cell["successRate"]
    assert len(cell["failures"]) == 5  # retained, not dropped
    # one error among enough valid trials: still not claimable until the error is explained
    mixed = [_attempt(trial=i + 1) for i in range(5)] + [_attempt(trial=6, verdict="ERROR")]
    cell = bench_stats.cell_stats("t", "m", "live", mixed)
    assert cell["claimable"] is False and "no claim" in cell["successRate"]


def test_ambiguous_stack_is_refused(monkeypatch):
    class R:
        stdout = "harness-demo harness-demo harness-rc"

    monkeypatch.delenv("E2E_COMPOSE_PROJECT", raising=False)
    monkeypatch.setattr(bench_trials.subprocess, "run", lambda *a, **k: R())
    msg = bench_trials.ambiguous_stack()
    assert msg and "harness-demo" in msg and "E2E_COMPOSE_PROJECT" in msg
    monkeypatch.setenv("E2E_COMPOSE_PROJECT", "harness-rc")
    assert bench_trials.ambiguous_stack() is None
    monkeypatch.delenv("E2E_COMPOSE_PROJECT")
    R.stdout = "harness-rc harness-rc"
    assert bench_trials.ambiguous_stack() is None
