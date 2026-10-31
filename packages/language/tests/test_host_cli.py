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


def test_a_recorded_run_can_be_replayed_after_the_files_are_gone(tmp_path: Path, capsys):
    """The whole point of `--record`/`--replay`, proven by removing the host's data.

    The second run reads nothing from disk: the repository file has been deleted,
    and the answer still arrives — so the replay demonstrably came from the
    recording rather than from a host it could still reach.
    """
    program = project(tmp_path)
    recording = tmp_path / "run.json"
    assert main(["run", str(program), "--grant", "repo.read", "--record", str(recording)]) == 0
    saved = json.loads(recording.read_text(encoding="utf-8"))
    assert saved["kind"] == "hx.recording/v2"
    assert [e["outcome"] for e in saved["exchanges"]] == ["ok"]

    (tmp_path / "README.md").unlink()
    capsys.readouterr()
    assert main(["run", str(program), "--grant", "repo.read", "--replay", str(recording)]) == 0
    assert "status: ok" in capsys.readouterr().out


def test_recording_a_failing_run_saves_the_failure_too(tmp_path: Path, capsys):
    """A run that failed is the interesting artifact; recording it is not optional."""
    program = tmp_path / "read_missing.hx"
    program.write_text(READ_ONE.replace('"README.md"', '"never-written.md"'), encoding="utf-8")
    recording = tmp_path / "failed.json"
    code = main(["run", str(program), "--grant", "repo.read", "--record", str(recording)])
    assert code == 1, "the live run failed because the file does not exist"
    saved = json.loads(recording.read_text(encoding="utf-8"))
    assert saved["exchanges"][0]["outcome"] == "failure"
    assert saved["exchanges"][0]["failure"]["code"] == "HXH301"

    # Replaying it reproduces the recorded failure even though the file now exists:
    # the answer comes from the recording, not from the filesystem behind it.
    (tmp_path / "never-written.md").write_text("appeared later\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["run", str(program), "--grant", "repo.read", "--replay", str(recording)]) == 1
    assert "[HXH301]" in capsys.readouterr().err, "the recorded failure came back"


def test_replaying_a_v1_recording_is_refused_by_name(tmp_path: Path, capsys):
    program = project(tmp_path)
    stale = tmp_path / "v1.json"
    stale.write_text(json.dumps({"kind": "hx.recording/v1", "exchanges": []}), encoding="utf-8")
    assert main(["run", str(program), "--grant", "repo.read", "--replay", str(stale)]) == 1
    assert "[HXH104]" in capsys.readouterr().err


def test_an_unreadable_recording_is_a_usage_failure_not_a_live_run(tmp_path: Path, capsys):
    """Falling back to the host when the recording cannot be read would be the bug."""
    program = project(tmp_path)
    assert main(["run", str(program), "--grant", "repo.read",
                 "--replay", str(tmp_path / "absent.json")]) == 2
    assert "cannot read the recording" in capsys.readouterr().err


def test_record_and_replay_are_alternatives(tmp_path: Path, capsys):
    program = project(tmp_path)
    both = tmp_path / "both.json"
    assert main(["run", str(program), "--grant", "repo.read",
                 "--record", str(both), "--replay", str(both)]) == 2
    assert "alternatives" in capsys.readouterr().err
