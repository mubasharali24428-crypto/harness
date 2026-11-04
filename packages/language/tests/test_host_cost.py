"""M6: ``budget.cost`` is a limit, not a note in the evidence.

Three properties, each with its negative control:

* a priced crossing is charged *before* it happens, so a run that cannot pay
  never produces the effect it could not afford;
* an operation the host's table does not price is refused rather than free —
  a ceiling with a hole in it is not a ceiling;
* with no table supplied the cost stays declared-and-reported, and the evidence
  says that in words instead of leaving a reader to guess.

Money is ``Decimal`` throughout, so the tests below pin cent-exact boundaries a
float budget check would get subtly wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from harness_hx_host import (
    FakeHostBridge,
    Pricing,
    PricingError,
    run_source,
)
from harness_hx_host.cli import main as hxr_main
from support import FIXTURES

FIX_ISSUE = (FIXTURES / "valid" / "fix_issue.hx").read_text(encoding="utf-8")
# The target workflow's crossings, in the order the program asks for them.
CROSSINGS = ("repo.read", "developer.plan", "developer.propose", "repo.propose_patch",
             "verify.tests")
ALL = ("repo.read", "repo.propose_patch", "tool.test", "model.invoke")
INPUTS = {"input": {"id": "1", "title": "t", "body": "b"}}
PROPOSAL = {"summary": "s", "patch": "--- a\n+++ b\n"}


def host() -> FakeHostBridge:
    return FakeHostBridge(agents={"developer": PROPOSAL})


def run(prices=None, source: str = FIX_ISSUE, **kwargs):
    bridge = kwargs.pop("bridge", host())
    kwargs.setdefault("granted", ALL)
    kwargs.setdefault("inputs", INPUTS)
    outcome = run_source(source, path="fix_issue.hx", bridge=bridge, prices=prices, **kwargs)
    return outcome, bridge


def test_a_priced_run_stops_before_the_crossing_it_cannot_pay():
    prices = dict.fromkeys(CROSSINGS, "0.50")  # 5 x 0.50 = 2.50 over a 2.00 ceiling
    outcome, bridge = run(prices=prices)
    assert outcome.status == "budget"
    assert outcome.error.code == "HXH501"
    assert len(bridge.calls) == 4, "the fifth crossing was refused before it happened"
    assert bridge.calls[-1].operation == "repo.propose_patch"
    assert outcome.budget["cost"]["used"] == "2.0000"
    assert outcome.error.detail["operation"] == "verify.tests"


def test_an_operation_the_table_does_not_price_is_refused_not_run_free():
    outcome, bridge = run(prices={"repo.read": "0.01"})
    assert outcome.status == "budget"
    assert outcome.error.code == "HXH502"
    assert len(bridge.calls) == 1, "only the priced crossing ran"
    assert "developer.plan" in outcome.error.message
    assert outcome.error.detail["priced"] == ["repo.read"]


def test_the_ceiling_is_spendable_to_the_cent_and_not_one_cent_more():
    prices = {
        "repo.read": "1.00",
        "developer.plan": "0.50",
        "developer.propose": "0.50",
        "repo.propose_patch": "0.00",
        "verify.tests": "0.00",
    }
    outcome, _ = run(prices=prices)
    assert outcome.status == "ok", outcome.error
    assert outcome.budget["cost"]["used"] == "2.0000"


def test_a_run_that_overspends_by_one_cent_stops():
    source = FIX_ISSUE.replace("cost: 2.00 USD", "cost: 1.99 USD")
    prices = {
        "repo.read": "1.00",
        "developer.plan": "0.50",
        "developer.propose": "0.50",
        "repo.propose_patch": "0.00",
        "verify.tests": "0.00",
    }
    outcome, bridge = run(prices=prices, source=source)
    assert outcome.status == "budget"
    assert outcome.error.code == "HXH501"
    assert len(bridge.calls) == 2, "the third crossing was one cent over"


def test_zero_prices_make_the_counter_itself_the_load_bearing_part():
    """Negative control: the stop above must come from prices, not from plumbing.

    Every operation priced at zero, the same ceiling: the run finishes. So the
    budget stop in the first test is the arithmetic, not a check that always fails.
    """
    outcome, bridge = run(prices=dict.fromkeys(CROSSINGS, "0.00"))
    assert outcome.status == "ok", outcome.error
    assert len(bridge.calls) == 5
    assert outcome.budget["cost"]["used"] == "0.0000"
    assert not [event for event in outcome.events if event["kind"] == "cost.charged"]


def test_money_is_exact_where_a_float_budget_would_drift():
    """0.10 + 0.20 must equal 0.30, or a 0.30 ceiling stops a run that fits."""
    source = FIX_ISSUE.replace("cost: 2.00 USD", "cost: 0.30 USD")
    prices = {
        "repo.read": "0.10",
        "developer.plan": "0.20",
        "developer.propose": "0.00",
        "repo.propose_patch": "0.00",
        "verify.tests": "0.00",
    }
    outcome, _ = run(prices=prices, source=source)
    assert outcome.status == "ok", outcome.error
    assert outcome.budget["cost"]["used"] == "0.3000"


def test_no_price_table_leaves_cost_declared_and_the_evidence_says_so():
    outcome, bridge = run(prices=None)
    assert outcome.status == "ok", outcome.error
    assert len(bridge.calls) == 5, "the pre-M6 behaviour is unchanged"
    cost = outcome.evidence()["usage"]["cost"]
    assert cost["mode"] == "declared-not-enforced"
    assert cost["enforced"] is False
    assert cost["declared"] == "2.00"
    assert cost["used"] == "0"


def test_a_run_that_declares_no_cost_is_not_billed_by_a_supplied_table():
    source = FIX_ISSUE.replace("; cost: 2.00 USD", "")
    outcome, _ = run(prices=dict.fromkeys(CROSSINGS, "5.00"), source=source)
    assert outcome.status == "ok", outcome.error
    assert outcome.budget["cost"]["mode"] == "undeclared"
    assert outcome.budget["cost"]["used"] == "0"


def test_a_malformed_price_table_refuses_the_run_and_crosses_nothing():
    for bad in ({"repo.read": "-1.00"}, {"repo.read": "free"}, {"": "1.00"}):
        outcome, bridge = run(prices=bad)
        assert outcome.status == "refused", bad
        assert outcome.error.code == "HXH503", bad
        assert bridge.calls == [], bad


def test_a_spawned_effect_is_charged_once_not_twice():
    source = """
module once
schema RepoFile {
  path: String
  text: String
}
workflow one() -> RepoFile {
  requires repo.read
  budget { steps: 10; tool_calls: 1; tasks: 1; duration: 1m; cost: 1.00 USD }
  let a = spawn repo.read("README.md")
  return await a
}
"""
    outcome = run_source(source, bridge=host(), granted=["repo.read"], inputs={},
                         prices={"repo.read": "0.25"})
    assert outcome.status == "ok", outcome.error
    assert outcome.budget["cost"]["used"] == "0.2500"
    assert [event["kind"] for event in outcome.events].count("cost.charged") == 1


def test_two_parallel_crossings_cannot_both_pass_a_ceiling_one_of_them_breaks():
    """The reservation is atomic: two spawns, two prices, one affordable crossing."""
    source = """
module twice
schema RepoFile {
  path: String
  text: String
}
workflow twice() -> String {
  requires repo.read
  budget { steps: 20; tool_calls: 2; tasks: 2; duration: 1m; cost: 1.00 USD }
  let a = spawn repo.read("README.md")
  let b = spawn repo.read("docs/architecture-map.md")
  let first = await a
  let second = await b
  return first.path
}
"""
    outcome, bridge = run(
        source=source,
        prices={"repo.read": "0.60"},
        granted=["repo.read"],
        inputs={},
        bridge=host(),
    )
    assert outcome.status == "budget"
    assert outcome.error.code == "HXH501"
    assert Decimal(outcome.budget["cost"]["used"]) == Decimal("0.6000")
    assert len(bridge.calls) <= 2


def test_the_table_is_fingerprinted_so_a_replay_can_prove_which_prices_ran():
    prices = dict.fromkeys(CROSSINGS, "0.10")
    outcome, _ = run(prices=prices)
    recorded = outcome.evidence()["usage"]["pricing"]
    assert recorded["operations"] == {name: "0.1000" for name in CROSSINGS}
    assert recorded["sha256"] == Pricing.from_mapping(prices).digest()
    assert recorded["currency"] == "USD"
    # Key order is not a fact a reviewer should have to know.
    assert Pricing.from_mapping(dict(reversed(list(prices.items())))).digest() == (
        recorded["sha256"]
    )


def test_a_price_table_that_cannot_be_built_is_named_as_such():
    with pytest.raises(PricingError, match="at least one operation"):
        Pricing.from_mapping({})
    with pytest.raises(PricingError, match="not finite"):
        Pricing.from_mapping({"repo.read": "NaN"})


def test_the_cli_charges_from_a_price_file(tmp_path):
    table = tmp_path / "prices.json"
    table.write_text('{"repo.read": "0.50"}', encoding="utf-8")
    program = tmp_path / "one.hx"
    program.write_text(
        """
module once
schema RepoFile {
  path: String
  text: String
}
workflow one() -> RepoFile {
  requires repo.read
  budget { steps: 10; duration: 1m; cost: 0.25 USD }
  return await repo.read("a.md")
}
""",
        encoding="utf-8",
    )
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    code = hxr_main([
        "run", str(program), "--grant", "repo.read",
        "--prices", str(table), "--evidence", str(evidence),
    ])
    assert code == 4, "a declared ceiling the first crossing already breaks"
    record = _read_json(evidence)
    assert record["outcome"]["error"]["code"] == "HXH501"
    assert record["usage"]["cost"]["mode"] == "enforced"


def test_the_cli_refuses_a_price_file_it_cannot_read(tmp_path):
    program = tmp_path / "one.hx"
    program.write_text(FIX_ISSUE, encoding="utf-8")
    code = hxr_main([
        "run", str(program), "--grant", ",".join(ALL),
        "--prices", str(tmp_path / "absent.json"),
    ])
    assert code == 2, "a missing table is a usage failure, never an unpriced run"


def _read_json(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))
