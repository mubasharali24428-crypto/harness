import json

from conftest import WEAK, write

from harness_verification import discovery, mutation


def test_discovery_follows_imports_and_names(project):
    r = discovery.discover(project, ["src/shop/pricing.py"])
    by = {t.path: t for t in r.tests}
    assert by["tests/test_pricing.py"].rank <= 5                      # imports + name match
    assert "tests/test_cart.py" in by                                 # cart imports pricing
    assert by["tests/test_cart.py"].reason.startswith("transitively")
    assert all(t.evidence == "static_inference" for t in r.tests)
    assert r.runners == ["pytest"]

    only_cart = discovery.discover(project, ["src/shop/cart.py"])
    assert only_cart.paths() == ["tests/test_cart.py"]


def test_discovery_edge_cases(project):
    stats = discovery.discover(project, ["src/shop/stats.py"])       # no test imports it
    assert stats.tests == [] and stats.unmatched_changed == ["src/shop/stats.py"]
    assert discovery.discover(project, ["README.md"]).unmatched_changed == ["README.md"]
    assert discovery.discover(project, ["pyproject.toml"]).run_all
    gone = discovery.discover(project, ["src/shop/deleted.py"])
    assert gone.notes and gone.unmatched_changed
    changed_test = discovery.discover(project, ["tests/test_cart.py"])
    assert changed_test.tests[0].reason == "changed test"


def test_discovery_typescript_import_graph(tmp_path):
    write(tmp_path, "package.json", json.dumps({"devDependencies": {"vitest": "2"}}))
    write(tmp_path, "src/util.ts", "export const a = 1;")
    write(tmp_path, "src/feature.ts", "import { a } from './util.js'; export const b = a;")
    write(tmp_path, "test/feature.test.ts", "import { b } from '../src/feature.js';")
    r = discovery.discover(tmp_path, ["src/util.ts"])
    assert r.runners == ["vitest"]
    assert r.paths() == ["test/feature.test.ts"]
    assert "transitively" in r.tests[0].reason


def test_mutation_strong_tests_score_high(project):
    rep = mutation.run_mutation(project, ["src/shop/pricing.py"], ["tests/test_pricing.py"],
                                max_mutants=40, python=_py())
    assert rep.status == "executed" and rep.score >= 0.9, [
        (r.mutant.line, r.mutant.detail) for r in rep.survived]
    assert (project / "src/shop/pricing.py").read_text().count("qty >= 10") == 1   # original untouched


def test_mutation_weak_tests_expose_survivors(project):
    write(project, "tests/test_weak.py", WEAK)
    rep = mutation.run_mutation(project, ["src/shop/pricing.py"], ["tests/test_weak.py"],
                                max_mutants=40, python=_py())
    assert rep.status == "executed" and rep.score < 0.6
    surv = {(r.mutant.line, r.mutant.op) for r in rep.survived}
    assert any(op == "compare" for _, op in surv)           # boundary changes went unnoticed
    strong = mutation.run_mutation(project, ["src/shop/pricing.py"], ["tests/test_pricing.py"],
                                   max_mutants=40, python=_py())
    assert strong.score > rep.score


def test_mutation_blocked_when_baseline_fails_and_pragma_respected(project):
    write(project, "tests/test_broken.py", "def test_x():\n    assert False\n")
    rep = mutation.run_mutation(project, ["src/shop/pricing.py"], ["tests/test_broken.py"],
                                python=_py())
    assert rep.status == "blocked" and not rep.baseline_ok
    path = project / "src/shop/stats.py"
    path.write_text(path.read_text().replace("if x < lo:", "if x < lo:  # pragma: no mutate"))
    lines = {m.line for m in mutation.enumerate_mutants(path, "src/shop/stats.py")}
    assert 2 not in lines and lines


def test_mutation_is_deterministic_and_budgeted(project):
    a = mutation.run_mutation(project, ["src/shop/pricing.py"], ["tests/test_pricing.py"],
                              max_mutants=5, seed=3, python=_py())
    b = mutation.run_mutation(project, ["src/shop/pricing.py"], ["tests/test_pricing.py"],
                              max_mutants=5, seed=3, python=_py())
    assert [r.mutant.index for r in a.results] == [r.mutant.index for r in b.results]
    c = mutation.run_mutation(project, ["src/shop/pricing.py"], ["tests/test_pricing.py"],
                              budget_seconds=0, python=_py())
    assert c.results == [] and "budget" in c.note


def _py():
    import sys
    return sys.executable
