"""Bridge tests: one door, and every implementation of it refuses the same things.

``SandboxHostBridge`` is the production shape — it speaks Section 5's settled
``exec_structured`` result and Section 2's repository protocol, and refuses
anything this host has no adapter for. ``ReadOnlyRepoBridge`` is the local
shape: reads inside one root, patches as proposals, everything else refused.
Both are tested without a container, a network or a model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_hx_host.bridge import (
    ConfinedCommandRunner,
    FakeHostBridge,
    ReadOnlyRepoBridge,
    SandboxHostBridge,
)
from harness_hx_host.errors import HostUnavailable, InvalidHostResult


class SyncSandbox:
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        self.commands: list[str] = []

    def exec_structured(self, handle: object, command: str) -> object:
        self.commands.append(command)
        return SimpleNamespace(exit_code=self.exit_code, summary="1 passed")


class AsyncSandbox(SyncSandbox):
    async def exec_structured(self, handle: object, command: str) -> object:  # type: ignore[override]
        self.commands.append(command)
        return SimpleNamespace(exit_code=self.exit_code, summary="async")


class StreamingOnlySandbox:
    def exec(self, handle: object, command: str) -> object:
        return iter(["line\n"])


class StubRepo:
    def read_file(self, project_id: str, ref: str, path: str) -> str:
        return f"# {path} @ {ref}\n"

    def read_repo_tree(self, project_id: str, ref: str) -> list[dict[str, str]]:
        return [{"path": "README.md"}, {"path": "docs/map.md"}]


def test_the_fake_bridge_records_every_crossing_in_order():
    bridge = FakeHostBridge(agents={"developer": {"summary": "s", "patch": "d"}})
    bridge.read_file("README.md")
    bridge.list_files("docs/")
    bridge.run_tests({"patch": "d"}, "unit")
    assert [call.operation for call in bridge.calls] == ["repo.read", "repo.list", "verify.tests"]


def test_a_patch_is_a_proposal_and_its_id_is_content_addressed():
    bridge = FakeHostBridge()
    first = bridge.propose_patch({"summary": "s", "patch": "--- a\n+++ b\n"})
    second = bridge.propose_patch({"summary": "s", "patch": "--- a\n+++ b\n"})
    assert first == second
    assert first["id"].startswith("patch-")
    assert first["diff"] == "--- a\n+++ b\n"


def test_sandbox_bridge_turns_a_settled_exit_code_into_evidence():
    sandbox = SyncSandbox(exit_code=0)
    bridge = SandboxHostBridge(
        confined=ConfinedCommandRunner(sandbox, handle=object()),
        test_commands={"unit": "pytest -q tests/unit"},
    )
    assert bridge.run_tests({}, "unit") == {"passed": True, "detail": "exit 0: 1 passed"}
    assert sandbox.commands == ["pytest -q tests/unit"]


def test_sandbox_bridge_reports_failure_as_evidence_not_as_a_verdict():
    bridge = SandboxHostBridge(
        confined=ConfinedCommandRunner(SyncSandbox(exit_code=1), handle=object()),
        test_commands={"unit": "pytest -q"},
    )
    evidence = bridge.run_tests({}, "unit")
    assert evidence["passed"] is False
    assert evidence["detail"].startswith("exit 1")


def test_a_coroutine_structured_exec_is_awaited():
    sandbox = AsyncSandbox(exit_code=0)
    bridge = SandboxHostBridge(
        confined=ConfinedCommandRunner(sandbox, handle=object()),
        test_commands={"unit": "pytest -q"},
    )
    assert bridge.run_tests({}, "unit")["passed"] is True


def test_a_streaming_only_sandbox_is_refused_rather_than_guessing_an_exit_code():
    runner = ConfinedCommandRunner(StreamingOnlySandbox(), handle=object())
    with pytest.raises(HostUnavailable, match="settled exit code"):
        runner.run("pytest -q")


def test_an_unlisted_suite_is_refused_by_name():
    bridge = SandboxHostBridge(confined=None, test_commands={"unit": "pytest -q"})
    with pytest.raises(HostUnavailable, match="no allow-listed test command"):
        bridge.run_tests({}, "integration")


def test_repository_reads_go_through_the_repository_service():
    bridge = SandboxHostBridge(repo=StubRepo(), project_id="p1", ref="main")
    assert bridge.read_file("docs/map.md")["text"] == "# docs/map.md @ main\n"
    assert [entry["path"] for entry in bridge.list_files("docs/")] == ["docs/map.md"]


def test_a_missing_adapter_is_refused_not_invented():
    bridge = SandboxHostBridge()
    for call in (
        lambda: bridge.read_file("README.md"),
        lambda: bridge.run_tests({}, "unit"),
        lambda: bridge.invoke_agent("developer", "plan", {}),
    ):
        with pytest.raises(HostUnavailable):
            call()


def test_the_local_bridge_reads_inside_its_root(tmp_path: Path):
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    bridge = ReadOnlyRepoBridge(root=tmp_path)
    assert bridge.read_file("README.md")["text"] == "# hi\n"
    assert [entry["path"] for entry in bridge.list_files("")] == ["README.md"]


def test_the_local_bridge_refuses_an_escape_and_a_secret_file(tmp_path: Path):
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    (tmp_path / "key.pem").write_text("---\n", encoding="utf-8")
    bridge = ReadOnlyRepoBridge(root=tmp_path)
    with pytest.raises(InvalidHostResult, match="escapes the allowed root"):
        bridge.read_file("../outside.txt")
    with pytest.raises(InvalidHostResult, match="forbidden path"):
        bridge.read_file(".env")
    with pytest.raises(InvalidHostResult, match="forbidden path"):
        bridge.read_file("key.pem")


def test_the_local_bridge_refuses_tests_and_models_by_name(tmp_path: Path):
    bridge = ReadOnlyRepoBridge(root=tmp_path)
    with pytest.raises(HostUnavailable, match="Section 5"):
        bridge.run_tests({}, "unit")
    with pytest.raises(HostUnavailable, match="model relay"):
        bridge.invoke_agent("developer", "plan", {})


def test_an_async_runner_does_not_deadlock_inside_a_running_loop():
    async def main() -> object:
        runner = ConfinedCommandRunner(AsyncSandbox(exit_code=0), handle=object())
        return runner.run("pytest -q")

    assert asyncio.run(main())["passed"] is True
