"""W1 — the live transcript, from the runtime event to the worker's stdout line.

Three layers are pinned here because each one can silently produce an empty
transcript:

* the normaliser, against the runtime's own ``SessionEventMap`` shapes;
* the live callback, including de-duplication against the post-run sweep (a
  runtime may report a call twice, and the UI must not show it twice);
* the worker protocol, NDJSON ``{"progress": …}`` lines followed by one result
  line, which is what makes the parent able to write events mid-run.
"""

import io
import json
import sys
import types

from harness_orchestrator.engine import DeepSeekHarnessWrapper, _notification_events


def _call(call_id="c1", name="bash") -> dict:
    return {
        "type": "tool/call",
        "seq": 3,
        "time": 1000,
        "data": {
            "turn": 1,
            "step": 1,
            "callId": call_id,
            "name": name,
            "arguments": '{"command": "ls -la"}',
        },
    }


def _result(call_id="c1", is_error=False) -> dict:
    return {
        "type": "tool/result",
        "seq": 4,
        "time": 1450,
        "data": {
            "turn": 1,
            "step": 1,
            "message": {
                "role": "tool",
                "toolCallId": call_id,
                "isError": is_error,
                "content": [{"type": "text", "text": "a.txt\nb.txt"}],
            },
        },
    }


def test_a_call_becomes_a_transcript_record_with_its_arguments():
    record = DeepSeekHarnessWrapper._tool_record(_call())
    assert record == {
        "phase": "call",
        "callId": "c1",
        "name": "bash",
        "arguments": '{"command": "ls -la"}',
        "turn": 1,
        "step": 1,
        "at": 1000,
        "seq": 3,
    }


def test_a_result_carries_outcome_and_output_preview():
    ok = DeepSeekHarnessWrapper._tool_record(_result())
    assert ok["phase"] == "result" and ok["ok"] is True
    assert ok["callId"] == "c1" and ok["output"] == "a.txt\nb.txt"
    assert ok["at"] == 1450

    failed = DeepSeekHarnessWrapper._tool_record(_result(is_error=True))
    assert failed["ok"] is False


def test_result_error_reason_survives_when_the_runtime_reports_one():
    event = _result(is_error=True)
    event["data"]["error"] = {"name": "RemoteError", "code": "outside-workspace"}
    record = DeepSeekHarnessWrapper._tool_record(event)
    assert record["ok"] is False and record["error"] == "outside-workspace"


def test_unknown_events_are_ignored_not_guessed():
    assert DeepSeekHarnessWrapper._tool_record({"type": "turn/start", "data": {}}) is None
    assert DeepSeekHarnessWrapper._tool_record({"type": "tool/call", "data": {}}) is None
    assert DeepSeekHarnessWrapper._tool_record("nonsense") is None


def test_notification_payload_shapes_all_unwrap():
    bare = _call()
    wrapped = {"event": bare}
    entries = {"events": [bare]}
    nested = {"data": {"type": "tool/call"}}
    assert _notification_events(bare) == [bare]
    assert _notification_events(wrapped) == [bare]
    assert _notification_events(entries) == [bare]
    assert _notification_events(nested) == [{"type": "tool/call"}]
    assert _notification_events({"method": "x", "params": {}}) == []


class _StreamingFake:
    """Emits the same call twice, then the result — as a chatty runtime would."""

    def __init__(self, **kw):
        self.kw = kw
        self.on_notification = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, goal, session_id=None, on_notification=None):
        self.on_notification = on_notification
        notification = types.SimpleNamespace(method="session/event", payload={"event": _call()})
        on_notification(notification)
        on_notification(notification)  # duplicate delivery
        on_notification(types.SimpleNamespace(method="session/event", payload={"event": _result()}))
        return types.SimpleNamespace(
            final_response="done", events=[_call(), _result()], finish_reason="stop"
        )


def test_live_records_are_streamed_once_and_the_sweep_adds_nothing(tmp_path, monkeypatch):
    fake = _StreamingFake()
    monkeypatch.setitem(
        sys.modules, "deepseek_harness", types.SimpleNamespace(DeepSeekHarness=lambda **kw: fake)
    )
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"),
        session_root=str(tmp_path / "sess"),
        api_key="sk-test",
        live_mode="dry",
        isolated=False,
    )
    seen: list[dict] = []
    text, status, *_ = wrapper._run_live_events("g", "run-live", on_progress=seen.append)

    assert status == "completed" and text == "done"
    assert [r["phase"] for r in seen] == ["call", "result"]
    assert {r["callId"] for r in seen} == {"c1"}


def test_a_runtime_that_never_notifies_still_yields_a_complete_transcript(tmp_path, monkeypatch):
    """The post-run sweep is the fallback: a silent notification channel must
    not turn into a silent transcript."""

    class Quiet(_StreamingFake):
        def run(self, goal, session_id=None, on_notification=None):
            return types.SimpleNamespace(
                final_response="done", events=[_call(), _result()], finish_reason="stop"
            )

    fake = Quiet()
    monkeypatch.setitem(
        sys.modules, "deepseek_harness", types.SimpleNamespace(DeepSeekHarness=lambda **kw: fake)
    )
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"),
        session_root=str(tmp_path / "sess"),
        api_key="sk-test",
        live_mode="dry",
        isolated=False,
    )
    seen: list[dict] = []
    wrapper._run_live_events("g", "run-quiet", on_progress=seen.append)
    assert [r["phase"] for r in seen] == ["call", "result"]


def test_the_worker_writes_progress_then_result(tmp_path, monkeypatch):
    """The parent's reader depends on this exact line protocol."""
    fake = _StreamingFake()
    monkeypatch.setitem(
        sys.modules, "deepseek_harness", types.SimpleNamespace(DeepSeekHarness=lambda **kw: fake)
    )
    request = {
        "goal": "g",
        "session_id": "run-worker",
        "model": "deepseek-chat",
        "workspace_root": str(tmp_path / "ws"),
        "session_root": str(tmp_path / "sess"),
        "shell_policy": {},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request) + "\n"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)

    from harness_orchestrator import engine_worker

    assert engine_worker.main() == 0

    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert [("progress" in line) for line in lines] == [True, True, False]
    assert [line["progress"]["phase"] for line in lines[:-1]] == ["call", "result"]
    assert lines[-1]["status"] == "completed"
