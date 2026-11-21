"""2026-11-13: the run aggregate's per-step detail (feeds the run page timeline)."""

from harness_orchestrator.service import OrchestratorService

GHP = "".join(["ghp", "_", "c" * 36])


def test_detail_keeps_only_listed_facts_and_scrubs_secrets():
    d = OrchestratorService._step_detail(
        {
            "callId": "c1",
            "name": "bash",
            "arguments": {
                "command": f"git push https://u:{GHP}@github.com/o/r\nsecond line",
                "env": "X=1",
            },
            "ok": False,
            "error": "401: Authorization: Bearer abcdef123456 rejected\ntrace...",
            "durationMs": 42.7,
            "output": "BIG BODY",
            "steps": ["a", "b", "c"],
        }
    )
    assert d["callId"] == "c1" and d["name"] == "bash" and d["ok"] is False
    assert d["durationMs"] == 42 and d["stepCount"] == 3
    assert GHP not in d["command"] and "second line" not in d["command"]
    assert "abcdef123456" not in d["error"] and "trace" not in d["error"]
    assert "output" not in d and "env" not in d and "BIG BODY" not in str(d)


def test_detail_reads_json_string_arguments_and_paths():
    d = OrchestratorService._step_detail({"arguments": '{"file_path": "src/a.py"}'})
    assert d == {"path": "src/a.py"}


def test_detail_is_none_for_empty_or_non_dict_payloads():
    assert OrchestratorService._step_detail(None) is None
    assert OrchestratorService._step_detail({"output": "x"}) is None
    assert OrchestratorService._step_detail("text") is None
