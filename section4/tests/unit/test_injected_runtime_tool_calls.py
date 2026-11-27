"""Checkpoint-1 faithful flow: an injected runtime reports its tool calls like the SDK does.

``execute_goal`` folds a mapping result's ``events`` through the same ``_fold_tool_calls``
a live run uses, so ``record_engine_tool_calls`` writes them and I-09 reads them. The
evidence stays honest: only a recorded exit 0 is green; a red run, a refusal, or no
report at all never is. A plain string result keeps the historical shape.
"""

import json

import pytest

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.review_summary import verification_evidence


@pytest.fixture(autouse=True)
def _no_repo_context(monkeypatch):
    # Pin goal pass-through (N02 appends the workspace baseline on every path).
    monkeypatch.setenv("HARNESS_REPO_CONTEXT", "0")


def _bash(call_id: str, command: str, content: str | None, *, is_error: bool = False):
    call = {"type": "tool/call", "data": {
        "callId": call_id, "name": "bash", "arguments": json.dumps({"command": command})}}
    message = {"toolCallId": call_id, "isError": is_error}
    if content is not None:
        message["content"] = content
    return [call, {"type": "tool/result", "data": {"message": message}}]


def _run(result):
    return DeepSeekHarnessWrapper(backend_runner=lambda goal: result).execute_goal("refactor")


def test_mapping_result_folds_events_like_a_live_run():
    events = _bash("c1", "python -m pytest -q", "1 passed\n[exit code: 0]")
    out = _run({"finalResponse": "rewrote calc.py", "events": events})
    assert out["status"] == "completed" and out["engine"] == "injected"
    assert out["finalResponse"] == "rewrote calc.py"
    assert out["toolCalls"] == DeepSeekHarnessWrapper._fold_tool_calls(events) == [
        {"callId": "c1", "tool": "bash", "target": "python -m pytest -q",
         "outcome": "ok", "exitCode": 0}
    ]
    assert verification_evidence(out["toolCalls"])["green"] is True


def test_string_result_keeps_the_historical_shape():
    out = _run("ran:refactor")
    assert out["finalResponse"] == "ran:refactor"
    assert "toolCalls" not in out


def test_mapping_without_events_reports_no_calls_and_no_evidence():
    out = _run({"finalResponse": "rewrote calc.py"})
    assert out["finalResponse"] == "rewrote calc.py" and "toolCalls" not in out
    # Nothing recorded is unknown, never a pass (I-09 / reviewer integrity).
    assert verification_evidence(out.get("toolCalls"))["green"] is False


def test_a_red_test_run_is_recorded_red():
    events = _bash("c1", "pytest -q", "1 failed\n[exit code: 1]")
    out = _run({"finalResponse": "x", "events": events})
    assert out["toolCalls"][0]["exitCode"] == 1
    evidence = verification_evidence(out["toolCalls"])
    assert evidence["green"] is False and evidence["failed"] is True


def test_a_refused_test_run_never_counts_as_green():
    # A policy refusal: error result, no exit marker -> the command did not run.
    out = _run({"finalResponse": "x", "events": _bash("c1", "pytest -q", None, is_error=True)})
    assert out["toolCalls"][0]["outcome"] == "error" and "exitCode" not in out["toolCalls"][0]
    assert verification_evidence(out["toolCalls"])["green"] is False


def test_red_then_green_on_the_same_command_is_green():
    events = _bash("c1", "pytest -q", "[exit code: 1]") + _bash("c2", "pytest -q", "[exit code: 0]")
    evidence = verification_evidence(_run({"finalResponse": "x", "events": events})["toolCalls"])
    assert evidence["green"] is True and evidence["red_to_green"] is True
