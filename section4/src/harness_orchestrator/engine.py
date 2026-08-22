"""Execution engine: real DeepSeek Harness (Python SDK) with graceful fallbacks.

Modes (first match wins):
1. backend_runner callable  — injected (tests / custom runtimes)
2. deepseek-harness-sdk     — when api_key/base_url configured; the verified
                              programmatic interface from task0/hello.py
3. stub                     — explicit "not-configured" response, never faked success
"""

import os
from collections.abc import Callable
from typing import Any


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
        fallback_models: list[str] | None = None,
    ) -> None:
        self.backend_runner = backend_runner
        self.workspace = workspace_dir or os.environ.get(
            "DSH_WORKSPACE", "/tmp/harness-dsh-workspace"
        )
        self.session_root = session_root or os.environ.get(
            "DSH_SESSION_ROOT", "/tmp/harness-dsh-sessions"
        )
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url or os.environ.get("DEEPSEEK_BASE_URL", "")
        self.model = model or os.environ.get("DSH_MODEL", "deepseek-chat")
        env_fb = [m for m in os.environ.get("DSH_FALLBACK_MODELS", "").split(",") if m]
        self.fallback_models = list(fallback_models) if fallback_models else env_fb

    @property
    def live_mode(self) -> bool:
        return bool(self.backend_runner is None and self.api_key and self.model)

    def execute_goal(self, goal: str, session_id: str = "session-001") -> dict[str, Any]:
        if self.backend_runner:
            result_text = self.backend_runner(goal)
            status = "completed"
        elif self.live_mode:
            result_text, status = self._run_live_with_fallback(goal, session_id)
        else:
            result_text = (
                "DeepSeek Harness not configured: set DEEPSEEK_API_KEY "
                "(and DEEPSEEK_BASE_URL for OpenAI-compatible endpoints) to enable live runs."
            )
            status = "not_configured"

        return {
            "sessionId": session_id,
            "engine": self._engine_name(),
            "status": status,
            "finalResponse": result_text,
        }

    def _engine_name(self) -> str:
        if self.backend_runner:
            return "injected"
        if self.live_mode:
            return f"deepseek-harness-sdk:{self.model}"
        return "stub"

    def _run_live_with_fallback(self, goal: str, session_id: str) -> tuple[str, str]:
        """dsh-inspired downgrade: primary model first, then each fallback.
        All candidates failing is an honest 'error', never a fake success."""
        candidates = [self.model] + [m for m in self.fallback_models if m and m != self.model]
        last_error: Exception | None = None
        for candidate in candidates:
            original = self.model
            try:
                self.model = candidate
                text, status = self._run_live(goal, session_id)
            except Exception as exc:  # noqa: BLE001 — try next rung of the ladder
                last_error = exc
                continue
            finally:
                self.model = original
            if status == "completed":
                return text, status
            last_error = RuntimeError(text)
        tried = ", ".join(candidates)
        detail = f": {last_error}" if last_error else ""
        return (
            f"DeepSeek Harness run failed on all models (tried {tried}){detail}",
            "error",
        )

    def _run_live(self, goal: str, session_id: str) -> tuple[str, str]:
        try:
            from deepseek_harness import DeepSeekHarness

            os.makedirs(self.workspace, exist_ok=True)
            os.makedirs(self.session_root, exist_ok=True)
            kwargs: dict[str, Any] = dict(
                provider="deepseek-official",
                model=self.model,
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
            return text, "completed"
        except ImportError:
            return "deepseek-harness-sdk is not installed in this environment.", "sdk_missing"
        except Exception as exc:  # noqa: BLE001 — surface engine failures to the UI
            return f"DeepSeek Harness run failed: {exc}", "error"
