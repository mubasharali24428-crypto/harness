"""Budget tests: every key, every bound, and the units are not guesswork."""

from __future__ import annotations

from support import check, codes

HEADER = (
    "schema Evidence {\n"
    "  passed: Bool\n"
    "}\n"
    "workflow w(input: String) -> Evidence {\n"
    "  requires tool.test\n"
)


def workflow(budget: str) -> str:
    return (
        HEADER
        + f"  budget {{ {budget} }}\n"
        + '  let e = await verify.tests(input, suite: "unit")\n'
        + "  return e\n"
        + "}\n"
    )


def test_the_five_known_keys_are_accepted():
    source = workflow("steps: 10; tool_calls: 2; tasks: 2; duration: 90s; cost: 0.50 USD")
    assert check(source).diagnostics == []


def test_an_unknown_key_lists_what_is_known():
    result = check(workflow("steps: 10; tokens: 100"))
    assert codes(result) == ["HX5001"]
    assert "known: steps, tool_calls, tasks, duration, cost" in result.diagnostics[0].message


def test_a_duplicate_key_is_reported_even_if_the_first_value_was_bad():
    result = check(workflow("steps: 0; steps: 10"))
    assert codes(result) == ["HX5003", "HX5002"]


def test_count_keys_must_be_positive_and_bounded():
    assert codes(check(workflow("steps: 0"))) == ["HX5003"]
    assert codes(check(workflow("tool_calls: 1000001"))) == ["HX5003"]
    assert codes(check(workflow("tasks: -1"))) == ["HX2001"]


def test_a_duration_must_be_a_duration_and_within_a_day():
    assert codes(check(workflow("duration: 10"))) == ["HX5004"]
    assert codes(check(workflow("duration: 25h"))) == ["HX5003"]
    assert codes(check(workflow("duration: 24h"))) == []


def test_cost_needs_a_currency_and_that_currency_is_usd():
    assert codes(check(workflow("cost: 5"))) == ["HX5004"]
    assert codes(check(workflow("cost: 2.00 EUR"))) == ["HX5004"]
    assert codes(check(workflow("cost: 0 USD"))) == ["HX5003"]
    assert codes(check(workflow("cost: 1000.01 USD"))) == ["HX5003"]


def test_a_budget_that_is_absent_is_a_warning_not_a_refusal():
    source = HEADER + '  let e = await verify.tests(input, suite: "unit")\n  return e\n}\n'
    result = check(source)
    assert codes(result) == ["HX7002"]
    assert result.ok


def test_spawn_without_a_tasks_limit_is_a_warning():
    source = workflow("steps: 5").replace(
        'let e = await verify.tests(input, suite: "unit")',
        'let task = spawn verify.tests(input, suite: "unit")\n  let e = await task',
    )
    result = check(source)
    assert codes(result) == ["HX7003"]
