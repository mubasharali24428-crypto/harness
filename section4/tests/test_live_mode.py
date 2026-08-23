"""M11 Wave 1 — lane W1-B1: engine boot integrity & live-mode honesty.

Proves (per brief W1-B1 verify-contract):
* C-004 — placeholder credentials are REJECTED, never counted as live;
  mode is an explicit enum {live, dry} resolved from validated config,
  never ``bool(key)``.
* H-005 — compose ships no master-key dev default and no ALLOW_DEV=1.
* H-006 — the forced model-downgrade ladder is deleted; result
  attribution stays honest (raw-02 L1: report the model that answered).
* deploy/runners/engine_boot.py exits non-zero with an actionable,
  name-only message when required env is missing/degenerate — and never
  prints secret VALUES.
* The orchestrator status payload exposes the explicit engine mode.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.live_mode import (
    ENGINE_LIVE_MODE_ENV,
    API_KEY_ENV,
    MODEL_ENV,
    LiveMode,
    is_placeholder,
    parse_live_mode,
    resolve_live_mode,
    validate_engine_env,
    validate_live_credentials,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOT_SCRIPT = REPO_ROOT / "deploy" / "runners" / "engine_boot.py"
COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yml"

_ENGINE_ENV_PREFIXES = ("DEEPSEEK_", "ENGINE_LIVE_MODE", "DSH_")


def _clean_environ(**overrides: str) -> dict[str, str]:
    """Host env minus every engine variable, plus explicit overrides."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_ENGINE_ENV_PREFIXES)
    }
    env.update(overrides)
    return env


# --------------------------------------------------------------------------
# C-004: placeholders are never live configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "ollama",  # the historic compose default — THE regression
        "Ollama",
        "",
        "   ",
        "changeme",
        "CHANGEME-NOW",
        "your-api-key",
        "<api_key>",
        "${DEEPSEEK_API_KEY}",
        "sk-<paste-your-key-here>",
        "dev-master-key-change-me-in-prod!",
        None,
    ],
)
def test_placeholder_values_are_rejected(value):
    assert is_placeholder(value) is True


@pytest.mark.parametrize("value", ["sk-real-9f2c...", "a-real-credential-string"])
def test_real_looking_keys_are_not_placeholders(value):
    assert is_placeholder(value) is False


def test_validate_rejects_placeholder_api_key_by_name_not_value():
    with pytest.raises(Exception) as excinfo:
        validate_live_credentials(api_key="ollama", model="mymodel")
    message = str(excinfo.value)
    assert API_KEY_ENV in message  # names the VARIABLE...
    assert "ollama" not in message  # ...never the VALUE


def test_validate_rejects_missing_model():
    with pytest.raises(Exception) as excinfo:
        validate_live_credentials(api_key="sk-real", model=None)
    assert MODEL_ENV in str(excinfo.value)


def test_wrapper_construction_rejects_placeholder_key_under_live_mode(monkeypatch):
    """The C-004 shape exactly: ENGINE_LIVE_MODE=live with compose's old
    DEEPSEEK_API_KEY=ollama default must fail loudly at construction."""
    monkeypatch.setenv(ENGINE_LIVE_MODE_ENV, "live")
    monkeypatch.setenv(API_KEY_ENV, "ollama")
    monkeypatch.setenv(MODEL_ENV, "qwen3.5-9b-128k:latest")
    with pytest.raises(Exception) as excinfo:
        DeepSeekHarnessWrapper()
    assert API_KEY_ENV in str(excinfo.value)


def test_validate_engine_env_summary_carries_no_secret_values():
    summary = validate_engine_env(
        {
            ENGINE_LIVE_MODE_ENV: "live",
            API_KEY_ENV: "sk-super-secret-value",
            MODEL_ENV: "mymodel",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        }
    )
    assert summary["mode"] is LiveMode.LIVE
    assert "sk-super-secret-value" not in repr(summary)
    assert "sk-super-secret-value" not in str(summary)


def test_base_url_must_be_a_url_when_set():
    with pytest.raises(Exception) as excinfo:
        validate_live_credentials(
            api_key="sk-real", model="mymodel", base_url="ollama"
        )
    assert "http" in str(excinfo.value)


# --------------------------------------------------------------------------
# live_mode is an explicit enum, never bool(key)
# --------------------------------------------------------------------------


def test_parse_accepts_only_live_and_dry():
    assert parse_live_mode("live") is LiveMode.LIVE
    assert parse_live_mode("LIVE") is LiveMode.LIVE
    assert parse_live_mode(" live ") is LiveMode.LIVE
    assert parse_live_mode("dry") is LiveMode.DRY
    assert parse_live_mode(None) is LiveMode.DRY
    assert parse_live_mode("") is LiveMode.DRY
    assert parse_live_mode("   ") is LiveMode.DRY


def test_parse_rejects_unknown_values_loudly():
    with pytest.raises(Exception) as excinfo:
        parse_live_mode("prod")  # typo must fail, not silently become dry
    assert ENGINE_LIVE_MODE_ENV in str(excinfo.value)


def test_resolve_defaults_to_dry_without_explicit_choice():
    assert resolve_live_mode({}) is LiveMode.DRY
    assert resolve_live_mode({ENGINE_LIVE_MODE_ENV: ""}) is LiveMode.DRY
    assert resolve_live_mode({ENGINE_LIVE_MODE_ENV: "live"}) is LiveMode.LIVE


def test_key_presence_alone_is_never_live(monkeypatch):
    """The heart of C-004: keys in the env do NOT imply a live engine."""
    monkeypatch.setenv(API_KEY_ENV, "sk-real-key-material")
    monkeypatch.delenv(ENGINE_LIVE_MODE_ENV, raising=False)
    w = DeepSeekHarnessWrapper()
    assert w.mode is LiveMode.DRY
    assert w.live_mode is False
    out = w.execute_goal("goal")
    assert out["status"] == "not_configured"  # honest stub
    assert out["engine"] == "stub"


def test_explicit_live_string_and_enum_are_equivalent():
    a = DeepSeekHarnessWrapper(
        live_mode="live", api_key="sk-real", model="mymodel"
    )
    b = DeepSeekHarnessWrapper(
        live_mode=LiveMode.LIVE, api_key="sk-real", model="mymodel"
    )
    assert a.mode is LiveMode.LIVE and b.mode is LiveMode.LIVE
    assert a.live_mode is True and b.live_mode is True


# --------------------------------------------------------------------------
# H-006: forced-downgrade ladder deleted, attribution honest (raw-02 L1)
# --------------------------------------------------------------------------


def test_downgrade_ladder_is_gone():
    assert not hasattr(DeepSeekHarnessWrapper, "_run_live_with_fallback")


def test_no_fallback_models_parameter_remains():
    params = inspect.signature(DeepSeekHarnessWrapper.__init__).parameters
    assert "fallback_models" not in params
    source = inspect.getsource(DeepSeekHarnessWrapper)
    assert "DSH_FALLBACK_MODELS" not in source
    assert "local_fallback_candidates" not in source


def test_failed_model_is_reported_honestly_without_retry(monkeypatch):
    """Primary fails ⇒ status=error naming THAT model. No second model is
    ever consulted (AGENTS.md: no forced model downgrade)."""
    w = DeepSeekHarnessWrapper(
        live_mode="live", api_key="sk-real", model="primary-model"
    )
    calls: list[str | None] = []

    def fake_run(goal, session_id, model=None):
        calls.append(model)
        return "model exploded", "error", model

    monkeypatch.setattr(w, "_run_live", fake_run)
    out = w.execute_goal("goal", "sess")
    assert out["status"] == "error"
    assert out["finalResponse"] == "model exploded"
    assert out["engine"] == "deepseek-harness-sdk:primary-model"
    assert calls == ["primary-model"]  # exactly one attempt, zero fallbacks


def test_result_attribution_names_the_model_that_answered(monkeypatch):
    """raw-02 L1: even where a different model could answer, the engine
    field must name the actual responder — here the single configured one."""
    w = DeepSeekHarnessWrapper(
        live_mode="live", api_key="sk-real", model="configured-model"
    )

    def fake_run(goal, session_id, model=None):
        return "answer", "completed", model

    monkeypatch.setattr(w, "_run_live", fake_run)
    out = w.execute_goal("goal", "sess")
    assert out["status"] == "completed"
    assert out["engine"] == "deepseek-harness-sdk:configured-model"


# --------------------------------------------------------------------------
# deploy/runners/engine_boot.py: validate-or-exit, names-only output
# --------------------------------------------------------------------------


def _run_boot(**overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BOOT_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
        env=_clean_environ(**overrides),
    )


def test_boot_missing_env_exits_nonzero_with_actionable_message():
    proc = _run_boot(**{ENGINE_LIVE_MODE_ENV: "live"})
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "ENGINE BOOT FAILED" in combined
    assert ENGINE_LIVE_MODE_ENV in combined  # tells you HOW to run dry


def test_boot_never_prints_secret_values():
    secret = "sk-boot-test-NOT-A-REAL-KEY"
    proc = _run_boot(**{ENGINE_LIVE_MODE_ENV: "live", API_KEY_ENV: secret})
    assert proc.returncode != 0
    assert secret not in proc.stdout
    assert secret not in proc.stderr


def test_boot_placeholder_key_rejected_cleanly():
    proc = _run_boot(
        **{ENGINE_LIVE_MODE_ENV: "live", API_KEY_ENV: "ollama",
           MODEL_ENV: "mymodel"}
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "ENGINE BOOT FAILED" in combined
    assert API_KEY_ENV in combined
    assert "ollama" not in combined  # placeholder value never echoed


def test_boot_valid_live_config_passes():
    proc = _run_boot(
        **{
            ENGINE_LIVE_MODE_ENV: "live",
            API_KEY_ENV: "sk-real-boot-check",
            MODEL_ENV: "mymodel",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
        }
    )
    assert proc.returncode == 0, proc.stderr
    assert "mode=live" in proc.stdout
    assert "DSH_MODEL=mymodel" in proc.stdout
    assert "sk-real-boot-check" not in proc.stdout  # names, not values


def test_boot_explicit_dry_passes():
    proc = _run_boot(**{ENGINE_LIVE_MODE_ENV: "dry"})
    assert proc.returncode == 0, proc.stderr
    assert "mode=dry" in proc.stdout


def test_boot_invalid_mode_value_fails_loudly():
    proc = _run_boot(**{ENGINE_LIVE_MODE_ENV: "yeet"})
    assert proc.returncode != 0
    assert "is invalid" in proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# Status endpoint exposes the explicit mode
# --------------------------------------------------------------------------


async def test_status_payload_exposes_explicit_engine_mode():
    from harness_orchestrator.service import OrchestratorService

    dry = OrchestratorService(engine=DeepSeekHarnessWrapper())
    assert (await dry.get_status("run-dry"))["engineMode"] == "dry"

    live = OrchestratorService(
        engine=DeepSeekHarnessWrapper(
            live_mode="live", api_key="sk-real", model="mymodel"
        )
    )
    assert (await live.get_status("run-live"))["engineMode"] == "live"


# --------------------------------------------------------------------------
# H-005/C-004 in compose: no dev master key, no ALLOW_DEV=1, no ollama
# --------------------------------------------------------------------------


def test_compose_ships_no_degenerate_defaults():
    text = COMPOSE.read_text()
    assert "-ollama" not in text  # the placeholder API key default
    assert "dev-master-key-change-me-in-prod!" not in text  # H-005
    assert "host.docker.internal:11434" not in text  # implicit Ollama endpoint
    assert "${HARNESS_ALLOW_DEV_MASTER_KEY:-1}" not in text  # H-005 opt-in
    assert "DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}" in text
    assert "ENGINE_LIVE_MODE: ${ENGINE_LIVE_MODE:-dry}" in text


def test_compose_parses_as_yaml():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(COMPOSE.read_text())
    env = doc["x-backend-env"]
    assert env["ENGINE_LIVE_MODE"] == "${ENGINE_LIVE_MODE:-dry}"
    assert env["DEEPSEEK_API_KEY"] == "${DEEPSEEK_API_KEY:-}"
    assert env["HARNESS_ALLOW_DEV_MASTER_KEY"] == "${HARNESS_ALLOW_DEV_MASTER_KEY:-}"
