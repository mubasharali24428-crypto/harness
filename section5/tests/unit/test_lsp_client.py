"""W4/W7: a spawned language server is bounded, scrubbed, killable and usable.

The "usable" half (W7) is what the harness actually needs from LSP: diagnostics
for a document after ``didOpen``, document symbols, definitions and references —
each with the same deadline/byte/process limits as the W4 probes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness_sandbox.lsp_client import (
    LspError,
    LspOutputTooLarge,
    LspSession,
    LspTimeout,
    file_uri,
    language_id_for,
    main,
    scrubbed_env,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "lsp_fixture_server.py"
COMMAND = [sys.executable, "-I", str(FIXTURE)]


def _session(tmp_path: Path, **kwargs) -> LspSession:
    return LspSession(COMMAND, cwd=tmp_path, timeout=kwargs.pop("timeout", 5.0), **kwargs)


def test_initialize_and_definition(tmp_path):
    with _session(tmp_path) as session:
        initialized = session.initialize()
        assert initialized["capabilities"]["definitionProvider"] is True
        result = session.request("textDocument/definition", {
            "textDocument": {"uri": "file:///work/pkg/retry.py"},
            "position": {"line": 0, "character": 0},
        })
        assert result["uri"] == "file:///work/pkg/retry.py"
        assert result["range"]["start"]["line"] == 0


def test_references_return_every_location(tmp_path):
    with _session(tmp_path) as session:
        session.initialize()
        result = session.request("textDocument/references", {})
        assert [item["uri"] for item in result] == [
            "file:///work/pkg/retry.py",
            "file:///work/pkg/jobs.py",
        ]


def test_a_silent_server_hits_the_deadline_and_is_killed(tmp_path):
    session = _session(tmp_path, timeout=0.5)
    try:
        session.initialize()
        with pytest.raises(LspTimeout):
            session.request("test/slow", {}, timeout=0.5)
        assert session.returncode is not None, "a timed-out server must not survive"
    finally:
        session.close()


def test_an_oversized_frame_is_refused_before_it_is_buffered(tmp_path):
    session = _session(tmp_path, max_bytes=64_000)
    try:
        session.initialize()
        with pytest.raises(LspOutputTooLarge):
            session.request("test/flood")
        assert session.returncode is not None
    finally:
        session.close()


def test_the_server_gets_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("HARNESS_WORKER_DAEMON_TOKEN", "token-should-not-leak")
    with _session(tmp_path) as session:
        session.initialize()
        keys = set(session.request("test/env", {})["keys"])
    assert not [k for k in keys if "DEEPSEEK" in k or "HARNESS" in k]
    assert "PATH" in keys


def test_credential_shaped_overrides_are_refused(tmp_path):
    with pytest.raises(LspError):
        LspSession(COMMAND, cwd=tmp_path, env={"DEEPSEEK_API_KEY": "x"})
    with pytest.raises(LspError):
        LspSession(COMMAND, cwd=tmp_path, env={"ARBITRARY": "x"})
    assert scrubbed_env({"PYTHONUNBUFFERED": "1"})["PYTHONUNBUFFERED"] == "1"


def test_the_server_runs_in_the_requested_directory(tmp_path):
    with _session(tmp_path) as session:
        session.initialize()
        assert Path(session.request("test/cwd", {})["cwd"]).resolve() == tmp_path.resolve()


def test_close_ends_the_process(tmp_path):
    session = _session(tmp_path)
    session.initialize()
    session.close()
    assert session.returncode is not None


# ------------------------------------------------------------ W7: real use


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_diagnostics_arrive_after_open(tmp_path):
    source = _write(tmp_path, "broken.py", "def f():\n    return undefined_name\n")
    with _session(tmp_path) as session:
        session.initialize()
        uri = session.open_document(source)
        assert uri == file_uri(source)
        diagnostics = session.diagnostics(uri, timeout=2.0)
    assert len(diagnostics) == 1
    assert diagnostics[0]["severity"] == 1
    assert diagnostics[0]["range"]["start"]["line"] == 1
    assert "undefined_name" in diagnostics[0]["message"]


def test_a_clean_document_publishes_an_empty_list(tmp_path):
    source = _write(tmp_path, "clean.py", "VALUE = 1\n")
    with _session(tmp_path) as session:
        session.initialize()
        uri = session.open_document(source)
        assert session.diagnostics(uri, timeout=2.0) == []


def test_diagnostics_are_matched_to_their_own_document(tmp_path):
    first = _write(tmp_path, "a.py", "undefined_name\n")
    second = _write(tmp_path, "b.py", "x = 1\ny = 2\nundefined_name\n")
    with _session(tmp_path) as session:
        session.initialize()
        uri_a = session.open_document(first)
        uri_b = session.open_document(second)
        # Ask for the second file first: the first file's notification must be
        # queued, not consumed as if it were the answer.
        diag_b = session.diagnostics(uri_b, timeout=2.0)
        diag_a = session.diagnostics(uri_a, timeout=2.0)
    assert [d["range"]["start"]["line"] for d in diag_b] == [2]
    assert [d["range"]["start"]["line"] for d in diag_a] == [0]


def test_document_symbols_list_top_level_definitions(tmp_path):
    source = _write(
        tmp_path,
        "policy.py",
        "class RetryPolicy:\n    pass\n\ndef backoff(attempt):\n    return attempt\n",
    )
    with _session(tmp_path) as session:
        session.initialize()
        uri = session.open_document(source)
        symbols = session.document_symbols(uri)
    assert [item["name"] for item in symbols] == ["RetryPolicy", "backoff"]
    assert [item["kind"] for item in symbols] == [5, 12]


def test_definition_and_references_use_the_opened_document(tmp_path):
    source = _write(tmp_path, "caller.py", "from pkg.retry import backoff\n")
    with _session(tmp_path) as session:
        session.initialize()
        uri = session.open_document(source)
        definition = session.definition(uri, 0, 24)
        references = session.references(uri, 0, 24)
    assert definition["uri"] == "file:///work/pkg/retry.py"
    assert [item["uri"] for item in references] == [
        "file:///work/pkg/retry.py",
        "file:///work/pkg/jobs.py",
    ]


def test_server_requests_are_answered_so_real_servers_do_not_block(tmp_path):
    with _session(tmp_path) as session:
        initialized = session.initialize()
    assert initialized["serverInfo"]["clientAnswered"] is True


def test_an_endless_notification_stream_kills_the_server(tmp_path):
    session = _session(tmp_path)
    try:
        session.initialize()
        with pytest.raises(LspError):
            session.request("test/notifyFlood")
        assert session.returncode is not None
    finally:
        session.close()


def test_uri_and_language_helpers(tmp_path):
    path = _write(tmp_path, "a file.py", "x = 1\n")
    assert file_uri(path).startswith("file:///")
    assert "%20" in file_uri(path)
    assert language_id_for(path) == "python"
    assert language_id_for("notes.rs") == "rust"
    assert language_id_for("Makefile") == "plaintext"


def test_cli_scenario_mode_runs_one_operation(tmp_path, capsys):
    source = _write(tmp_path, "policy.py", "def backoff(attempt):\n    return attempt\n")
    code = main(["--cwd", str(tmp_path), "--command", json.dumps(COMMAND),
                 "--scenario", "documentSymbol", "--file", str(source)])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == 0
    assert [item["name"] for item in payload["result"]["symbols"]] == ["backoff"]
    assert payload["initialize"]["serverInfo"]["clientAnswered"] is True


def test_cli_requires_exactly_one_mode(tmp_path, capsys):
    assert main(["--cwd", str(tmp_path), "--command", json.dumps(COMMAND)]) == 2
    assert main(["--cwd", str(tmp_path), "--command", json.dumps(COMMAND),
                 "--scenario", "diagnostics", "--method", "test/env"]) == 2
    assert main(["--cwd", str(tmp_path), "--command", json.dumps(COMMAND),
                 "--scenario", "diagnostics"]) == 2
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["error"]["code"] for line in lines] == ["Usage"] * 3
