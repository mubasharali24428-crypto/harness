"""Engine mode selection: injected > live SDK > honest stub."""

from harness_orchestrator.engine import DeepSeekHarnessWrapper


def test_stub_mode_is_honest_not_faked():
    w = DeepSeekHarnessWrapper()
    out = w.execute_goal("do a thing")
    assert out["status"] == "not_configured"
    assert "not configured" in out["finalResponse"]
    assert out["engine"] == "stub"


def test_injected_backend_still_works():
    w = DeepSeekHarnessWrapper(backend_runner=lambda g: f"ran:{g}")
    out = w.execute_goal("x")
    assert out["status"] == "completed" and out["finalResponse"] == "ran:x"
    assert out["engine"] == "injected"


def test_live_mode_detected_via_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DSH_MODEL", "qwen3-abliterated-128k:latest")
    w = DeepSeekHarnessWrapper()
    assert w.live_mode is True
    assert w._engine_name() == "deepseek-harness-sdk:qwen3-abliterated-128k:latest"


def _openrouter_key() -> str | None:
    """Read OPENROUTER_API_KEY from ~/.hermes/.env without ever printing it."""
    import os

    path = os.path.expanduser("~/.hermes/.env")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        for line in fh:
            if line.startswith("OPENROUTER_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                return value or None
    return None


def test_live_run_against_openrouter():
    """Live agentic round-trip through OpenRouter (stealth/ox-alpha).

    Proves the OpenAI-compatible engine path end-to-end INCLUDING a real
    filesystem side effect — replaces the old Ollama probe whose model
    returned status=completed with 0 chars of output (D2's honesty rule
    turned that into an honest error). Skips without OPENROUTER_API_KEY
    so CI stays green offline."""
    import tempfile

    import pytest

    api_key = _openrouter_key()
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not configured (~/.hermes/.env)")

    # Fresh workspace AND session root per run: dsh resumes session history
    # from session_root, so reusing the old Ollama-era dir would continue a
    # poisoned conversation instead of exercising a clean run.
    ws = tempfile.mkdtemp(prefix="harness-engine-test-ws-")
    session_root = tempfile.mkdtemp(prefix="harness-engine-test-sessions-")

    w = DeepSeekHarnessWrapper(
        workspace_dir=ws,
        session_root=session_root,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        model="stealth/ox-alpha",
        fallback_models=[],  # live test must exercise exactly the named model
    )
    out = w.execute_goal(
        "Create a file named engine_ok.txt containing exactly 'engine ok'."
    )
    assert out["status"] == "completed", out
    from pathlib import Path

    created = Path(ws) / "engine_ok.txt"
    assert created.read_text().strip() == "engine ok"


# ----------------------------------------------------------------------
# Wave 6 (D2): an empty/whitespace finalResponse is a FAILED rung, never a
# success — a missing model once returned status=completed with text="" and
# silently skipped the fallback ladder (live smoke evidence, smoke-run-1).
# ----------------------------------------------------------------------


def _wrapper_with_spy(monkeypatch, responses):
    """responses: dict model -> (text, status); records call order."""
    w = DeepSeekHarnessWrapper(
        api_key="sk-test",
        base_url="http://localhost:11434/v1",
        model="primary-model",
        fallback_models=["fallback-a", "fallback-b"],
    )
    calls: list[str | None] = []

    def fake_run(goal, session_id, model=None):
        calls.append(model)
        return responses[model]

    monkeypatch.setattr(w, "_run_live", fake_run)
    return w, calls


def test_empty_primary_response_falls_through_to_next_model(monkeypatch):
    w, calls = _wrapper_with_spy(
        monkeypatch,
        {
            "primary-model": ("", "completed"),  # the D2 fake-success shape
            "fallback-a": ("   \n  ", "completed"),  # whitespace-only also counts
            "fallback-b": ("real answer", "completed"),
        },
    )
    text, status = w._run_live_with_fallback("goal", "sess")
    assert (text, status) == ("real answer", "completed")
    assert calls == ["primary-model", "fallback-a", "fallback-b"]


def test_all_models_empty_is_honest_error(monkeypatch):
    w, calls = _wrapper_with_spy(
        monkeypatch,
        {m: ("", "completed") for m in ("primary-model", "fallback-a", "fallback-b")},
    )
    text, status = w._run_live_with_fallback("goal", "sess")
    assert status == "error"
    assert "primary-model" in text and "fallback-b" in text
    assert calls == ["primary-model", "fallback-a", "fallback-b"]


def test_nonempty_failure_status_still_falls_through(monkeypatch):
    """A chatty error must not be mistaken for success either."""
    w, calls = _wrapper_with_spy(
        monkeypatch,
        {
            "primary-model": ("model exploded loudly", "error"),
            "fallback-a": ("fine", "completed"),
        },
    )
    text, status = w._run_live_with_fallback("goal", "sess")
    assert (text, status) == ("fine", "completed")
    assert calls == ["primary-model", "fallback-a"]
