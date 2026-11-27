"""ci-local reads ci.yml instead of copying it, and live-PG tests never default onto a fixed port.

Run: section4/.venv/bin/python -m pytest scripts/test_ci_local.py -q
"""

import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_local", ROOT / "scripts" / "ci_local.py")
ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci)
CFG = yaml.safe_load(ci.CI_YML.read_text())


def test_dispatch_selects_the_forced_full_list():
    assert set(ci.SECTIONS) <= set(ci.changed_filters(CFG, "dispatch", None))


def test_job_if_is_evaluated_not_assumed():
    jobs = CFG["jobs"]
    forced = ci.changed_filters(CFG, "dispatch", None)
    assert ci.job_enabled(jobs["nightly-pg-probe"], "dispatch", forced)[0]
    ok, why = ci.job_enabled(jobs["nightly-pg-probe"], "pull_request", ["section1"])
    assert not ok and "event filter" in why
    ok, why = ci.job_enabled(jobs["mcp"], "pull_request", ["section1"])
    assert not ok and why == "path filter: 'mcp' not touched"
    assert ci.job_enabled(jobs["mcp"], "pull_request", ["mcp"])[0]
    assert not ci.job_enabled(jobs["python-tests"], "pull_request", ["mcp"])[0]
    assert ci.job_enabled(jobs["static-gates"], "pull_request", [])[0]  # no `if`: always runs


def test_every_gate_step_is_tagged_and_every_job_is_known():
    # ci-local runs exactly the [..]-tagged run steps; a new untagged gate step would be
    # silently treated as provisioning, so pin the convention on the jobs that gate PRs.
    for jid in ("static-gates", "python-tests", "mcp", "language", "frontend"):
        runs = [s for s in CFG["jobs"][jid]["steps"] if "run" in s]
        gates = [s for s in runs if re.search(r"\[[^\]]+\]", str(s.get("name", "")))]
        assert gates, jid
        for s in runs:
            if s not in gates:
                assert re.search(r"npm ci|venv|pip install|openssl", s["run"]), (jid, s)


def test_expand_refuses_unknown_expressions():
    assert ci.expand("make test-${{ matrix.section }}", {"matrix.section": "section2"}) == "make test-section2"
    try:
        ci.expand("${{ secrets.OTHER }}", {})
    except KeyError:
        return
    raise AssertionError("unknown expression must not expand to an empty string")


def test_live_pg_tests_never_hardcode_a_port_without_the_dsn_override():
    # 2026-11-21: 22 helpers fell back to 127.0.0.1:5499 under HARNESS_TEST_PG=1 and ignored
    # HARNESS_TEST_PG_DSN; on a machine where 5499 is another stack's database, a probe run
    # would create and drop scratch databases there.
    bare = re.compile(r"postgresql://harness:harness@(127\.0\.0\.1|localhost):5499/harness")
    offenders = []
    for p in list(ROOT.glob("section*/tests/**/*.py")) + list(ROOT.glob("integration/*.py")):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if bare.search(line) and "HARNESS_TEST_PG_DSN" not in line and not line.lstrip().startswith(("#", '"', "'")):
                offenders.append(f"{p.relative_to(ROOT)}:{n}")
    assert offenders == []
