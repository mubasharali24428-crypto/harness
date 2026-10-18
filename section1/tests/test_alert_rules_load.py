"""deploy/observability/alerts.yml must load: structure, wiring and runbook links.

Structural check only (no PromQL parser). The real parser is
`promtool check rules deploy/observability/alerts.yml`; this test runs it too
when a local `promtool` binary exists (docs/runbooks/alerting.md section 1 has the
docker one-liner).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
OBS = REPO / "deploy" / "observability"
ALERTS = OBS / "alerts.yml"
PROM = OBS / "prometheus.yml"
ALERT_DOC = REPO / "docs" / "runbooks" / "alerting.md"
DURATION = re.compile(r"^\d+(ms|s|m|h|d|w|y)$")
SEVERITIES = {"warning", "page"}
LANE_A_COUNTERS = {
    "harness_approval_denied_total",
    "harness_run_cancel_total",
    "harness_isolation_violation_total",
    "harness_event_delivery_total",
}


def _rules() -> list[tuple[str, dict]]:
    doc = yaml.safe_load(ALERTS.read_text())
    return [(g["name"], r) for g in doc["groups"] for r in g["rules"]]


def _anchors(md: Path) -> set[str]:
    """GitHub-style heading slugs (lowercase, drop punctuation, spaces to hyphens)."""
    slugs = set()
    for line in md.read_text().splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if m:
            slug = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
            slugs.add(slug)
    return slugs


def test_file_is_valid_yaml_with_groups():
    doc = yaml.safe_load(ALERTS.read_text())
    assert set(doc) == {"groups"}
    assert doc["groups"], "no groups"
    for g in doc["groups"]:
        assert g.get("name") and g.get("rules"), g


def test_every_rule_has_required_keys_and_labels():
    names = []
    for group, r in _rules():
        assert set(r) <= {"alert", "expr", "for", "labels", "annotations"}, (group, r)
        assert r.get("alert") and r.get("expr"), (group, r)
        names.append(r["alert"])
        assert r["labels"]["severity"] in SEVERITIES, r["alert"]
        for key in ("summary", "description", "runbook"):
            assert r["annotations"].get(key), (r["alert"], key)
        if "for" in r:
            assert DURATION.match(str(r["for"])), (r["alert"], r["for"])
    assert len(names) == len(set(names)), "duplicate alert names"


def test_expr_is_balanced_and_durations_valid():
    for _, r in _rules():
        expr = r["expr"].strip()
        for open_, close in ("()", "{}", "[]"):
            assert expr.count(open_) == expr.count(close), (r["alert"], open_)
        assert expr.count('"') % 2 == 0, r["alert"]
        for sel in re.findall(r"\[([^\]]*)\]", expr):
            assert DURATION.match(sel), (r["alert"], sel)
        for dur in re.findall(r"offset\s+(\d+[a-z]+)", expr):
            assert DURATION.match(dur), (r["alert"], dur)


def test_every_runbook_annotation_points_at_an_existing_anchor():
    for _, r in _rules():
        path, _, anchor = r["annotations"]["runbook"].partition("#")
        target = REPO / path
        assert target.is_file(), (r["alert"], path)
        if anchor:
            assert anchor in _anchors(target), (r["alert"], anchor)


def test_prometheus_config_loads_the_rules_file():
    cfg = yaml.safe_load(PROM.read_text())
    # compose bind-mounts ./observability at /etc/prometheus
    assert cfg["rule_files"] == ["/etc/prometheus/alerts.yml"]
    compose = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())
    volumes = " ".join(compose["services"]["prometheus"]["volumes"])
    assert "./observability:/etc/prometheus" in volumes


def test_required_coverage_is_present():
    """Part 32 directive 007 plus service health, and the page-level isolation alert."""
    by_name = {r["alert"]: r for _, r in _rules()}
    exprs = " ".join(r["expr"] for r in by_name.values())
    for counter in LANE_A_COUNTERS:
        assert counter in exprs, f"no alert reads {counter}"
        dependents = [r for r in by_name.values() if counter in r["expr"]]
        assert all(counter in r["annotations"].get("depends_on", "") for r in dependents), counter
    assert by_name["HarnessIsolationViolation"]["labels"]["severity"] == "page"
    assert by_name["HarnessServiceDown"]["labels"]["severity"] == "page"
    assert 'up{job="harness-backends"}' in by_name["HarnessServiceDown"]["expr"]


def test_alerting_doc_mentions_every_alert():
    doc = ALERT_DOC.read_text()
    missing = [r["alert"] for _, r in _rules() if r["alert"].removeprefix("Harness") not in doc]
    assert not missing, f"alerting.md does not mention: {missing}"


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool not installed")
def test_promtool_accepts_the_rules():
    out = subprocess.run(
        ["promtool", "check", "rules", str(ALERTS)], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stdout + out.stderr
