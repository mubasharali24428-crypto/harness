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


# ------------------------------------------------------- the HTML (public) form


def _html_sections(doc: str) -> dict[str, str]:
    """Split the page on its section markers (<h2> headers and the dry banner)
    so an assertion names one section and cannot be satisfied by another."""
    import re

    parts = re.split(r'<h2>|<div class="dry">', doc)
    sections: dict[str, str] = {"head": parts[0]}
    for chunk in parts[1:]:
        if "</h2>" in chunk:
            name, body = chunk.split("</h2>", 1)
        else:  # the dry banner ends at the </strong>…</div> of its own div
            name, body = chunk.split("</div>", 1)[0], chunk.split("</div>", 1)[1]
        sections[name.strip()] = body
    return sections


def test_html_dry_cells_never_in_the_ranked_table():
    b = lb.board(_five(model="dry-fake-model", mode="dry") + _five(task="t2", model="other"))
    doc = lb.render_html(b, label="x", manifest_sha=None)
    sections = _html_sections(doc)
    assert "dry-fake-model" not in sections["Ranked (claimable cells only — one row per task × model)"]
    assert "other" in sections["Ranked (claimable cells only — one row per task × model)"]
    assert lb.DRY_NOTE in doc and "excluded from ranking" in doc


def test_html_unknown_cost_is_unknown_never_zero():
    attempts = _five()
    for a in attempts[:-1]:
        a["costUsd"] = None
    doc = lb.render_html(lb.board(attempts), label="x", manifest_sha=None)
    assert "unknown" in doc and "$0.00" not in doc


def test_html_no_claimable_banner_says_the_bar():
    doc = lb.render_html(lb.board(_five()[:3]), label="x", manifest_sha=None)
    assert "No claimable rows yet" in doc and "insufficient data" in doc


def test_html_is_deterministic_and_escapes_model_names():
    b = lb.board(_five(model="alpha<script>alert(1)</script>"))
    doc1 = lb.render_html(b, label="x", manifest_sha=None, generated="2026-11-21T00:00:00+00:00")
    doc2 = lb.render_html(b, label="x", manifest_sha=None, generated="2026-11-21T00:00:00+00:00")
    assert doc1 == doc2, "same board + stamp -> byte-identical page"
    assert "<script>" not in doc1, "a model name is data, not markup"
    assert "&lt;script&gt;" in doc1, "escaped so a hostile model name cannot inject"


def test_md_and_html_come_from_the_same_board():
    """The no-second-arithmetic law, asserted across formats: both renderers
    must rank the same set of cells in the same order."""
    attempts = _five(task="t1", model="alpha") + _five(task="t2", model="beta", cost=0.02) \
        + [_attempt(task="t3", model="gamma", trial=i) for i in range(1, 4)]
    b = lb.board(attempts)
    md = lb.render_markdown(b, label="x", manifest_sha=None, generated="s")
    html = lb.render_html(b, label="x", manifest_sha=None, generated="s")
    md_rows = [ln for ln in md.splitlines() if ln.startswith("| ") and ln.split("|")[1].strip().isdigit()]
    assert [r.split("|")[2].strip() for r in md_rows] == ["alpha", "beta"], "md order"
    ranked_html = _html_sections(html)["Ranked (claimable cells only — one row per task × model)"]
    assert ranked_html.index("alpha") < ranked_html.index("beta") and "gamma" not in ranked_html, (
        "html ranks the same cells, same order; gamma (3 trials) stays below the bar")


def test_html_is_self_contained():
    doc = lb.render_html(lb.board(_five()), label="x", manifest_sha=None)
    assert "<script" not in doc and "http://" not in doc and "https://" not in doc, (
        "no JS, no external assets — droppable on any static host")
    assert doc.startswith("<!DOCTYPE html>") and doc.rstrip().endswith("</html>")
