"""Stdio MCP servers start only through an OS confiner (Section 5 confine_argv), never directly."""

import importlib.util
import sys
from pathlib import Path

import pytest

from harness_extensions.config import Settings
from harness_extensions.mcp_client import ExternalMcpClient, StdioRefusedError

REPO = Path(__file__).resolve().parents[3]
PROBE_SERVER = Path(__file__).resolve().parent / "stdio_probe_server.py"


def _load_section5_confinement():
    """Section 5 owns the OS mechanism; load it by path so these tests need no sandbox install."""
    path = REPO / "section5" / "src" / "harness_sandbox" / "confinement.py"
    spec = importlib.util.spec_from_file_location("harness_sandbox_confinement", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


confinement = _load_section5_confinement()
requires_confinement = pytest.mark.skipif(
    not (confinement.seatbelt_available() or confinement.landlock_supported()),
    reason="no OS confinement on this host",
)


async def test_stdio_discovery_refuses_without_a_confiner():
    client = ExternalMcpClient(Settings())
    with pytest.raises(StdioRefusedError):
        await client.discover_stdio(sys.executable, [str(PROBE_SERVER)], confine=None)


async def test_a_confiner_refusal_stops_the_launch():
    client = ExternalMcpClient(Settings())

    def refuse(argv):
        raise RuntimeError("no sandbox on this host")

    with pytest.raises(RuntimeError, match="no sandbox"):
        await client.discover_stdio(sys.executable, [str(PROBE_SERVER)], confine=refuse)


@requires_confinement
async def test_stdio_server_lists_its_tools_through_the_confiner(tmp_path):
    client = ExternalMcpClient(Settings())
    workspace = tmp_path / "ws"
    workspace.mkdir()
    wrapped: list[list[str]] = []

    def confine(argv):
        wrapped.append(list(argv))
        return confinement.confine_argv(argv, workspace=workspace, tmpdir=tmp_path / "tmp")

    result = await client.discover_stdio(sys.executable, [str(PROBE_SERVER)], confine=confine)
    assert wrapped == [[sys.executable, str(PROBE_SERVER)]]
    assert [tool.name for tool in result.tools] == ["ping"]
