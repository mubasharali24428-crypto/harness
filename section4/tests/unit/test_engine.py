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


def test_live_run_against_local_ollama(monkeypatch):
    import pytest

    ollama_up = True
    import urllib.request

    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
    except Exception:
        ollama_up = False
    if not ollama_up:
        pytest.skip("local Ollama not running")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "ollama")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("DSH_MODEL", "qwen3-abliterated-128k:latest")
    w = DeepSeekHarnessWrapper(
        workspace_dir="/tmp/harness-engine-test-ws",
        session_root="/tmp/harness-engine-test-sessions",
    )
    out = w.execute_goal("Create a file named engine_ok.txt containing exactly 'engine ok'.")
    assert out["status"] == "completed", out
    from pathlib import Path

    created = Path("/tmp/harness-engine-test-ws") / "engine_ok.txt"
    assert created.read_text().strip() == "engine ok"
