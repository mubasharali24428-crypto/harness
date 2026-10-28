"""W4 spike: a spawned language server is bounded, scrubbed and killable."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from harness_sandbox.lsp_client import (
    LspError,
    LspOutputTooLarge,
    LspSession,
    LspTimeout,
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
