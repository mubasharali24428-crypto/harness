"""Resume an interrupted SDK session without silently starting another."""

import sys
import types
from pathlib import Path

from harness_orchestrator.engine import DeepSeekHarnessWrapper


def test_persisted_session_is_resumed_and_restore_failure_is_not_retried(monkeypatch, tmp_path):
    calls = []

    class Result:
        final_response = "ok"
        events = []

    class Harness:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, goal, *, session_id, **kwargs):
            calls.append(("new", session_id))
            return Result()

    class Session:
        def run(self, goal, **kwargs):
            assert "workspace may have been rewound" in goal
            assert goal.endswith("goal")
            return Result()

    def resume(self, session_id):
        calls.append(("resume", session_id))
        if len(calls) == 3:
            raise RuntimeError("restore failed")
        return Session()

    Harness.resume_session = resume
    monkeypatch.setitem(
        sys.modules, "deepseek_harness", types.SimpleNamespace(DeepSeekHarness=Harness)
    )
    engine = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"),
                                    session_root=str(tmp_path / "sessions"),
                                    api_key="test-key", base_url="http://engine.test/v1",
                                    model="test-model", live_mode="live")
    assert engine._run_live_events("goal", "run1")[1] == "completed"
    history = (Path(engine.run_session_dir("run1")) / "sessions" / "workspace" /
               "run1" / "session.v4.jsonl")
    history.parent.mkdir(parents=True)
    history.write_text("history")
    assert engine._run_live_events("goal", "run1")[1] == "completed"
    assert engine._run_live_events("goal", "run1")[1] == "error"
    assert calls == [("new", "run1"), ("resume", "run1"), ("resume", "run1")]
