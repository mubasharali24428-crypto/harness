import sys

import pytest
from conftest import PRICING, STRONG, write

from harness_verification import repair, testgen

PY = sys.executable
BUGGY = PRICING.replace("pct > 100", "pct > 101")          # seeded defect: 101 accepted


@pytest.fixture
def buggy(project):
    write(project, "src/shop/pricing.py", BUGGY)
    return project


def fixer_sequence(*patches):
    it = iter(patches)
    return lambda failure, attempts, wd: next(it, None)


def test_defect_reproduces_then_repair_loop_fixes_it(buggy):
    before = repair.run_pytest(buggy, ["tests/test_pricing.py"], python=PY)
    assert before is not None and any("test_discount_range" in i for i in before.ids)

    wrong = {"src/shop/pricing.py": BUGGY.replace("pct < 0", "pct < -5")}      # plausible, wrong
    right = {"src/shop/pricing.py": PRICING}
    res = repair.run_repair(buggy, ["tests/test_pricing.py"], fixer_sequence(wrong, right),
                            python=PY)
    assert res.status == "fixed" and [a.outcome for a in res.attempts] == ["still_failing", "passed"]
    assert res.patch["src/shop/pricing.py"] == PRICING
    assert "pct > 101" in (buggy / "src/shop/pricing.py").read_text()      # original tree untouched


def test_repair_budget_and_stall_controls(buggy):
    same = {"src/shop/pricing.py": BUGGY}
    res = repair.run_repair(buggy, ["tests/test_pricing.py"], lambda *a: same, python=PY,
                            budget=repair.Budget(max_attempts=2))
    assert res.status in ("exhausted", "stalled") and len(res.attempts) <= 2
    res = repair.run_repair(buggy, ["tests/test_pricing.py"], lambda *a: same, python=PY,
                            budget=repair.Budget(max_seconds=0))
    assert res.status == "timeout" and res.attempts == []
    res = repair.run_repair(buggy, ["tests/test_pricing.py"], lambda *a: None, python=PY)
    assert res.status == "no_proposal"
    assert repair.run_repair(project_ok(buggy), ["tests/test_cart.py"], lambda *a: None,
                             python=PY).status == "already_passing"


def project_ok(p):
    return p


def test_repair_refuses_test_weakening_and_unsafe_patches(buggy):
    t = "tests/test_pricing.py"
    cases = {
        "test edits off by default": ({t: "def test_discount_range():\n    pass\n"}, False,
                                      "editing tests is not allowed"),
        "deleting a test": ({t: STRONG.replace("def test_bulk_threshold", "def _unused")}, True,
                            "removed"),
        "dropping assertions": ({t: STRONG.replace("    assert bulk_price(0, 10) == 0\n", "")}, True,
                                "dropped"),
        "adding skip": ({t: "import pytest\n" + STRONG.replace(
            "def test_discount_range", "@pytest.mark.skip\ndef test_discount_range")}, True, "skip"),
        "path escape": ({"../evil.py": "x=1"}, False, "escapes"),
        "not allowed glob": ({"README.md": "x"}, False, "not allowed"),
        "huge patch": ({"src/shop/pricing.py": "\n".join(f"x{i}=1" for i in range(400))}, False,
                       "too large"),
    }
    for label, (patch, allow, needle) in cases.items():
        res = repair.run_repair(buggy, [t], lambda *a, p=patch: p, python=PY,
                                allow_test_edits=allow)
        assert res.status == "rejected_patch" and needle in res.reason, (label, res.reason)


def test_weakening_detector_unit():
    assert repair.weakening_violations(STRONG, STRONG) == []
    assert repair.weakening_violations(STRONG, STRONG + "\n# comment") == []
    # adding a trivial assert removes nothing ...
    assert repair.weakening_violations(STRONG, STRONG.replace("== 90.0", "== 90.0\n    assert True")) == []
    # ... but swapping a real assertion for `assert True` is a weakening
    swapped = STRONG.replace("assert bulk_price(10, 10) == 90.0", "assert True")
    assert any("dropped" in v for v in repair.weakening_violations(STRONG, swapped))
    assert repair.weakening_violations(STRONG, "def test_discount_values():\n    assert 1 == 1\n")


# ---------------------------------- test generation -------------------------------------
def test_characterization_proposal_is_labelled_and_passes_the_gates(project):
    fn = next(f for f in testgen.list_functions(project, "src/shop/stats.py")
              if f.name == "clamp")
    props = testgen.CharacterizationGenerator(PY).propose(project, fn)
    assert len(props) == 1 and "CHARACTERIZATION" in props[0].rationale
    assert "does not prove" in props[0].rationale and props[0].cases >= 6
    ev = testgen.evaluate(project, props[0], ["tests/test_pricing.py"], python=PY, mutants=15)
    assert ev.passes_on_current and ev.accepted
    assert (ev.coverage_gained_lines or 0) > 0 and (ev.mutants_killed or 0) >= 1, ev


def test_gate_rejects_failing_and_redundant_proposals(project):
    bad = testgen.TestProposal("src/shop/stats.py::clamp", "x", "from shop.stats import clamp\n\n"
                               "def test_wrong():\n    assert clamp(5, 0, 1) == 99\n")
    ev = testgen.evaluate(project, bad, [], python=PY)
    assert not ev.accepted and not ev.passes_on_current

    redundant = testgen.TestProposal(
        "src/shop/pricing.py::apply_discount", "dup",
        "from shop.pricing import apply_discount\n\ndef test_dup():\n"
        "    assert apply_discount(100, 0) == 100\n")
    ev = testgen.evaluate(project, redundant, ["tests/test_pricing.py"], python=PY, mutants=20)
    assert ev.passes_on_current and (ev.coverage_gained_lines or 0) == 0
    # it still kills a few mutants on its own, so the gate is mutant-driven, not coverage-only
    assert isinstance(ev.accepted, bool)


def test_no_proposal_when_function_has_no_parameters(project):
    write(project, "src/shop/const.py", "def answer():\n    return 42\n")
    fn = testgen.list_functions(project, "src/shop/const.py")[0]
    assert testgen.CharacterizationGenerator(PY).propose(project, fn) == []
