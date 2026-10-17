"""F111 — pure replay pieces: digest, redaction, archive, view assembly."""

from harness_orchestrator.replay_archive import (
    MAX_RECORD_BYTES,
    load_record,
    redact_record,
    save_record,
)
from harness_orchestrator.run_replay import (
    MAX_OBSERVATIONS,
    MAX_PROMPT_CHARS,
    build_replay_view,
    build_run_record,
    latest_context_ref,
    summarize_engine_events,
)

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GH_TOKEN = "ghp_" + "a" * 36


def test_summarize_engine_events_digest_ordered_and_tolerant():
    def chunk(**payload):
        return {"type": "assistant/chunk", "data": {"chunk": payload}}

    events = [
        chunk(type="tool_use", toolName="exec", id="t1"),
        chunk(type="tool_result", text="3 passed in 0.02s"),
        chunk(type="usage", usage={"inputTokens": 5}),
        {"type": "totally/new/event", "data": {"weird": True}},
        "not-a-dict",
    ]
    out = summarize_engine_events(events)
    kinds = [o.get("chunkType", o["kind"]) for o in out]
    assert kinds == ["tool_use", "tool_result", "usage", "totally/new/event"]
    assert out[0]["toolName"] == "exec" and out[0]["id"] == "t1"
    assert out[1]["text"] == "3 passed in 0.02s"


def test_summarize_engine_events_caps_and_truncates():
    long_text = "x" * 1000
    out = summarize_engine_events(
        [{"type": "assistant/chunk", "data": {"chunk": {"type": "text", "text": long_text}}}],
        max_text=10,
    )
    assert out[0]["text"] == "x" * 10
    assert out[0]["textTruncated"] is True
    capped = summarize_engine_events(
        [{"type": "e"} for _ in range(MAX_OBSERVATIONS + 5)], max_items=3
    )
    assert capped[-1]["kind"] == "truncated"


def test_redact_record_replaces_configured_and_pattern_secrets():
    record = {
        "prompt": {"text": f"key={AWS_KEY} token={GH_TOKEN} cfg=supersecret"},
        "nested": [{"x": "supersecret"}],
    }
    redacted, replaced = redact_record(record, secrets=("supersecret",))
    text = str(redacted)
    assert AWS_KEY not in text and GH_TOKEN not in text and "supersecret" not in text
    assert "[REDACTED:aws-key]" in text and "[REDACTED:github-token]" in text
    assert replaced >= 4
    # input was not mutated (pure)
    assert AWS_KEY in record["prompt"]["text"]


def test_archive_roundtrip_integrity_and_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_REPLAY_ROOT", str(tmp_path / "replay"))
    ref = save_record({"hello": "world", "n": [1, 2]})
    loaded = load_record(ref)
    assert loaded == {"hello": "world", "n": [1, 2]}
    # tampered body → refused
    body_path = tmp_path / "replay" / ref["path"]
    body_path.write_text('{"hello": "evil"}')
    assert load_record(ref) is None
    # traversal / malformed refs → refused
    assert load_record({"kind": "replay-record", "path": "../x.json"}) is None
    assert load_record({"kind": "checkpoint", "path": ref["path"]}) is None
    assert load_record(None) is None
    # oversize bodies are refused by the writer, not silently truncated
    try:
        save_record({"big": "x" * (MAX_RECORD_BYTES + 10)})
        raise AssertionError("oversize record must raise")
    except ValueError:
        pass


def test_build_run_record_caps_prompt_and_counts_redactions(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_REPLAY_ROOT", str(tmp_path / "replay"))
    record = build_run_record(
        run_id="r1",
        prompt_text="x" * (MAX_PROMPT_CHARS + 100) + AWS_KEY,
        prompt_meta={"framed": True},
        engine_result={
            "status": "completed",
            "finalResponse": f"used {AWS_KEY}",
            "observations": [{"kind": "assistant/chunk"}],
        },
    )
    assert record["prompt"]["truncated"] is True
    assert len(record["prompt"]["text"]) == MAX_PROMPT_CHARS
    assert record["engineResult"]["finalResponse"] == "used [REDACTED:aws-key]"
    assert "observations" not in record["engineResult"]
    assert record["observations"] == [{"kind": "assistant/chunk"}]
    assert record["redaction"]["applied"] is True


def test_build_replay_view_orders_event_observations_and_marks_gaps():
    rows = [
        {"seq": 1, "ts": "t1", "type": "execution_started", "payload": {"runId": "r"}},
        {"seq": 2, "ts": "t2", "type": "terminal_output", "payload": {"line": "$ pytest -q"}},
        {
            "seq": 3,
            "ts": "t3",
            "type": "diff_progress",
            "payload": {"path": "src/a.py", "checkpoint": {"kind": "agent-edit"}},
        },
        {"seq": 4, "ts": "t4", "type": "engine_completed", "payload": {"summary": "ok"}},
    ]
    view = build_replay_view(run_id="r", rows=rows, record=None, record_ref=None)
    kinds = [o["kind"] for o in view["observations"]]
    assert kinds == ["tool_output", "file_observation", "engine_settled"]
    assert view["observations"][0]["line"] == "$ pytest -q"
    assert view["available"] is False
    assert view["prompt"]["available"] is False
    assert view["unavailable"] and "reason" in view["unavailable"][0]
    assert [t["seq"] for t in view["timeline"]] == [1, 2, 3, 4]
    assert latest_context_ref(rows) is None


def test_latest_context_ref_reads_settlement_payload():
    rows = [
        {"type": "engine_failed", "payload": {"reason": "x"}},
        {"type": "engine_completed", "payload": {"contextRef": {"sha": "abc"}}},
    ]
    assert latest_context_ref(rows) == {"sha": "abc"}
