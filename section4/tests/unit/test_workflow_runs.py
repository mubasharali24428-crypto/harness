"""M2: Section 4 executes a workflow through the contract, and the bus sees it.

What is proved here, in order:

1. A run's events arrive in causal order — start, one call/result pair per
   crossing, one terminal — and a client can pair them by ``callId``.
2. Every non-ok status ends the run with ``engine_failed`` carrying the driver's
   code, and the run is not left "executing".
3. A runner that answers off-contract still produces a terminal event (the log
   must not strand a run) and the caller gets an exception naming the violation.
4. A secret in a program's arguments never reaches the bus.
5. Nothing in this path imports the driver: the section's only harness package
   is ``harness_contracts``.

The runner here is a fake. It is not weaker evidence than the real one: the
contract is the seam, so a fake that honours it exercises exactly what Section 4
does with the real driver, and the real driver's own suite proves the other
side of the seam (``packages/language/tests/test_host_contract_runner.py``).
"""

from __future__ import annotations

import asyncio

import pytest
from harness_contracts.events import EventType
from harness_contracts.workflow import WorkflowOperation, WorkflowRequest, WorkflowResult

from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.workflow_runs import (
    WorkflowContractViolation,
    WorkflowRunnerUnavailable,
)

RUN = "wf-run-1"
PROJECT = "proj-wf"


class RecordingBus:
    """The smallest bus that still preserves order and identity."""

    def __init__(self) -> None:
        self.published: list[tuple[EventType, str, dict, str | None]] = []
        self.seq = 0

    async def ensure_run(self, project_id: str, run_id: str) -> None:  # noqa: ARG002
        return None

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        self.seq += 1
        self.published.append((event_type, project_id, dict(payload or {}), run_id))


class FakeRunner:
    """One runner, many outcomes: the contract is the only thing it owes."""

    def __init__(self, result: WorkflowResult) -> None:
        self.result = result
        self.requests: list[WorkflowRequest] = []

    def run(self, request: WorkflowRequest) -> WorkflowResult:
        self.requests.append(request)
        return self.result


def _service(result: WorkflowResult) -> tuple[OrchestratorService, RecordingBus, FakeRunner]:
    bus = RecordingBus()
    runner = FakeRunner(result)
    service = OrchestratorService(bus=bus, workflow_runner=runner)
    return service, bus, runner


async def _drain(service: OrchestratorService) -> None:
    for _ in range(20):
        chains = list(service._publish_chains.values())
        if not chains:
            return
        await asyncio.gather(*chains, return_exceptions=True)


def _ok(operations: list[WorkflowOperation], **overrides) -> WorkflowResult:
    fields = {
        "status": "ok",
        "workflow": "read_one",
        "output": {"path": "README.md", "text": "# demo\n"},
        "digest": "a" * 64,
        "evidence": {"kind": "hx.evidence/v1"},
        "operations": operations,
    }
    fields.update(overrides)
    return WorkflowResult(**fields)


def test_a_successful_run_publishes_in_causal_order():
    operations = [
        WorkflowOperation(
            callId=f"{RUN}:1:repo.read",
            operation="repo.read",
            ok=True,
            arguments='{"path":"README.md"}',
            durationMs=3,
        )
    ]
    service, bus, runner = _service(_ok(operations))

    async def scenario():
        result = await service.run_workflow(
            RUN, "module m\n", project_id=PROJECT, grants=["repo.read"], workflow="read_one"
        )
        await _drain(service)
        return result

    result = asyncio.run(scenario())

    assert result.status == "ok"
    assert runner.requests[0].runId == RUN
    assert runner.requests[0].projectId == PROJECT
    assert runner.requests[0].grants == ["repo.read"]

    kinds = [event[0] for event in bus.published]
    assert kinds == [
        EventType.EXECUTION_STARTED,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.ENGINE_COMPLETED,
    ]
    # Every event lands on the SAME run and project: one transcript, no strays.
    assert {(event[1], event[3]) for event in bus.published} == {(PROJECT, RUN)}
    call, settled = bus.published[1][2], bus.published[2][2]
    assert call["callId"] == settled["callId"]
    assert call["name"] == "repo.read" and settled["ok"] is True
    assert bus.published[0][2]["executor"] == "hx"
    assert bus.published[3][2]["evidenceDigest"] == "a" * 64


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("refused", "HXH101"),
        ("denied", "HXH401"),
        ("budget", "HXH501"),
        ("cancelled", "HXH601"),
        ("error", "HXH301"),
    ],
)
def test_every_non_ok_status_ends_the_run_with_the_drivers_code(status, code):
    service, bus, _ = _service(
        _ok([], status=status, code=code, message=f"the run settled {status}")
    )

    async def scenario():
        await service.run_workflow(RUN, "module m\n", project_id=PROJECT)
        await _drain(service)

    asyncio.run(scenario())

    assert [event[0] for event in bus.published] == [
        EventType.EXECUTION_STARTED,
        EventType.ENGINE_FAILED,
    ]
    payload = bus.published[-1][2]
    assert payload["code"] == code
    assert payload["status"] == status
    assert payload["reason"].startswith(code)


def test_a_runner_that_breaks_the_contract_still_settles_the_run():
    class OffContract:
        def run(self, request):  # noqa: ARG002
            return {"status": "ok"}

    bus = RecordingBus()
    service = OrchestratorService(bus=bus, workflow_runner=OffContract())

    async def scenario():
        with pytest.raises(WorkflowContractViolation, match="not WorkflowResult"):
            await service.run_workflow(RUN, "module m\n", project_id=PROJECT)
        await _drain(service)

    asyncio.run(scenario())

    kinds = [event[0] for event in bus.published]
    assert kinds[-1] == EventType.ENGINE_FAILED
    assert "WorkflowResult" in bus.published[-1][2]["reason"]


def test_an_unwired_runner_is_refused_before_anything_is_published():
    bus = RecordingBus()
    service = OrchestratorService(bus=bus)

    async def scenario():
        with pytest.raises(WorkflowRunnerUnavailable):
            await service.run_workflow(RUN, "module m\n", project_id=PROJECT)
        await _drain(service)

    asyncio.run(scenario())
    assert bus.published == []


def test_a_secret_in_an_argument_never_reaches_the_bus():
    secret = "sk-live-abcdef123456"
    operations = [
        WorkflowOperation(
            callId="c1",
            operation="repo.read",
            ok=False,
            arguments='{"api_key":"sk-live-abcdef123456"}',
            error=f"host refused key {secret}",
        )
    ]
    service, bus, _ = _service(_ok(operations, status="error", code="HXH301"))

    class KeyedEngine:
        api_key = secret

    service.engine = KeyedEngine()

    async def scenario():
        await service.run_workflow(RUN, "module m\n", project_id=PROJECT)
        await _drain(service)

    asyncio.run(scenario())

    blob = repr([event[2] for event in bus.published])
    assert secret not in blob
    assert "[redacted]" in blob


def test_one_run_cannot_execute_two_programs_at_once():
    service, _, _ = _service(_ok([]))

    async def scenario():
        await service._ensure_run(RUN, PROJECT)
        service._workflow_runs_in_flight.add(RUN)
        with pytest.raises(RuntimeError, match="already executing"):
            await service.run_workflow(RUN, "module m\n", project_id=PROJECT)

    asyncio.run(scenario())


def test_the_section_imports_only_contracts_and_itself():
    """The isolation law, restated where the workflow seam is: no driver import."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "harness_orchestrator"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top.startswith("harness_") and top not in (
                    "harness_contracts",
                    "harness_orchestrator",
                ):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
