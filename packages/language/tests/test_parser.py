"""Parser tests: syntax errors are located, and recovery keeps the rest of the file."""

from __future__ import annotations

from harness_hx.diagnostics import Reporter
from harness_hx.parser import parse
from support import check


def parse_source(source: str):
    reporter = Reporter("t.hx")
    program = parse(source, reporter)
    return program, reporter.diagnostics()


def test_an_unclosed_brace_points_at_the_opener():
    _, diagnostics = parse_source("schema Issue {\n  id: String\n")
    assert [diagnostic.code for diagnostic in diagnostics] == ["HX2002"]
    assert (diagnostics[0].line, diagnostics[0].col) == (1, 1)


def test_recovery_keeps_the_declarations_that_follow():
    program, diagnostics = parse_source(
        "schema Issue {\n  id: String\n}\n\nfn broken(n: Int) -> Bool {\n  let = 3\n}\n"
    )
    assert [diagnostic.code for diagnostic in diagnostics] == ["HX2001"]
    assert [type(decl).__name__ for decl in program.decls] == ["SchemaDecl", "FnDecl"]


def test_an_unknown_top_level_word_is_a_declaration_error():
    _, diagnostics = parse_source("bogus_thing\nschema X { a: Int }\n")
    assert [diagnostic.code for diagnostic in diagnostics] == ["HX2003"]


def test_requires_after_the_body_is_an_ordering_error():
    source = (
        "schema E { passed: Bool }\n"
        "workflow w() -> E {\n"
        "  budget { steps: 1 }\n"
        "  let e = await verify.tests(1, suite: \"unit\")\n"
        "  requires tool.test\n"
        "  return e\n"
        "}\n"
    )
    result = check(source)
    assert "HX2004" in [diagnostic.code for diagnostic in result.diagnostics]


def test_a_second_budget_section_is_a_duplicate_section_error():
    source = (
        "schema E { passed: Bool }\n"
        "workflow w() -> E {\n"
        "  budget { steps: 1 }\n"
        "  budget { steps: 2 }\n"
        "  let e = await verify.tests(1, suite: \"unit\")\n"
        "  return e\n"
        "}\n"
    )
    result = check(source)
    assert "HX2005" in [diagnostic.code for diagnostic in result.diagnostics]


def test_schema_fields_may_be_separated_by_newlines_or_commas():
    program, diagnostics = parse_source(
        "schema S {\n  a: Int\n  b: String,\n  c: Bool\n}\n"
    )
    assert diagnostics == []
    schema = program.decls[0]
    assert [field.name for field in schema.fields] == ["a", "b", "c"]


def test_optional_and_generic_types_parse():
    program, diagnostics = parse_source(
        "schema S {\n  a: List<Map<String, Int>>\n  b: String?\n}\n"
    )
    assert diagnostics == []
    fields = program.decls[0].fields
    assert fields[0].type.name == "List"
    assert fields[0].type.args[0].name == "Map"
    assert fields[1].type.optional


def test_an_expression_statement_is_kept_for_the_checker_to_judge():
    program, diagnostics = parse_source(
        'fn f() -> Bool {\n  verify.tests(1, suite: "unit")\n  return true\n}\n'
    )
    assert diagnostics == []
    assert type(program.decls[0].body.statements[0]).__name__ == "ExprStmt"
