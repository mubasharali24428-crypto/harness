import json
import sys

from conftest import PRICING, WEAK, write

from harness_verification import cli, contract
from harness_verification.scorecard import Check, build_scorecard

PY = sys.executable
BUGGY = PRICING.replace("pct > 100", "pct > 101")


def by_name(sc):
    return {c.name: c for c in sc.checks}


def test_seeded_defect_fails_then_fix_passes_with_labelled_evidence(project):
    write(project, "src/shop/pricing.py", BUGGY)
    bad = build_scorecard(project, ["src/shop/pricing.py"], python=PY)
    assert bad.verdict == "fail"
    assert by_name(bad)["tests"].evidence == "executed" and by_name(bad)["tests"].passed is False
    assert by_name(bad)["mutation"].evidence == "skipped"        # needs a green baseline

    write(project, "src/shop/pricing.py", PRICING)
    good = build_scorecard(project, ["src/shop/pricing.py"], python=PY, max_mutants=40)
    c = by_name(good)
    assert good.verdict == "pass", (good.reasons, [x.detail for x in good.checks])
    assert c["test_discovery"].evidence == "static_inference"
    assert c["tests"].evidence == "executed" and c["mutation"].evidence == "executed"
    assert c["contract"].evidence == "skipped"
    assert len(good.evidence_digest) == 64 and json.loads(good.to_json())["verdict"] == "pass"


def test_weak_tests_cannot_earn_a_pass(project):
    (project / "tests/test_pricing.py").unlink()
    write(project, "tests/test_pricing.py", WEAK)
    sc = build_scorecard(project, ["src/shop/pricing.py"], python=PY, max_mutants=40)
    assert by_name(sc)["tests"].passed is True
    assert by_name(sc)["mutation"].passed is False and sc.verdict == "fail"
    assert by_name(sc)["mutation"].data["survivors"]


def test_missing_tests_and_blocked_evidence_never_pass(project):
    sc = build_scorecard(project, ["src/shop/stats.py"], python=PY)       # nothing tests stats.py
    assert sc.verdict == "insufficient_evidence"
    assert by_name(sc)["tests"].evidence == "blocked"
    sc = build_scorecard(project, ["src/shop/pricing.py"], python=PY, run_mutation_check=False)
    assert sc.verdict == "pass_weak" and "strength was not measured" in " ".join(sc.reasons)
    sc = build_scorecard(project, ["src/shop/pricing.py"], python=PY, run_mutation_check=False,
                         contract=Check("contract", "executed", False, "drift"))
    assert sc.verdict == "fail"


def test_digest_changes_with_evidence(project):
    a = build_scorecard(project, ["src/shop/cart.py"], python=PY, run_mutation_check=False)
    write(project, "tests/test_cart.py", "def test_x():\n    assert False\n"
          "from shop.cart import line_total\n")
    b = build_scorecard(project, ["src/shop/cart.py"], python=PY, run_mutation_check=False)
    assert a.evidence_digest != b.evidence_digest and b.verdict == "fail"


# ---------------------------------- contract ---------------------------------------------
def spec(paths):
    return {"paths": {p: {m.lower(): {"responses": {c: {} for c in codes}} for m, codes in ops}
                      for p, ops in paths.items()}}


def test_openapi_drift_classification():
    committed = spec({"/a": [("GET", ["200"])], "/b": [("POST", ["201"])],
                      "/c": [("GET", ["200", "404"])]})
    live = spec({"/a": [("GET", ["200"])], "/c": [("GET", ["200"])], "/d": [("DELETE", ["200"])]})
    d = contract.diff_specs(committed, live)
    assert d.undocumented == ["DELETE /d"] and d.stale == ["POST /b"]
    assert d.status_changes == ["GET /c: ['200', '404'] -> ['200']"] and not d.clean
    assert contract.diff_specs(committed, committed).clean


def test_live_spec_accepts_app_factory_module(tmp_path, monkeypatch):
    (tmp_path / "fakeapp.py").write_text(
        "class A:\n    def openapi(self):\n        return {'paths': {'/x': {'get': {'responses': {'200': {}}}}}}\n"
        "def make():\n    return A()\n")
    got = contract.live_spec("fakeapp:make", [str(tmp_path)])
    assert contract.diff_specs(spec({"/x": [("GET", ["200"])]}), got).clean


def test_golden_fixtures_validate_and_round_trip(tmp_path):
    class Model:
        def __init__(self, d):
            if "id" not in d:
                raise ValueError("id required")
            self.d = d

        def model_dump(self, **_):
            return {"id": self.d["id"]}

    (tmp_path / "task.ok.json").write_text(json.dumps({"id": 1}))
    (tmp_path / "task.bad.json").write_text(json.dumps({"name": "x"}))
    (tmp_path / "task.renamed.json").write_text(json.dumps({"id": 1, "extra_field": 2}))
    (tmp_path / "unknown.x.json").write_text("{}")
    r = contract.check_golden(tmp_path, {"task": Model})
    assert r.checked == 3 and not r.ok
    text = " ".join(r.failures)
    assert "task.bad.json" in text and "round trip" in text and "no validator" in text


# ---------------------------------- cli ---------------------------------------------------
def test_cli_discover_and_weakening(project, capsys, tmp_path):
    assert cli.main(["--root", str(project), "discover", "--changed", "src/shop/cart.py"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [t["path"] for t in out["tests"]] == ["tests/test_cart.py"]
    before, after = tmp_path / "b.py", tmp_path / "a.py"
    before.write_text("def test_a():\n    assert 1 == 1\n")
    after.write_text("def test_a():\n    pass\n")
    assert cli.main(["weakening", str(before), str(after)]) == 1
    assert cli.main(["weakening", str(before), str(before)]) == 0


def test_cli_scorecard_exit_codes(project, capsys):
    assert cli.main(["--root", str(project), "scorecard", "--changed", "src/shop/cart.py",
                     "--no-mutation"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "pass_weak"
    assert cli.main(["--root", str(project), "scorecard", "--changed", "src/shop/stats.py",
                     "--no-mutation"]) == 3
