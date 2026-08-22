from fixtures.golden_fixtures import GOLDEN_PYTEST_OUTPUT

from harness_sandbox.output_parser import parse_exec_output


def test_parse_pytest_failure():
    result = parse_exec_output(
        command="pytest tests/",
        exit_code=1,
        stdout=GOLDEN_PYTEST_OUTPUT,
        stderr="",
        duration_ms=450,
    )
    assert result.status == "failed"
    assert result.passed_count == 4
    assert result.failed_count == 1
    assert len(result.diagnostics) >= 1
    assert "FAILED test_api.py::test_login" in result.diagnostics[0]


def test_parse_success():
    result = parse_exec_output(
        command="npm test",
        exit_code=0,
        stdout="Tests: 12 passed, 12 total",
        stderr="",
        duration_ms=1200,
    )
    assert result.status == "passed"
    assert result.passed_count == 12
    assert result.failed_count == 0
