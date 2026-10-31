"""Every fixture is checked against a reviewed golden.

Two directions matter and both are asserted: a broken fixture must produce
exactly the records its golden names (no more, no fewer, at the same positions),
and a clean fixture must produce none. A golden that drifts silently is worse
than no test, which is why regeneration lives in its own script and the diff is
meant to be read.
"""

from __future__ import annotations

import pytest

from support import case_ids, cases, check, errors, expected_for, modules_for, records


@pytest.mark.parametrize(("directory", "profile", "source"), cases(), ids=case_ids())
def test_fixture_matches_its_golden(directory, profile, source):
    result = check(
        source.read_text(encoding="utf-8"), profile, path=source.name, modules=modules_for(source)
    )
    assert records(result) == expected_for(source)


def test_clean_fixtures_produce_no_errors_at_all():
    # ``core_workflow`` is the refusal fixture for the core profile; its golden
    # names HX6001, so it is not expected to be clean.
    for directory, profile, source in cases():
        if directory == "invalid" or source.stem == "core_workflow":
            continue
        result = check(
            source.read_text(encoding="utf-8"),
            profile,
            path=source.name,
            modules=modules_for(source),
        )
        assert errors(result) == [], f"{source.name} should be clean"


def test_warning_fixture_fails_no_gate():
    source = next(item for item in cases() if item[2].stem == "no_budget")[2]
    result = check(source.read_text(encoding="utf-8"), path=source.name)
    assert result.ok
    assert result.warnings == 3


def test_every_fixture_has_a_golden():
    for _, _, source in cases():
        assert source.with_suffix(".expected.json").exists(), source.name
