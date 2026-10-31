"""Boundary tests: a host reply is data until it matches the declared type.

The point of these tests is that validation is *nominal* and *closed*: a field
that was not declared fails, a missing field fails, a Bool is not an Int, and a
``List<RepoFile>`` is checked element by element so the failure names the index.
"""

from __future__ import annotations

from harness_hx import ast as A
from harness_hx_host.boundary import SchemaValue, split_type, truthy, validate
from harness_hx_host.errors import InvalidHostResult


def schema(name: str, **fields: str) -> A.SchemaDecl:
    return A.SchemaDecl(
        name=name,
        fields=tuple(
            A.Field(field_name, A.TypeRef(field_type, (), False, 0, 0), None, 0, 0)
            for field_name, field_type in fields.items()
        ),
        line=0,
        col=0,
    )


SCHEMAS = {
    "RepoFile": schema("RepoFile", path="String", text="String"),
    "Stats": schema("Stats", files="List<RepoFile>", note="String?"),
}


def test_primitives_are_checked_and_bool_is_not_an_int():
    assert validate("String", "x", SCHEMAS) == "x"
    assert validate("Int", 3, SCHEMAS) == 3
    assert validate("Float", 3, SCHEMAS) == 3
    for type_text, value in (("String", 1), ("Int", True), ("Bool", "true"), ("Int", 1.5)):
        try:
            validate(type_text, value, SCHEMAS, error=InvalidHostResult)
        except InvalidHostResult:
            continue
        raise AssertionError(f"{type_text} accepted {value!r}")


def test_any_accepts_everything_and_optional_accepts_none():
    assert validate("Any", {"anything": 1}, SCHEMAS) == {"anything": 1}
    assert validate("String?", None, SCHEMAS) is None
    assert validate("String?", "x", SCHEMAS) == "x"


def test_a_schema_is_nominal_and_closed():
    value = validate("RepoFile", {"path": "a", "text": "b"}, SCHEMAS)
    assert isinstance(value, SchemaValue)
    assert value.name == "RepoFile"
    assert value.to_json() == {"path": "a", "text": "b"}


def test_a_missing_field_fails_and_names_it():
    try:
        validate("RepoFile", {"path": "a"}, SCHEMAS, error=InvalidHostResult)
    except InvalidHostResult as error:
        assert "missing field(s) text" in str(error)
    else:
        raise AssertionError("a missing field was accepted")


def test_an_undeclared_field_fails_instead_of_being_dropped():
    try:
        validate("RepoFile", {"path": "a", "text": "b", "token": "s"}, SCHEMAS,
                 error=InvalidHostResult)
    except InvalidHostResult as error:
        assert "undeclared field(s) token" in str(error)
    else:
        raise AssertionError("an undeclared field was silently dropped")


def test_a_nested_list_names_the_index_that_failed():
    payload = {"files": [{"path": "a", "text": "b"}, {"path": 2, "text": "c"}], "note": None}
    try:
        validate("Stats", payload, SCHEMAS, error=InvalidHostResult)
    except InvalidHostResult as error:
        assert "Stats.files[1].path" in str(error)
    else:
        raise AssertionError("a bad list element was accepted")


def test_a_nominal_type_is_not_interchangeable_with_a_lookalike():
    other = SchemaValue("Other", {"path": "a", "text": "b"})
    try:
        validate("RepoFile", other, SCHEMAS, error=InvalidHostResult)
    except InvalidHostResult as error:
        assert "must be RepoFile, got Other" in str(error)
    else:
        raise AssertionError("a different nominal type was accepted")


def test_an_undeclared_schema_name_is_a_failure_not_a_pass():
    try:
        validate("Mystery", {"a": 1}, SCHEMAS, error=InvalidHostResult)
    except InvalidHostResult as error:
        assert "no schema named 'Mystery'" in str(error)
    else:
        raise AssertionError("an unknown type was accepted")


def test_type_text_splits_nested_generics():
    assert split_type("List<RepoFile>") == ("List", ["RepoFile"])
    assert split_type("Map<String, List<RepoFile>>") == ("Map", ["String", "List<RepoFile>"])
    assert split_type("String") == ("String", [])


def test_truthiness_follows_the_language_not_python_truthiness():
    assert truthy(True) is True
    assert truthy(False) is False
    assert truthy(SchemaValue("RepoFile", {"path": "a", "text": ""})) is True
    assert truthy("") is False
