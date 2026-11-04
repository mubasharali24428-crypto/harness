"""M5: the closed set grew by exactly three constructs, and each one shipped whole.

A construct is added here only when it has all four parts: the rule that accepts
it, the rule that refuses it, a fixture, and a golden that records the refusal at
a reviewed position. This file is the audit of that: it runs the additions end to
end, points at the fixture and golden pair for each, and pins the boundary by
showing what did *not* come in with them — there was still no ``while``, no
assignment and no way to name another file. M8 added `use` and `for … accumulate`;
what M8 did not add is pinned in `test_host_modules.py`, in the same style.

The loop is the one with a story to tell. ``for`` iterates a list, so the list
bounds the passes; it spends one step per pass, so ``budget.steps`` bounds the
run. What that buys is the last test here: a loop over a list far longer than the
budget stops as a *budget* outcome at a recorded step, with the iterations it did
perform still in the event log.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_hx.api import check_source
from harness_hx.lexer import KEYWORDS
from harness_hx_host import FakeHostBridge, run_source

FIXTURES = Path(__file__).resolve().parent / "fixtures"

ENUM_VALUES = (FIXTURES / "valid" / "enum_values.hx").read_text(encoding="utf-8")
RECORDS = (FIXTURES / "valid" / "records.hx").read_text(encoding="utf-8")
LOOPS = (FIXTURES / "valid" / "loops.hx").read_text(encoding="utf-8")

PICK = """\
module pick
enum Severity { low  high }
workflow pick(level: Severity) -> Severity {
  budget { steps: 10; tool_calls: 1; duration: 1m }
  ensure level != Severity.low
  return Severity.high
}
"""

SCAN = """\
module scan
schema RepoFile { path: String  text: String }
workflow scan() -> Int {
  requires repo.read
  budget { steps: 5; tool_calls: 1; duration: 1m }
  let files = await repo.list("docs/")
  for file in files {
    ensure file.path != ""
  }
  return 0
}
"""

WHILE = """\
workflow spin() -> Int {
  budget { steps: 3; tool_calls: 1; duration: 1m }
  while true {
    ensure true
  }
  return 0
}
"""


def golden(directory: str, name: str) -> list[dict[str, object]]:
    return json.loads((FIXTURES / directory / f"{name}.expected.json").read_text(encoding="utf-8"))


def codes(directory: str, name: str) -> list[str]:
    return [str(record["code"]) for record in golden(directory, name)]


# -- each addition is code + fixture + golden --------------------------------


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("enum_unknown_member", "HX3013"),
        ("type_not_callable", "HX3011"),
        ("record_missing_field", "HX3003"),
        ("loop_without_steps", "HX5006"),
        ("loop_over_non_list", "HX3014"),
    ],
)
def test_each_refusal_has_a_fixture_and_a_golden(fixture, code):
    assert codes("invalid", fixture) == [code]
    assert (FIXTURES / "invalid" / f"{fixture}.hx").is_file()


@pytest.mark.parametrize("fixture", ["enum_values", "records", "loops"])
def test_each_addition_has_a_fixture_that_checks_clean(fixture):
    assert golden("valid", fixture) == []
    result = check_source(
        (FIXTURES / "valid" / f"{fixture}.hx").read_text(encoding="utf-8"), fixture, "agent"
    )
    assert result.ok, [record.wire() for record in result.diagnostics]


# -- (a) enum values in source ----------------------------------------------


def test_an_enum_member_is_a_value_of_the_enums_type():
    outcome = run_source(
        PICK, bridge=FakeHostBridge(), granted=[], inputs={"level": "high"}
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.output == "high"


def test_a_value_the_enum_does_not_declare_is_refused_not_coerced():
    outcome = run_source(
        PICK, bridge=FakeHostBridge(), granted=[], inputs={"level": "urgent"}
    )

    assert outcome.status == "error"
    assert "one of low, high" in outcome.error.message
    assert outcome.output is None


# -- (b) record construction ------------------------------------------------


def test_a_constructed_record_is_the_value_the_source_wrote():
    outcome = run_source(
        RECORDS,
        bridge=FakeHostBridge(files={"README.md": "# demo\n"}),
        granted=["repo.read"],
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.output.name == "Report"
    assert outcome.output.to_json() == {
        "headline": {"text": "# demo\n", "path": "README.md"},
        "files": [{"path": "README.md", "text": "# demo\n"}],
    }


def test_a_construction_the_source_cannot_complete_never_runs():
    source = RECORDS.replace(", path: readme.path", "")
    outcome = run_source(source, bridge=FakeHostBridge(), granted=["repo.read"])

    assert outcome.status == "refused"
    assert outcome.error.code == "HXH101"
    assert codes("invalid", "record_missing_field") == ["HX3003"]


# -- (c) loops, and the budget that terminates them -------------------------


def test_a_loop_walks_the_list_and_returns_from_inside_it():
    outcome = run_source(
        LOOPS,
        bridge=FakeHostBridge(files={"docs/architecture-map.md": "# map\n"}),
        granted=["repo.read"],
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.output == "docs/architecture-map.md"
    kinds = [event["kind"] for event in outcome.events]
    assert kinds.count("loop.iteration") == 1


def test_a_list_longer_than_the_budget_ends_the_run_as_a_budget_stop():
    files = {f"docs/{index:03d}.md": "" for index in range(500)}
    outcome = run_source(
        SCAN, bridge=FakeHostBridge(files=files), granted=["repo.read"]
    )

    assert outcome.status == "budget"
    assert outcome.error.code == "HXH501"
    assert "budget.steps (5)" in outcome.error.message
    iterations = [event for event in outcome.events if event["kind"] == "loop.iteration"]
    assert 0 < len(iterations) <= 5, "the loop must stop inside the declared steps"
    assert outcome.evidence()["usage"]["used"]["steps"] > 5


# -- what did not come in with them -----------------------------------------


def test_the_growth_is_by_three_constructs_and_not_by_a_language():
    assert "for" in KEYWORDS and "in" in KEYWORDS
    # No `while`: a loop with no structural bound would need a termination proof
    # this language cannot offer, so it is absent rather than trusted.
    assert "while" not in KEYWORDS
    refused = check_source(WHILE, "spin.hx", "agent")
    assert not refused.ok
    assert all(record.code.startswith("HX2") or record.code.startswith("HX3")
               for record in refused.diagnostics)
    # And the oldest refusal still holds: nothing is loaded while a run goes on.
    # 0.4.0's `use` is resolved and pinned at check time (§5.3); `import` is the
    # runtime load this language has never accepted.
    assert codes("invalid", "import_disabled") == ["HX4004"]
