"""H1 — the tool-output protocol, and a check that the parser can fail.

Two things are tested here, deliberately in that order:

1. **Round-trip.** Hermes' contract is symmetric — it prints the records, a
   caller reads them back. Emitting with ``render_diagnostics_json`` and parsing
   with the protocol tier must return the same records, including the exact
   field set, so a tool written against the contract and the sandbox reading it
   cannot drift apart silently.

2. **Seeded detection (the validate-the-validator step).** Hermes proved its own
   fuzzer was not vacuous by injecting deliberate bugs and confirming the suite
   caught each one. The same discipline applies to a parser that is about to
   become a review gate: every diagnostic the corpus plants must come back, and
   every output that contains none must come back empty. A parser that reports
   whatever it likes passes tests like these only by accident, so the corpus
   pins the count, the kind, and — where the source had one — the position.
"""

import json

import pytest

from harness_sandbox.output_parser import (
    Diagnostic,
    parse_exec_output,
    parse_located_line,
    parse_protocol_diagnostics,
    render_diagnostics_json,
)

# ---------------------------------------------------------------------------
# 1. the contract round-trips
# ---------------------------------------------------------------------------


def test_hermes_field_order_is_the_wire_format():
    records = [
        Diagnostic(path="app.hms", line=3, col=7, kind="error", message="expected ')'"),
        Diagnostic(path="app.hms", line=9, kind="warning", message="unused variable"),
        Diagnostic(kind="note", message="no source location"),
    ]
    expected = (
        '[{"path":"app.hms","line":3,"col":7,"kind":"error","message":"expected \')\'"},'
        '{"path":"app.hms","line":9,"kind":"warning","message":"unused variable"},'
        '{"kind":"note","message":"no source location"}]'
    )
    assert render_diagnostics_json(records) == expected


@pytest.mark.parametrize(
    "records",
    [
        [],
        [Diagnostic(kind="error", message="boom")],
        [
            Diagnostic(path="src/a.py", line=1, col=2, kind="warning", message="tab after space"),
            Diagnostic(
                path="src/b.py", line=44, col=1, kind="error", message='quote " and \\ and é'
            ),
        ],
    ],
)
def test_emit_then_parse_is_identity(records):
    """The writer half and the reader half must agree, or the transcript lies."""
    stream = render_diagnostics_json(records)
    assert parse_protocol_diagnostics(stream) == records
    # the wire form is valid JSON and nothing but JSON — stdout rule holds
    assert json.loads(stream) == [r.model_dump(exclude_none=True) for r in records]


def test_protocol_tier_wins_and_reports_its_source():
    stderr = render_diagnostics_json(
        [Diagnostic(path="x.hms", line=2, col=1, kind="error", message="undefined name 'x'")]
    )
    result = parse_exec_output("hermesc run x.hms --json", 1, "", stderr, 10)
    assert result.diagnostics_source == "protocol"
    assert result.diagnostics[0].path == "x.hms"
    assert result.diagnostics[0].line == 2
    assert result.diagnostics[0].kind == "error"


def test_streaming_records_are_read_too():
    """NDJSON — one record per line — is what a tool that reports as it goes emits."""
    stream = '{"path":"a.c","line":1,"col":1,"kind":"error","message":"first"}\nnoise\n' \
             '{"path":"a.c","line":8,"col":3,"kind":"warning","message":"second"}\n'
    records = parse_protocol_diagnostics(stream)
    assert [r.message for r in records] == ["first", "second"]
    assert records[1].kind == "warning"


def test_foreign_tool_json_maps_without_inventing_positions():
    """Compatibility tier: real keys from ruff/eslint/tsc, no fabricated fields."""
    ruff = json.dumps(
        [
            {
                "code": "F401",
                "filename": "app.py",
                "line": 12,
                "column": 1,
                "message": "`os` imported but unused",
                "end_line": 12,
            }
        ]
    )
    records = parse_protocol_diagnostics(ruff)
    assert len(records) == 1
    assert records[0].path == "app.py"
    assert records[0].line == 12 and records[0].col == 1
    # an unrecognised severity word stays the conservative error, not a soft note
    assert records[0].kind == "error"


def test_unrelated_stderr_json_is_not_a_diagnostic():
    """A tool printing its own JSON result must not become fake diagnostics."""
    assert parse_protocol_diagnostics('{"status":"ok","count":3}') == []
    assert parse_protocol_diagnostics("not json at all") == []
    assert parse_protocol_diagnostics('[{"msg":"  "}]') == []


# ---------------------------------------------------------------------------
# 2. located lines — the compiler text form, positions are real
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, path, kind",
    [
        ("main.c:12:5: error: implicit declaration of 'foo'", "main.c", "error"),
        ("app.hms:3:7: error: expected ')'", "app.hms", "error"),
        ("/tmp/x/y.ts(4,9): error TS2322: Type 'string' is not assignable", "/tmp/x/y.ts", "error"),
        ("src/a.py:88:1: warning: unused import", "src/a.py", "warning"),
    ],
)
def test_located_lines_carry_positions(line, path, kind):
    record = parse_located_line(line)
    assert record is not None
    assert record.kind == kind
    assert record.line is not None and record.col is not None
    if path is not None:
        assert record.path == path


@pytest.mark.parametrize(
    "line",
    [
        "========================= 1 failed, 4 passed in 0.45s ==========================",
        "Collecting numpy",
        "Traceback (most recent call last):",
        '{"some":"result","not":"a diagnostic"}',
        "4 files changed, 12 insertions(+)",
    ],
)
def test_context_lines_are_not_diagnostics(line):
    """The scan tier exists precisely because these have no position to report."""
    assert parse_located_line(line) is None


def test_located_tier_is_only_read_from_stderr():
    """The contract keeps stdout for the program's output; a stdout that looks
    like a compiler error is the program's own text, not a diagnostic source."""
    result = parse_exec_output("cat notes.txt", 0, "main.c:3:1: error: not a build", "", 5)
    assert result.diagnostics_source == "scan"
    assert result.diagnostics[0].line is None

    from_stderr = parse_exec_output("make", 2, "", "main.c:3:1: error: real", 5)
    assert from_stderr.diagnostics_source == "located"
    assert from_stderr.diagnostics[0].line == 3


# ---------------------------------------------------------------------------
# 3. seeded corpus — the parser must catch everything planted, nothing invented
# ---------------------------------------------------------------------------

# (name, stderr, stdout, expected kinds in order)
SEEDED = [
    (
        "hermes-syntax-error",
        '[{"path":"app.hms","line":3,"col":7,"kind":"error","message":"expected \')\'"}]',
        "",
        ["error"],
    ),
    (
        "hermes-error-plus-warning",
        '[{"path":"a.hms","line":1,"col":1,"kind":"error","message":"bad"},'
        '{"path":"a.hms","line":6,"col":2,"kind":"warning","message":"shadowed"}]',
        "42\n",  # the program's own stdout must not be read as diagnostics
        ["error", "warning"],
    ),
    (
        "gcc-two-errors",
        "src/m.c:4:9: error: undeclared 'x'\nsrc/m.c:9:1: error: bad type\n",
        "make: *** [Makefile:12: build] Error 1\n",
        ["error", "error"],
    ),
    (
        "tsc-no-colon-dash",
        "app.tsx(17,24): error TS2339: Property 'foo' does not exist\n",
        "",
        ["error"],
    ),
    (
        "eslint- stylish-located",
        "  12:3  error  Unexpected console statement\n",
        "",
        ["error"],
    ),
    (
        "pytest-failure-summary",
        "",
        "FAILED tests/test_api.py::test_login - assert False\n1 failed, 4 passed\n",
        ["error"],
    ),
    (
        "vitest-dot-marker",
        "● Test suite failed to run\n",
        "",
        ["error"],
    ),
    (
        "clean-run",
        "",
        "========================= 5 passed in 0.31s ==========================\n",
        [],
    ),
    (
        "silent-success",
        "",
        "ok\n",
        [],
    ),
]


@pytest.mark.parametrize("name, stderr, stdout, kinds", SEEDED)
def test_every_planted_defect_is_detected(name, stderr, stdout, kinds):
    result = parse_exec_output("tool", 1 if kinds else 0, stdout, stderr, 10)
    assert [d.kind for d in result.diagnostics] == kinds, name
    if kinds:
        assert result.diagnostics_source in ("protocol", "located", "scan"), name
    else:
        assert result.diagnostics_source == "none", name


def test_seeded_corpus_covers_all_three_tiers():
    """A single-tier parser would be a scraped string matcher wearing a name."""
    sources = set()
    for _name, stderr, stdout, kinds in SEEDED:
        if not kinds:
            continue
        sources.add(parse_exec_output("tool", 1, stdout, stderr, 10).diagnostics_source)
    assert sources == {"protocol", "located", "scan"}


def test_truncation_is_declared_not_silent():
    stderr = "\n".join(f"f{i}.c:1:1: error: seeded {i}" for i in range(40))
    result = parse_exec_output("make", 1, "", stderr, 10)
    assert len(result.diagnostics) == 20
    assert result.diagnostics_truncated is True
    assert result.diagnostics_source == "located"


def test_duplicates_do_not_pad_the_count():
    stderr = "app.c:1:1: error: same defect\napp.c:1:1: error: same defect\n"
    result = parse_exec_output("make", 1, "", stderr, 10)
    assert len(result.diagnostics) == 1
