"""Execution engine: real DeepSeek Harness (Python SDK) with explicit modes.

Modes (exactly one, from VALIDATED config — see ``live_mode.py``):
1. backend_runner callable  — injected (tests / custom runtimes)
2. live                     — ENGINE_LIVE_MODE=live plus a validated
                              DEEPSEEK_API_KEY / DSH_MODEL; every run goes
                              through deepseek-harness-sdk
3. dry                      — explicit "not-configured" response, never
                              faked success

M11 Wave 1 (W1-B1): the mode is an explicit :class:`LiveMode` enum
resolved from validated configuration — never ``bool(api_key and model)``,
which counted compose placeholders like ``DEEPSEEK_API_KEY=ollama`` as a
live engine (finding C-004). The forced model-downgrade ladder is DELETED
per AGENTS.md law ("No forced model downgrade"; raw-02 finding H1): the
configured model is the only model that runs, and its failure surfaces
honestly as ``status="error"`` instead of being retried against whatever
model the environment or a catalog happens to offer. Result attribution
stays honest (raw-02 L1): the ``engine`` field always names the model
that actually answered.
"""

import os
from collections.abc import Callable
from typing import Any

from harness_orchestrator.live_mode import (
    LiveMode,
    parse_live_mode,
    resolve_live_mode,
    validate_live_credentials,
)


class DeepSeekHarnessWrapper:
    """Wraps DeepSeek Harness behind one method: execute_goal(goal)."""

    def __init__(
        self,
        backend_runner: Callable[[str], str] | None = None,
        workspace_dir: str | None = None,
        session_root: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        live_mode: LiveMode | str | None = None,
    ) -> None:
        self.backend_runner = backend_runner
        self.workspace = workspace_dir or os.environ.get(
            "DSH_WORKSPACE", "/tmp/harness-dsh-workspace"
        )
        self.session_root = session_root or os.environ.get(
            "DSH_SESSION_ROOT", "/tmp/harness-dsh-sessions"
        )
        # Explicit constructor values win over the environment; blank env
        # entries behave like unset ones (compose "${VAR:-}" emptiness).
        self.api_key = (
            api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        )
        self.base_url = (
            base_url if base_url is not None else os.environ.get("DEEPSEEK_BASE_URL", "")
        )
        self.model = model or os.environ.get("DSH_MODEL", "deepseek-chat")

        # M11-W1-B1: mode is an explicit, validated choice. A string arg is
        # parsed strictly (a typo fails HERE, loudly); None falls back to
        # ENGINE_LIVE_MODE, whose absence means dry — never inferred live.
        if isinstance(live_mode, str):
            self.live_config: LiveMode = parse_live_mode(live_mode)
        elif isinstance(live_mode, LiveMode):
            self.live_config = live_mode
        else:
            self.live_config = resolve_live_mode()

        # Fail fast at construction when LIVE is requested without a real
        # credential/model — placeholder keys are rejected by name here,
        # before any run can silently degrade (C-004).
        if self.backend_runner is None and self.live_config is LiveMode.LIVE:
            validate_live_credentials(
                api_key=self.api_key or None,
                model=self.model or None,
                base_url=self.base_url or None,
            )

    @property
    def mode(self) -> LiveMode:
        """The explicit engine mode (never inferred from secret presence)."""
        return self.live_config

    @property
    def live_mode(self) -> bool:
        """Backward-compat boolean view of :attr:`mode`."""
        return self.live_config is LiveMode.LIVE

    def execute_goal(self, goal: str, session_id: str = "session-001") -> dict[str, Any]:
        used_model: str | None = None
        if self.backend_runner:
            result_text = self.backend_runner(goal)
            status = "completed"
        elif self.live_config is LiveMode.LIVE:
            # The configured model is THE model: passed explicitly so no
            # callee ever picks another one (no-downgrade law).
            try:
                result_text, status, used_model = self._run_live(
                    goal, session_id, model=self.model
                )
            except Exception as exc:  # noqa: BLE001 — honest error, never escape
                result_text = f"DeepSeek Harness run failed on {self.model}: {exc}"
                status = "error"
                used_model = self.model
            if status == "completed" and not (result_text or "").strip():
                # Wave 6 (D2) honesty rule, preserved after ladder deletion:
                # an empty/whitespace response is a FAILED answer, never a
                # vacuous success attributed to the configured model.
                status = "error"
                result_text = f"model {used_model} returned an empty response"
        else:
            result_text = (
                "DeepSeek Harness not configured: set ENGINE_LIVE_MODE=live "
                "with DEEPSEEK_API_KEY (and DEEPSEEK_BASE_URL for "
                "OpenAI-compatible endpoints) to enable live runs."
            )
            status = "not_configured"

        return {
            "sessionId": session_id,
            "engine": self._engine_name(used_model),
            "status": status,
            "finalResponse": result_text,
        }

    def _engine_name(self, model_used: str | None = None) -> str:
        """Name the engine AND the model that actually answered.

        raw-02 L1 honesty rule: report the winning candidate, never assume
        the configured primary answered."""
        if self.backend_runner:
            return "injected"
        if self.live_config is LiveMode.LIVE:
            return f"deepseek-harness-sdk:{model_used or self.model}"
        return "stub"

    def _run_live(self, goal: str, session_id: str, model: str | None = None) -> tuple[
        str, str, str | None
    ]:
        """One attempt against exactly one model — no ladder, no retries on
        other models. Returns (text, status, model_that_answered); the third
        element keeps attribution honest even on failure paths."""
        model = model or self.model
        try:
            from deepseek_harness import DeepSeekHarness

            os.makedirs(self.workspace, exist_ok=True)
            os.makedirs(self.session_root, exist_ok=True)
            kwargs: dict[str, Any] = dict(
                provider="deepseek-official",
                model=model,
                max_tokens=4096,
                cwd=self.workspace,
                session_root=self.session_root,
            )
            cordis = os.environ.get("DSH_CORDIS_CONFIG")
            if cordis and os.path.exists(cordis):
                kwargs["cordis"] = cordis

            import base64

            saved_key = os.environ.get("DEEPSEEK_API_KEY")
            saved_url = os.environ.get("DEEPSEEK_BASE_URL")
            os.environ["DEEPSEEK_API_KEY"] = self.api_key
            if self.base_url:
                os.environ["DEEPSEEK_BASE_URL"] = self.base_url
            try:
                with DeepSeekHarness(**kwargs) as harness:
                    result = harness.run(goal, session_id=session_id)
                text = result.final_response
            finally:
                if saved_key is not None:
                    os.environ["DEEPSEEK_API_KEY"] = saved_key
                if saved_url is not None:
                    if saved_url:
                        os.environ["DEEPSEEK_BASE_URL"] = saved_url
                    else:
                        os.environ.pop("DEEPSEEK_BASE_URL", None)
                _ = base64
            return text, "completed", model
        except ImportError:
            return (
                "deepseek-harness-sdk is not installed in this environment.",
                "sdk_missing",
                model,
            )
        except Exception as exc:  # noqa: BLE001 — surface engine failures to the UI
            return f"DeepSeek Harness run failed: {exc}", "error", model
