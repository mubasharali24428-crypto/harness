"""Message tests: one line, one code, and the family shows in the code.

A message is read by three audiences — a human in a terminal, the located
parser in Section 5, and the review gate that counts errors. A newline or a
second code inside one record breaks all three, so those are pinned here.
"""

from __future__ import annotations

import re

from harness_hx.diagnostics import Reporter
from support import cases, check, modules_for

CODE_PREFIX = re.compile(r"^\[(HX[1-7]\d{3})\] ")
FAMILY_BY_STEM = {
    "lexical_": "HX1",
    "syntax_": "HX2",
    "budget_": "HX5",
    "profile_": "HX6",
}


def every_fixture():
    for _directory, profile, source in cases():
        result = check(
            source.read_text(encoding="utf-8"),
            profile,
            path=source.name,
            modules=modules_for(source),
        )
        yield source, result


def test_every_record_starts_with_exactly_one_code():
    for source, result in every_fixture():
        for record in result.diagnostics:
            match = CODE_PREFIX.match(record.text)
            assert match, (source.name, record.text)
            assert match.group(1) == record.code, (source.name, record.text)
            assert record.text.count("[HX") == 1, (source.name, record.text)


def test_every_record_is_one_line_of_plain_text():
    for source, result in every_fixture():
        for record in result.diagnostics:
            assert record.message == record.message.strip(), (source.name, record.text)
            assert "\n" not in record.text, (source.name, record.text)
            assert record.kind in {"error", "warning", "note"}, (source.name, record.kind)


def module_names(source) -> set[str]:
    """The files a fixture may be blamed in: itself plus its sidecar modules.

    Since M8 a check spans files, so "one record, one file" is still the rule and
    the set of legal files grew. A module's mistake is reported against the module —
    pinning every record to the entry file would push authors to read a helper's
    error as a bug in the program that called it.
    """
    modules = modules_for(source) or {}
    return {source.name, *(f"{name}.hx" for name in modules)}


def test_every_record_names_the_file_it_came_from_once_encoded():
    for source, result in every_fixture():
        allowed = module_names(source)
        for record in result.diagnostics:
            assert record.path in allowed, (source.name, record.path, record.text)
            assert source.name not in record.message
            for name in allowed:
                assert name not in record.message, (source.name, record.text)


def test_a_fixture_announces_its_family_in_the_code():
    paired = 0
    for source, result in every_fixture():
        for prefix, family in FAMILY_BY_STEM.items():
            if source.stem.startswith(prefix):
                paired += 1
                assert result.diagnostics, source.name
                for record in result.diagnostics:
                    assert record.code.startswith(family), (source.name, record.code)
    assert paired >= 5


def test_position_is_one_based_and_inside_the_file():
    """The position is checked against the file the record names, not the entry."""
    for source, result in every_fixture():
        texts = {source.name: source.read_text(encoding="utf-8")}
        for name, text in (modules_for(source) or {}).items():
            texts[f"{name}.hx"] = text
        for record in result.diagnostics:
            if record.line is None:
                continue
            lines = texts.get(str(record.path), texts[source.name]).splitlines()
            assert 1 <= record.line <= len(lines), (source.name, record.text)
            assert 1 <= record.col, (source.name, record.text)


def test_identical_records_are_reported_once():
    reporter = Reporter("t.hx")
    reporter.error("HX4001", "capability 'tool.test' is not declared", 3, 7)
    reporter.error("HX4001", "capability 'tool.test' is not declared", 3, 7)
    reporter.error("HX4001", "capability 'tool.test' is not declared", 4, 7)
    assert len(reporter.diagnostics()) == 2


def test_the_errors_counter_is_not_fooled_by_dedupe():
    reporter = Reporter("t.hx")
    reporter.error("HX4001", "same", 1, 1)
    reporter.error("HX4001", "same", 1, 1)
    reporter.warning("HX7001", "careful", 2, 1)
    assert reporter.errors() == 1
