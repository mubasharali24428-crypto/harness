"""W4 slice 3: reviewed MCP servers — the boundary is the config validator.

An MCP server is a process the confined worker spawns, so the rules here are the
same shape as the shell gate's: the list is operator-reviewed input, it is
validated entry by entry, and anything questionable is refused rather than
degraded. A credential-bearing header, a non-https URL, or any env at all on a
spawned server fails the parse — and the run — instead of quietly widening it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_orchestrator import mcp
from harness_orchestrator.engine import DeepSeekHarnessWrapper

from .test_run_isolation_and_stop_truth import _fake_sdk

STUB = {
    "transport": "stdio",
    "serverName": "harness-stub",
    "command": "python",
    "args": ["-m", "harness_orchestrator.mcp_stub"],
    "cwd": "/tmp",
}


def _write_servers(tmp_path, servers):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ validation


def test_a_clean_stdio_server_passes_and_gets_startup_failure_made_loud():
    config = mcp.validate_server({**STUB, "failOnStartupError": False}, 0)
    assert config["command"] == "python"
    # the file cannot turn the loud failure off: a listed server that does not
    # come up must end the run, not leave the agent silently tool-poor
    assert config["failOnStartupError"] is True


def test_env_on_a_spawned_server_is_refused():
    with pytest.raises(ValueError, match="no credentials"):
        mcp.validate_server({**STUB, "env": {"OPENAI_API_KEY": "sk-test"}}, 0)


def test_credential_bearing_headers_are_refused():
    entry = {
        "transport": "streamable-http",
        "serverName": "remote",
        "url": "https://mcp.example.com/rpc",
        "headers": {"Authorization": "Bearer tok"},
    }
    with pytest.raises(ValueError, match="credential"):
        mcp.validate_server(entry, 0)


def test_plain_http_and_unknown_keys_and_bad_names_are_refused():
    with pytest.raises(ValueError, match="https"):
        mcp.validate_server(
            {"transport": "streamable-http", "serverName": "x", "url": "http://a/b"}, 0
        )
    with pytest.raises(ValueError, match="unknown keys"):
        mcp.validate_server({**STUB, "dockerImage": "ubuntu"}, 0)
    with pytest.raises(ValueError, match="serverName"):
        mcp.validate_server({**STUB, "serverName": "../evil"}, 0)
    with pytest.raises(ValueError, match="transport"):
        mcp.validate_server({**STUB, "transport": "unix"}, 0)


def test_a_declared_list_that_cannot_be_read_fails_the_run(tmp_path):
    with pytest.raises(mcp.McpConfigError, match="does not exist"):
        mcp.reviewed_servers({mcp.SERVERS_ENV: str(tmp_path / "absent.json")})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(mcp.McpConfigError, match="not valid JSON"):
        mcp.reviewed_servers({mcp.SERVERS_ENV: str(bad)})
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({"servers": [STUB, STUB]}), encoding="utf-8")
    with pytest.raises(mcp.McpConfigError, match="duplicate"):
        mcp.reviewed_servers({mcp.SERVERS_ENV: str(dup)})


def test_no_declared_list_means_no_servers():
    assert mcp.reviewed_servers({}) == []


# ------------------------------------------------------------------ mounting


def _patch_for(tmp_path, monkeypatch, env):
    seen = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv(mcp.SERVERS_ENV, env)
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"), session_root=str(tmp_path / "s"),
        isolated=False, live_mode="dry",
    )
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    return Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")


def _mcp_config_of(patch: str, server_id: str) -> dict:
    for index, line in enumerate(patch.splitlines()):
        if line.strip() == f"- id: {server_id}":
            for candidate in patch.splitlines()[index:]:
                if candidate.strip().startswith("config: "):
                    return json.loads(candidate.split("config: ", 1)[1])
    raise AssertionError(f"{server_id} not mounted")


def test_reviewed_servers_are_mounted_as_the_bundled_client_plugin(tmp_path, monkeypatch):
    path = _write_servers(tmp_path, [STUB])
    patch = _patch_for(tmp_path, monkeypatch, path)
    assert "name: '@deepseek-ai/dsh-mcp-client'" in patch
    config = _mcp_config_of(patch, "mcp-harness-stub")
    assert config["args"] == ["-m", "harness_orchestrator.mcp_stub"]
    assert config["failOnStartupError"] is True
    assert "env" not in config


def test_an_unmounted_list_changes_nothing_but_a_bad_one_fails_the_run(tmp_path, monkeypatch):
    patch = _patch_for(tmp_path, monkeypatch, "")
    assert "dsh-mcp-client" not in patch

    # a declared-but-invalid list must not degrade into "no servers": the run
    # fails, and the reason reaches the operator (scrubbed, but present)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"servers": [{**STUB, "env": {"X": "1"}}]}), encoding="utf-8")
    seen = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv(mcp.SERVERS_ENV, str(bad))
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws2"), session_root=str(tmp_path / "s2"),
        isolated=False, live_mode="dry",
    )
    text, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "error"
    assert "credentials" in text
    assert seen == []  # the SDK was never constructed with a half-applied boundary


def test_the_plan_cannot_reach_the_mcp_list(tmp_path, monkeypatch):
    """shell_policy is the only run-shaped input the patch reads, and it has no
    MCP keys: a plan asking for servers gets none."""
    path = _write_servers(tmp_path, [STUB])
    seen = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv(mcp.SERVERS_ENV, path)
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"), session_root=str(tmp_path / "s"),
        isolated=False, live_mode="dry",
    )
    wrapper.set_shell_policy("run-1", {"mcpServers": [{"command": "curl"}]})
    wrapper._run_live_events("goal", "run-1")
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    assert "curl" not in patch  # only the reviewed list is mounted
    assert _mcp_config_of(patch, "mcp-harness-stub")["command"] == "python"
