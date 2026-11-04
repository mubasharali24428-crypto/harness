"""CLI tests: the exit code is the contract, and stdout is never polluted.

A pipeline reads three things from ``hxc``: the exit code, stderr, and — only
when it asked for one — a manifest on stdout. Each of those is asserted here,
because "it printed something" is not a wire contract.
"""

from __future__ import annotations

import json

from harness_hx.cli import main
from support import FIXTURES

VALID = FIXTURES / "valid" / "fix_issue.hx"
INVALID = FIXTURES / "invalid" / "ambient_exec.hx"
WARN_ONLY = FIXTURES / "warnings" / "no_budget.hx"
CORE = FIXTURES / "core" / "pure_helpers.hx"


def test_a_clean_program_exits_zero_and_says_nothing(capsys):
    assert main(["check", str(VALID)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_an_invalid_program_exits_one_with_records_on_stderr(capsys):
    status = main(["check", str(INVALID)])
    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert "ambient host operation" in captured.err
    assert captured.err.endswith("\n")


def test_warnings_do_not_fail_the_build(capsys):
    assert main(["check", str(WARN_ONLY)]) == 0
    assert "warning" in capsys.readouterr().err


def test_json_mode_is_one_array_on_stderr(capsys):
    assert main(["check", "--json", str(INVALID)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    records = json.loads(captured.err)
    assert isinstance(records, list) and records
    assert set(records[0]) <= {"path", "line", "col", "kind", "message"}
    assert records[0]["path"].endswith("ambient_exec.hx")
    assert records[0]["message"].startswith("[")


def test_json_mode_for_a_clean_program_is_an_empty_array(capsys):
    assert main(["check", "--json", str(VALID)]) == 0
    assert json.loads(capsys.readouterr().err) == []


def test_a_manifest_request_goes_to_stdout_and_leaves_stderr_for_records(capsys):
    assert main(["check", "--manifest", "-", str(VALID)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["policy_request"]["grants"] == "none"
    assert captured.err == ""


def test_a_manifest_file_is_written_not_printed(tmp_path, capsys):
    target = tmp_path / "policy-request.json"
    assert main(["check", "--manifest", str(target), str(VALID)]) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["language"] == "hx"
    assert capsys.readouterr().out == ""


def test_a_manifest_cannot_describe_more_than_one_program(capsys):
    status = main(["check", "--manifest", "-", str(VALID), str(WARN_ONLY)])
    assert status == 2
    assert "exactly one program" in capsys.readouterr().err


def test_an_unreadable_file_is_a_usage_failure_not_a_diagnostic(capsys):
    missing = FIXTURES / "valid" / "absent.hx"
    status = main(["check", str(missing)])
    captured = capsys.readouterr()
    assert status == 2
    assert "cannot read" in captured.err
    assert captured.out == ""


def test_the_worst_program_decides_the_exit_code(capsys):
    assert main(["check", str(VALID), str(INVALID)]) == 1
    capsys.readouterr()


def test_the_profile_flag_is_the_only_way_to_select_core(capsys):
    assert main(["check", str(CORE)]) == 0
    assert main(["check", "--profile", "core", str(CORE)]) == 0
    assert main(["check", "--profile", "core", str(VALID)]) == 1
    capsys.readouterr()


def test_an_unknown_profile_is_a_usage_failure(capsys):
    assert main(["check", "--profile", "turbo", str(VALID)]) == 2
    assert "invalid choice" in capsys.readouterr().err


def test_version_reports_both_versions(capsys):
    assert main(["--version"]) == 0
    assert "hxc 0.4.0 (hx spec 0.4.0)" in capsys.readouterr().out


def test_no_subcommand_is_a_usage_failure(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err
