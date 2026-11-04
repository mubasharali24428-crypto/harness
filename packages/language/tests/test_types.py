"""Type tests: the vocabulary is closed, and ``T?`` means something.

The checker is not a full type system and should not pretend to be one, but the
rules it does claim are pinned here: nominal boundaries, fixed generic arity,
optionality that survives into comparisons, and operators that keep their types
instead of inventing them.
"""

from __future__ import annotations

from support import check, codes


def test_optional_accepts_a_present_value():
    source = 'schema S {\n  owner: String?\n}\nfn f(s: S) -> Bool {\n  return s.owner != ""\n}\n'
    assert check(source, profile="core").diagnostics == []


def test_an_optional_value_cannot_stand_where_a_present_one_is_required():
    source = "schema S {\n  owner: String?\n}\nfn f(s: S) -> String {\n  return s.owner\n}\n"
    assert codes(check(source, profile="core")) == ["HX3002"]


def test_generics_have_a_fixed_arity():
    one = "fn f(x: List) -> Bool {\n  return true\n}\n"
    two = "fn f(x: Map<String>) -> Bool {\n  return true\n}\n"
    primitive = "fn f(x: Int<String>) -> Bool {\n  return true\n}\n"
    assert codes(check(one, profile="core")) == ["HX3008"]
    assert codes(check(two, profile="core")) == ["HX3008"]
    assert codes(check(primitive, profile="core")) == ["HX3008"]


def test_agent_types_are_only_allowed_in_agent_declarations():
    source = "schema P {\n  text: String\n}\nfn f(x: Agent<P>) -> Bool {\n  return true\n}\n"
    assert codes(check(source)) == ["HX3001"]


def test_an_unknown_type_is_named_as_unknown():
    result = check("fn f(x: Widget) -> Bool {\n  return true\n}\n", profile="core")
    assert codes(result) == ["HX3001"]
    assert "Widget" in result.diagnostics[0].message


def test_a_field_that_does_not_exist_is_not_invented():
    source = "schema S {\n  a: Int\n}\nfn f(s: S) -> Int {\n  return s.b\n}\n"
    assert codes(check(source, profile="core")) == ["HX3004"]


def test_a_member_of_a_primitive_is_not_readable():
    source = "fn f(x: Int) -> Int {\n  return x.b\n}\n"
    assert codes(check(source, profile="core")) == ["HX3011"]


def test_plus_is_string_concatenation_only():
    concat = 'fn f() -> String {\n  return "a" + "b"\n}\n'
    assert check(concat, profile="core").diagnostics == []
    assert codes(check("fn f() -> Bool {\n  return 1 and 2\n}\n", profile="core")) == ["HX3012"]


def test_ordering_needs_comparable_operands():
    assert codes(check("fn f() -> Bool {\n  return 1 < true\n}\n", profile="core")) == ["HX3012"]


def test_an_annotation_must_match_its_value():
    source = 'fn f() -> Bool {\n  let x: Int = "no"\n  return true\n}\n'
    assert codes(check(source, profile="core")) == ["HX3002"]


def test_a_duplicate_parameter_is_reported_once():
    source = "fn f(a: Int, a: Int) -> Bool {\n  return true\n}\n"
    assert codes(check(source, profile="core")) == ["HX3006"]


def test_a_return_in_both_branches_counts_as_ending_with_return():
    source = "fn f(x: Bool) -> Int {\n  if x {\n    return 1\n  } else {\n    return 2\n  }\n}\n"
    assert check(source, profile="core").diagnostics == []


def test_a_missing_final_return_is_an_error():
    source = "fn f(x: Bool) -> Int {\n  let y = 1\n}\n"
    assert codes(check(source, profile="core")) == ["HX3007"]
