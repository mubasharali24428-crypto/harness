"""I-04 (P-13/P-18) leaderboard tests: offline, no network, no Docker, no spend.

The leaderboard is a PRESENTATION of bench_stats.aggregate — so these tests pin
the presentation laws, not new arithmetic:

* dry cells are rendered but NEVER appear in the ranked table;
* a live cell below MIN_N=5 (or with environment errors) prints the
  "insufficient data" refusal and is not ranked;
* unknown cost renders as unknown / `~`, never $0;
* ranking is deterministic (same attempts -> same bytes, stated order);
* failures appear as counts, not disappear;
* the no-claimable case withholds rankings rather than printing rates.

Run from scripts/eval:

    ../../section1/.venv/bin/python -m pytest test_bench_leaderboard.py -q -rxXs
"""
from __future__ import annotations

import json

import bench_leaderboard as lb


def _attempt(task="t1", model="m", mode="live", trial=1, verdict="PASS", duration=10.0,
             cost=0.01, first=1.0, tools=5, interventions=0):
    return {"kind": "w7-bench-attempt", "taskId": task, "model": model, "mode": mode,
            "trial": trial, "verdict": verdict, "durationSec": duration,
            "costUsd": cost, "firstOutputS": first, "toolCalls": tools,
            "interventions": interventions, "driverVerdict": verdict, "failedChecks": []}


def _five(task="t1", model="m", verdict="PASS", cost=0.01, mode="live"):
    return [_attempt(task=task, model=model, verdict=verdict, cost=cost, mode=mode,
                     trial=i, duration=10.0 + i) for i in range(1, 6)]


def test_dry_cells_render_but_never_rank():
    doc = lb.render(_five(model="dry-fake-model", mode="dry") + _five(task="t2", model="other"),
                    label="x", manifest_sha=None)
    assert lb.DRY_NOTE in doc, "dry cells must be labelled"
    ranked = doc.split("## Ranked")[1].split("##")[0]
    assert "dry-fake-model" not in ranked, "the dry model must not appear in the ranked table"
    assert "other" in ranked, "the live model with 5 PASS trials ranks"


def test_below_min_n_is_refused_not_rate():
    attempts = _five()[:3]  # 3 trials: below the bar
    doc = lb.render(attempts, label="x", manifest_sha=None)
    assert "No claimable rows yet" in doc, "3 trials must not produce a ranked rate"
    assert "insufficient data" in doc


def test_environment_errors_never_rank():
    attempts = [_attempt(trial=i, verdict="ERROR") for i in range(1, 6)]
    doc = lb.render(attempts, label="x", manifest_sha=None)
    assert "No claimable rows yet" in doc
    assert "errored before a verdict" in doc, "the reason is shown, not hidden"


def test_unknown_cost_is_unknown_never_zero():
    attempts = _five()
    for a in attempts[:-1]:
        a["costUsd"] = None
    doc = lb.render(attempts, label="x", manifest_sha=None)
    assert "unknown" in doc
    assert "$0.00" not in doc, "unknown cost must not render as $0"


def test_partial_unknown_cost_is_marked_with_tilde():
    attempts = _five()
    attempts[-1]["costUsd"] = None
    doc = lb.render(attempts, label="x", manifest_sha=None)
    assert "~$" in doc and "1 unknown" in doc


def test_failures_are_counted_not_hidden():
    attempts = _five(verdict="FAIL")
    doc = lb.render(attempts, label="x", manifest_sha=None)
    # 5 FAIL trials: not claimable (0 passes -> rate row shows 0/5)
    assert "0/5" in doc
    assert doc.count("failures") >= 1


def test_ranking_is_deterministic_and_stated():
    a = _five(task="t1", model="alpha") + _five(task="t2", model="beta", cost=0.02)
    doc1 = lb.render(a, label="x", manifest_sha=None, generated="2026-11-21T00:00:00+00:00")
    doc2 = lb.render(list(reversed(a)), label="x", manifest_sha=None,
                     generated="2026-11-21T00:00:00+00:00")
    assert doc1 == doc2, "same attempts in any order -> identical document"


def test_manifest_absence_is_said_out_loud():
    doc = lb.render(_five(), label="x", manifest_sha=None)
    assert "NOT FOUND" in doc and "no run here can be claimable" in doc


def test_load_attempts_reads_raw_and_skips_batch_index_and_names_junk(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_attempt(trial=1)))
    # A batch file is an INDEX over attempt files: expanding it would
    # double-count, so it must be skipped and named.
    (tmp_path / "batch.json").write_text(json.dumps(
        {"schema": 1, "kind": "w7-bench-batch", "attemptFiles": ["a.json"],
         "attempts": [_attempt(trial=2)]}))
    (tmp_path / "junk.json").write_text(json.dumps({"hello": 1}))
    attempts, skipped = lb.load_attempts(tmp_path)
    assert len(attempts) == 1 and sorted(skipped) == ["batch.json", "junk.json"]
