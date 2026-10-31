"""CLI tests: the two commands a person actually types, and their exit codes.

``hxr plan`` answers "may this run here?"; ``hxr run`` runs it locally against a
read-only root and writes evidence. stdout stays machine-readable, diagnostics
go to stderr — the same split ``hxc`` keeps.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_hx_host.cli import main
from support import FIXTURES

FIX_ISSUE = str(FIXTURES / "valid" / "fix_issue.hx")
ALL = "repo.read,repo.propose_patch,tool.test,model.invoke"

READ_ONE = """module demo

schema RepoFile {
  path: String
  text: String
}

workflow read_one() -> RepoFile {
  requires repo.read
  budget { steps: 20; tool_calls: 2; duration: 1m }
  let file = await repo.read("README.md")
  return file
}
"""

TESTS_ONE = """module demo

schema Evidence {
  passed: Bool
  detail: String
}

workflow tests_one() -> Evidence {
  requires tool.test
  budget { steps: 20; tool_calls: 2; duration: 1m }
  let evidence = await verify.tests("origin/main", suite: "unit")
  return evidence
}
"""

BROKEN = """module broken

workflow nope() -> Int {
  return
}
"""


def project(tmp_path: Path, source: str = READ_ONE) -> Path:
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    program = tmp_path / "read_one.hx"
    program.write_text(source, encoding="utf-8")
    return program


def test_plan_without_grants_exits_denied(capsys):
    assert main(["plan", FIX_ISSUE]) == 3
    out = capsys.readouterr().out
    assert "requested: model.invoke, repo.propose_patch, repo.read, tool.test" in out
    assert "granted: nothing" in out
    assert out.count("denied: ") == 4


def test_plan_with_grants_exits_zero(capsys):
    assert main(["plan", FIX_ISSUE, "--grant", ALL]) == 0
    printed = capsys.readouterr().out
    assert "granted: model.invoke, repo.propose_patch, repo.read, tool.test" in printed


def test_plan_json_keeps_stdout_machine_readable(capsys):
    assert main(["plan", FIX_ISSUE, "--grant", ALL, "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["policy"]["allowed"] is True
    assert payload["policy"]["forgeable"] is False
    assert payload["manifest"]["language"] == "hx"
    assert json.loads(captured.err) == []


def test_run_reads_inside_the_root_and_writes_evidence(tmp_path: Path, capsys):
    program = project(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    code = main(
        [
            "run",
            str(program),
            "--grant",
            "repo.read",
            "--json",
            "--evidence",
            str(evidence_path),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    evidence = json.loads(captured.out)
    assert evidence["outcome"]["status"] == "ok"
    assert evidence["outcome"]["output"] == {"path": "README.md", "text": "# hello\n"}
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["source"]["sha256"] == (
        evidence["source"]["sha256"]
    )
    assert captured.err == ""


def test_run_without_a_grant_is_denied_and_says_why(tmp_path: Path, capsys):
    program = project(tmp_path)
    assert main(["run", str(program)]) == 3
    captured = capsys.readouterr()
    assert "status: denied" in captured.out
    assert "[HXH401]" in captured.err


def test_run_on_a_missing_file_is_a_usage_failure(capsys):
    assert main(["run", "/nonexistent/program.hx"]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_run_on_a_program_with_errors_refuses_and_reports_the_codes(tmp_path: Path, capsys):
    program = project(tmp_path, BROKEN)
    assert main(["run", str(program), "--grant", "repo.read"]) == 1
    captured = capsys.readouterr()
    assert "[HX3" in captured.err
    assert "status: refused" in captured.out


def test_run_with_a_wrong_digest_refuses(tmp_path: Path, capsys):
    program = project(tmp_path)
    assert main(["run", str(program), "--grant", "repo.read", "--digest", "0" * 64]) == 1
    assert "[HXH103]" in capsys.readouterr().err


def test_run_reports_a_host_refusal_as_an_error_with_a_code(tmp_path: Path, capsys):
    # The local bridge has no Section 5 sandbox: the program checks, is
    # authorised, and is then refused by the host with a code of its own.
    program = project(tmp_path, TESTS_ONE)
    assert main(["run", str(program), "--grant", "tool.test"]) == 1
    captured = capsys.readouterr()
    assert "[HXH403]" in captured.err
    assert "status: error" in captured.out


def test_inputs_must_be_a_json_object(tmp_path: Path, capsys):
    program = project(tmp_path)
    assert main(["run", str(program), "--grant", "repo.read", "--inputs", "[1]"]) == 2
    assert "must be a JSON object" in capsys.readouterr().err
