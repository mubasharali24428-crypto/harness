"""Engine mode selection: injected > explicit live SDK > honest stub.

M11 Wave 1 (W1-B1): engine modes are EXPLICIT — resolved from validated
config (``live_mode.py``), never ``bool(api_key and model)``, which counted
compose placeholders like ``DEEPSEEK_API_KEY=ollama`` as a live engine
(C-004). The forced-downgrade ladder is deleted per AGENTS.md law: only
the configured model ever runs (raw-02 H1), and attribution names the
model that actually answered (raw-02 L1).
"""

import os

import pytest

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.live_mode import LiveMode


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


def test_key_presence_alone_is_never_live(monkeypatch):
    """C-004 regression guard: env keys do not imply a live engine."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DSH_MODEL", "qwen3-abliterated-128k:latest")
    w = DeepSeekHarnessWrapper()
    assert w.live_mode is False
    assert w.mode is LiveMode.DRY


def test_explicit_live_mode_runs_sdk_path(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DSH_MODEL", "qwen3-abliterated-128k:latest")
    w = DeepSeekHarnessWrapper(live_mode="live")
    assert w.live_mode is True
    assert w._engine_name() == "deepseek-harness-sdk:qwen3-abliterated-128k:latest"


def _openrouter_key() -> str | None:
    """Read OPENROUTER_API_KEY from ~/.hermes/.env without ever printing it."""
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
        live_mode="live",  # exactly this model — there is no fallback anymore
    )
    out = w.execute_goal(
        "Create a file named engine_ok.txt containing exactly 'engine ok'."
    )
    if out["status"] == "error" and "empty response" in out.get("finalResponse", ""):
        # Upstream provider problem, not an engine-contract violation: the
        # empty-response→error honesty path itself is pinned offline by
        # test_empty_response_is_error_never_vacuous_success. Skip rather
        # than fail the whole suite on a third-party outage.
        pytest.skip(
            "OpenRouter upstream unavailable to model stealth/ox-alpha: "
            f"{out.get('finalResponse')!r}"
        )
    if out["status"] == "sdk_missing":
        # deepseek-harness-sdk not installed in THIS interpreter: environment
        # gap, not an engine-contract violation. Skip like other 3rd-party
        # availability problems (documented honest limit).
        pytest.skip("deepseek-harness-sdk not installed in this environment")
    assert out["status"] == "completed", out
    from pathlib import Path

    created = Path(ws) / "engine_ok.txt"
    assert created.read_text().strip() == "engine ok"


# ----------------------------------------------------------------------
# Downgrade ladder deleted (W1-B1 / raw-02 H1): one model, one attempt,
# honest status + honest attribution. Wave 6's (D2) empty-response
# honesty rule is preserved on the single configured rung.
# ----------------------------------------------------------------------


def _wrapper_with_spy(monkeypatch, text, status):
    w = DeepSeekHarnessWrapper(
        live_mode="live",
        api_key="sk-test",
        base_url="http://localhost:11434/v1",
        model="primary-model",
    )
    calls: list[str | None] = []

    def fake_run_events(goal, session_id, model=None):
        # M14-W4 F1a: the live path now reads (text, status, model, events);
        # events stay empty so results carry no usage block (estimates apply).
        calls.append(model)
        return text, status, model, []

    monkeypatch.setattr(w, "_run_live_events", fake_run_events)
    return w, calls


def test_downgrade_ladder_is_gone():
    import inspect

    assert not hasattr(DeepSeekHarnessWrapper, "_run_live_with_fallback")
    params = inspect.signature(DeepSeekHarnessWrapper.__init__).parameters
    assert "fallback_models" not in params


def test_empty_response_is_error_never_vacuous_success(monkeypatch):
    w, calls = _wrapper_with_spy(monkeypatch, "   \n  ", "completed")
    out = w.execute_goal("goal", "sess")
    assert out["status"] == "error"
    assert "primary-model" in out["finalResponse"]
    assert out["engine"] == "deepseek-harness-sdk:primary-model"  # L1 honesty
    assert calls == ["primary-model"]  # one attempt, zero fallbacks


def test_model_failure_is_honest_and_never_retried_on_other_models(monkeypatch):
    w, calls = _wrapper_with_spy(monkeypatch, "model exploded loudly", "error")
    out = w.execute_goal("goal", "sess")
    assert out["status"] == "error"
    assert "exploded" in out["finalResponse"]
    assert calls == ["primary-model"]


def test_nonempty_completed_response_succeeds(monkeypatch):
    w, calls = _wrapper_with_spy(monkeypatch, "real answer", "completed")
    out = w.execute_goal("goal", "sess")
    assert out["status"] == "completed"
    assert out["finalResponse"] == "real answer"
    assert calls == ["primary-model"]


# ----------------------------------------------------------------------
def test_live_exception_text_never_carries_the_key(monkeypatch):
    """Provider exceptions can embed the Authorization header or the
    provider body; the key configured on the wrapper must never reach the
    finalResponse surface (mirrors section3 providers' _scrub)."""
    w = DeepSeekHarnessWrapper(
        live_mode="live",
        api_key="sk-engineecho-test",
        base_url="http://localhost:11434/v1",
        model="primary-model",
    )

    def raising_run_events(goal, session_id, model=None):
        raise RuntimeError("boom Bearer sk-engineecho-test endpoint unreachable")

    monkeypatch.setattr(w, "_run_live_events", raising_run_events)
    out = w.execute_goal("goal", "sess")
    assert out["status"] == "error"
    assert "sk-engineecho-test" not in out["finalResponse"]
    assert "[redacted]" in out["finalResponse"]
    assert "boom" in out["finalResponse"]  # message stays diagnostic
