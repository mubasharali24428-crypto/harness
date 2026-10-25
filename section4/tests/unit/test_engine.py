"""Engine mode selection: injected > explicit live SDK > honest stub.

M11 Wave 1 (W1-B1): engine modes are EXPLICIT — resolved from validated
config (``live_mode.py``), never ``bool(api_key and model)``, which counted
compose placeholders like ``DEEPSEEK_API_KEY=ollama`` as a live engine
(C-004). The forced-downgrade ladder is deleted per AGENTS.md law: only
the configured model ever runs (raw-02 H1), and attribution names the
model that actually answered (raw-02 L1).
"""

import os
from pathlib import Path

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
    """Read the private test credential from HARNESS_OPENROUTER_KEY_FILE (never printed).

    No default path: a section must not reach into a sibling section (isolation law).
    """
    path = os.environ.get("HARNESS_OPENROUTER_KEY_FILE")
    if not path:
        return None
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        for line in fh:
            if line.startswith("OPENROUTER_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                return value or None
    return None


def test_live_run_against_openrouter(monkeypatch):
    """Live agentic round-trip through an explicitly selected free OpenRouter model.

    Proves the OpenAI-compatible engine path end-to-end INCLUDING a real
    filesystem side effect — replaces the old Ollama probe whose model
    returned status=completed with 0 chars of output (D2's honesty rule
    turned that into an honest error). Skips without OPENROUTER_API_KEY
    so CI stays green offline."""
    import tempfile

    if os.environ.get("HARNESS_LIVE_OPENROUTER") != "1":
        pytest.skip("opt-in OpenRouter: set HARNESS_LIVE_OPENROUTER=1 (strict, no outage skip)")
    import json
    import urllib.request

    model = os.environ.get("HARNESS_TEST_OPENROUTER_MODEL", "cohere/north-mini-code:free")
    # Tests may only select catalog-confirmed free models. No paid fallback.
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=20) as response:
        catalog = json.load(response)["data"]
    selected = next((m for m in catalog if m["id"] == model), None)
    assert selected is not None and model.endswith(":free"), "Select an available :free model"
    assert all(float(selected["pricing"][k]) == 0 for k in ("prompt", "completion"))
    api_key = _openrouter_key()
    if not api_key:
        pytest.fail(
            "Section 3 OpenRouter test credential not configured; strict verification cannot run"
        )
    monkeypatch.setenv("HARNESS_ENGINE_CONFINEMENT", "container")
    monkeypatch.delenv("HARNESS_ENGINE_WORKER_MODULE", raising=False)
    monkeypatch.delenv("HARNESS_ENGINE_IMAGE", raising=False)

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
        model=model,
        isolated=True,
        live_mode="live",  # exactly this model — there is no fallback anymore
    )
    from pathlib import Path

    patch = Path(w.run_session_dir("provider-proof")) / "openrouter.patch.yml"
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text((Path(__file__).parents[1] / "fixtures/openrouter.patch.yml").read_text())
    monkeypatch.setenv("DSH_MODEL", model)
    monkeypatch.setenv("DSH_PATCH_FILES", str(patch))
    out = w.execute_goal(
        "Create a file named engine_ok.txt containing exactly 'engine ok'.", "provider-proof"
    )
    assert out["status"] == "completed", out
    from pathlib import Path

    created = Path(w.run_workspace("provider-proof")) / "engine_ok.txt"
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
# S1-R02c: a live run sets DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL in the
# process env for the duration of the SDK call. The restore must be EXACT:
# when the variable was not there before, it must be gone afterwards — a
# provider secret must never linger in a long-lived server process.
# ----------------------------------------------------------------------


class _FakeResult:
    final_response = "ok"
    events: list = []


class _FakeHarness:
    seen_env: tuple = ()

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        _FakeHarness.seen_env = (self.kwargs.get("api_key"), self.kwargs.get("base_url"))
        return self

    def __exit__(self, *exc):
        return False

    def run(self, goal, session_id=None):
        return _FakeResult()


def _install_fake_sdk(monkeypatch):
    import sys
    import types

    mod = types.ModuleType("deepseek_harness")
    mod.DeepSeekHarness = _FakeHarness
    monkeypatch.setitem(sys.modules, "deepseek_harness", mod)


def _live_wrapper(tmp_path):
    return DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "ws"),
        session_root=str(tmp_path / "sessions"),
        api_key="sk-injected-rotating",
        base_url="http://engine.test/v1",
        model="m1",
        live_mode="live",
    )


def test_live_run_env_restores_exactly_when_originally_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    _install_fake_sdk(monkeypatch)
    text, status, _model, _events = _live_wrapper(tmp_path)._run_live_events("g", "s-1")
    assert (status, text) == ("completed", "ok")
    # The keys reach the SDK as kwargs (guards against a vacuous pass)…
    assert _FakeHarness.seen_env == ("sk-injected-rotating", "http://engine.test/v1")
    # …and the process environment is never touched.
    assert "DEEPSEEK_API_KEY" not in os.environ
    assert "DEEPSEEK_BASE_URL" not in os.environ


def test_live_run_env_restores_previous_values(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://old/v1")
    _install_fake_sdk(monkeypatch)
    text, status, _model, _events = _live_wrapper(tmp_path)._run_live_events("g", "s-2")
    assert (status, text) == ("completed", "ok")
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-old"
    assert os.environ["DEEPSEEK_BASE_URL"] == "http://old/v1"


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


def test_provision_workspace_empty_then_seeded_and_resume_keeps_edits(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    (seed / ".git").mkdir(parents=True)
    (seed / ".git" / "config").write_text("x")
    (seed / "src").mkdir()
    (seed / "src" / "a.py").write_text("one")
    w = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"))

    monkeypatch.delenv("HARNESS_WORKSPACE_SEED", raising=False)
    empty = Path(w.provision_workspace("r-empty"))
    assert empty.is_dir() and not any(empty.iterdir())

    monkeypatch.setenv("HARNESS_WORKSPACE_SEED", str(seed))
    ws = Path(w.provision_workspace("r-seeded"))
    assert (ws / "src" / "a.py").read_text() == "one" and not (ws / ".git").exists()
    (ws / "src" / "a.py").write_text("edited")
    assert w.provision_workspace("r-seeded") == str(ws)  # resume does not re-seed
    assert (ws / "src" / "a.py").read_text() == "edited"

    monkeypatch.setenv("HARNESS_WORKSPACE_SEED", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="not a directory"):
        w.provision_workspace("r-bad")


def test_per_run_model_override_wins_over_the_configured_default(monkeypatch):
    """A team's saved model reaches the SDK call; the default stays intact."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DSH_MODEL", "deployment-default")
    w = DeepSeekHarnessWrapper(live_mode="live")
    seen: list[str] = []

    def fake_live(goal, session_id, model):  # noqa: ARG001
        seen.append(model)
        return "ok", "completed", model, []

    monkeypatch.setattr(w, "_run_live_events", fake_live)
    w.execute_goal("goal", "s1", model="team-choice")
    w.execute_goal("goal", "s2")
    assert seen == ["team-choice", "deployment-default"]
