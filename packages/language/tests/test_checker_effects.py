"""Effect tests: declaration, awaiting, purity and the ambient wall.

Each test states one rule of the effect model and asserts the rule fires at the
call site, because "somewhere in this file" is not a position a reviewer can use.
"""

from __future__ import annotations

from support import check, codes

WORKFLOW = (
    "schema Evidence {\n"
    "  passed: Bool\n"
    "  detail: String\n"
    "}\n"
    "workflow w(input: String) -> Evidence {\n"
    "  requires tool.test\n"
    "  budget { steps: 5 }\n"
    "  let e = await verify.tests(input, suite: \"unit\")\n"
    "  return e\n"
    "}\n"
)


def test_declared_and_awaited_effect_is_clean():
    assert check(WORKFLOW).diagnostics == []


def test_undeclared_capability_is_an_error_at_the_call():
    source = WORKFLOW.replace("requires tool.test", "requires repo.read")
    result = check(source)
    assert "HX4001" in codes(result)
    record = next(item for item in result.diagnostics if item.code == "HX4001")
    assert record.line == 8
    assert "tool.test" in record.message


def test_an_unawaited_effect_is_rejected():
    source = WORKFLOW.replace("let e = await verify.tests", "let e = verify.tests")
    result = check(source)
    assert codes(result) == ["HX4005"]


def test_an_effect_cannot_run_in_a_pure_fn():
    source = (
        "schema RepoFile {\n  path: String\n  text: String\n}\n"
        "fn read_it() -> RepoFile {\n"
        "  let file = await repo.read(\"README.md\")\n"
        "  return file\n"
        "}\n"
    )
    result = check(source)
    assert codes(result) == ["HX4003"]


def test_ambient_operations_are_named_not_merely_unknown():
    for snippet in ('exec("ls")', 'open("/etc/passwd")', 'http.get("x")'):
        source = WORKFLOW.replace(
            'let e = await verify.tests(input, suite: "unit")', f"let e = {snippet}"
        )
        result = check(source)
        assert "HX4004" in codes(result), snippet
        record = next(item for item in result.diagnostics if item.code == "HX4004")
        assert "ambient host operation" in record.message


def test_the_ambient_wall_holds_in_the_core_profile_too():
    source = 'fn f() -> Bool {\n  let x = open("x")\n  return true\n}\n'
    assert "HX4004" in codes(check(source, profile="core"))


def test_source_cannot_grant_itself_authority():
    source = 'fn f() -> Bool {\n  let ok = policy.grant("repo.write")\n  return ok\n}\n'
    result = check(source)
    assert codes(result) == ["HX4006"]
    assert "host policy decides" in result.diagnostics[0].message


def test_the_capability_list_is_closed_and_never_includes_approval():
    source = WORKFLOW.replace("requires tool.test", "requires approve")
    result = check(source)
    assert "HX4002" in codes(result)
    message = next(item.message for item in result.diagnostics if item.code == "HX4002")
    assert "approve" not in message.split("(known:")[1]


def test_the_review_bundle_is_pure_and_requests_nothing():
    source = WORKFLOW.replace(
        'let e = await verify.tests(input, suite: "unit")',
        'let e = await verify.tests(input, suite: "unit")\n  let bundle = review.bundle(e, e)',
    ).replace("return e", "return bundle")
    result = check(source)
    # review.bundle returns ReviewBundle, which this program does not declare.
    assert "HX3001" in codes(result)
    assert "ReviewBundle" in next(
        item.message for item in result.diagnostics if item.code == "HX3001"
    )


def test_a_spawned_task_must_be_awaited():
    source = WORKFLOW.replace(
        'let e = await verify.tests(input, suite: "unit")',
        'let task = spawn verify.tests(input, suite: "unit")\n  let e = await task',
    ).replace("budget { steps: 5 }", "budget { steps: 5; tasks: 1 }")
    assert check(source).diagnostics == []
    unawaited = source.replace(
        "let e = await task", 'let e = await verify.tests(input, suite: "unit")'
    )
    assert "HX4007" in codes(check(unawaited))


def test_spawning_more_tasks_than_the_budget_allows_is_an_error():
    source = WORKFLOW.replace(
        "budget { steps: 5 }", "budget { steps: 5; tasks: 1 }"
    ).replace(
        'let e = await verify.tests(input, suite: "unit")',
        'let a = spawn verify.tests(input, suite: "unit")\n'
        '  let b = spawn verify.tests(input, suite: "unit")\n'
        "  let first = await a\n"
        "  let e = await b",
    )
    result = check(source)
    assert codes(result) == ["HX5005"]
