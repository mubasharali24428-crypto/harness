"""The code table in the spec and the codes the source emits are one set.

docs/hx-language-spec.md §13 says an added code must be a deliberate change with "a
table entry, a fixture, a golden, and a test that the closed-set assertion was
deliberately updated". This file is that assertion for the checker's codes;
`test_host_codes.py` is the same idea for the driver's.

It reads the spec's §9.4 table rather than a copy of it, because a test that pastes
the codes the source already emits would pass while the documentation said something
else. Two directions are checked: a code emitted with no table entry is undocumented,
and a code in the table that nothing emits is a promise nobody keeps.
"""

from __future__ import annotations

import re
from pathlib import Path

from support import cases, check, modules_for

SRC = Path(__file__).resolve().parents[1] / "src" / "harness_hx"
SPEC = Path(__file__).resolve().parents[3] / "docs" / "hx-language-spec.md"
CODE = re.compile(r"\bHX[1-7]\d{3}\b")
#: Documented as reserved-but-unused; numbers are never reused, so they stay listed.
UNASSIGNED = frozenset({"HX1004", "HX7004"})


def table_codes() -> set[str]:
    """Every code named in §9.4's rows (the driver's `HXH…` rows are excluded)."""
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("### 9.4 Code table")[1].split("### 9.5")[0]
    rows = [line for line in section.splitlines() if line.startswith("| `HX")]
    found: set[str] = set()
    for row in rows:
        found.update(CODE.findall(row.split("|", 2)[2]))
    return found


def emitted_codes() -> set[str]:
    found: set[str] = set()
    for module in sorted(SRC.glob("*.py")):
        found.update(CODE.findall(module.read_text(encoding="utf-8")))
    return found


def fixture_codes() -> dict[str, list[str]]:
    """Which fixtures trigger which codes, goldens included."""
    triggered: dict[str, list[str]] = {}
    for _directory, profile, source in cases():
        result = check(
            source.read_text(encoding="utf-8"),
            profile,
            path=source.name,
            modules=modules_for(source),
        )
        for record in result.diagnostics:
            triggered.setdefault(record.code, []).append(source.name)
    return triggered


def test_the_spec_table_and_the_source_agree_on_every_code():
    documented, emitted = table_codes(), emitted_codes()
    missing = sorted(emitted - documented)
    assert not missing, f"codes emitted with no §9.4 entry: {missing}"
    ghost = sorted(documented - emitted - UNASSIGNED)
    assert not ghost, f"§9.4 names codes nothing emits: {ghost}"


#: Codes the checker can emit for which no fixture exists yet. This list is a
#: ratchet, not a to-do list to grow: it may only shrink, so a new code cannot join
#: it. Each entry was inherited from an earlier milestone that added the code without
#: pinning a fixture, and each still needs one before it can be dropped.
CODES_WITHOUT_FIXTURE = frozenset(
    {
        "HX1003",  # unterminated block comment
        "HX1005",  # invalid escape
        "HX2003",  # expected a declaration
        "HX2004",  # section or agent after the body
        "HX2005",  # duplicate section
        "HX3008",  # wrong type-argument arity
        "HX3009",  # unknown agent setting
        "HX3010",  # agent without role
        "HX3018",  # module over the host's size limit (a fixture cannot supply it)
        "HX4008",  # loop in a pure fn
        "HX7005",  # duplicate capability
    }
)


def test_a_new_code_cannot_join_the_without_a_fixture_list():
    """The ratchet: every emitted code either has a fixture or is listed above."""
    reachable = set(fixture_codes())
    unaccounted = emitted_codes() - reachable
    assert unaccounted <= CODES_WITHOUT_FIXTURE, (
        f"new codes with no fixture: {sorted(unaccounted - CODES_WITHOUT_FIXTURE)}"
    )
    stale = sorted(CODES_WITHOUT_FIXTURE - unaccounted)
    assert not stale, f"drop these from the list now that they have fixtures: {stale}"


def test_the_m8_codes_each_have_a_named_fixture():
    triggered = fixture_codes()
    for code in (
        "HX2006",
        "HX3015",
        "HX3016",
        "HX3017",
        "HX3019",
        "HX3020",
        "HX3021",
        "HX3022",
        "HX3023",
        "HX3024",
        "HX3025",
    ):
        assert triggered.get(code), f"{code} has no fixture"
        assert len(set(triggered[code])) <= 3, f"{code} is over-represented"
