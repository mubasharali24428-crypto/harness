"""Section 6 AI participant — invocation rules + team-BYO-key LLM client.

H-002 (Mission 12 lane E1): ``@ai`` becomes a REAL model call. When the
caller's team has a stored provider key (Section 1's ``api_keys`` store,
Fernet-encrypted at rest), the reply comes from Section 3's model router —
the decrypted key travels server-side only (request body to S3, never
logged, never echoed). The regex rules engine remains ONLY as an explicit
fallback for teams without a usable key; its replies say so honestly.

Secrets law (repo hard rule): raw keys never enter logs, exceptions, or
model context beyond the single POST body field S3 consumes.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

AI_INVOKE_PATTERN = re.compile(
    r"(^@ai\b|/ask-ai\b|^/research\b|^/summary\b|^/brief\b)", re.IGNORECASE
)
ELABORATE_PATTERN = re.compile(
    r"\b(elaborate|explain more|go deeper|more detail|expand on)\b",
    re.IGNORECASE,
)

DEFAULT_MAX_WORDS = 150
ELABORATE_MAX_WORDS = 500

logger = logging.getLogger("harness_meeting.ai_participant")

# Section 3 model router (H-002). Overridable via env so deployments can point
# at a different router instance without a code change.
MODELS_BASE_URL: str | None = None  # set from env by MeetingService wiring

# Honest-fallback copy (single source of truth; tests + UI rely on the wording).
NO_KEY_CONFIGURED_REPLY = "no AI key configured — add one in Settings → API keys"


class TeamAIClient:
    """Calls S3 ``POST /api/v1/models/call`` with the team's BYO key.

    The decrypted key is attached as the ``apiKey`` body field — S3's provider
    layer consumes it server-side. It is NEVER placed in headers, logs, or
    exception text (``_scrub`` is a belt-and-braces filter on anything we
    would surface).

    Sync facade on purpose: Section 6's routes are plain ``def`` handlers, so
    they already run in Starlette's threadpool with no running event loop;
    ``asyncio.run`` gives each completion an isolated loop (no cross-loop
    engine/client reuse).
    """

    def __init__(
        self,
        models_base_url: str,
        api_key: str,
        agent_id: str = "ai-participant",
        model: str | None = None,
        timeout_seconds: float = 30.0,
        transport: Any | None = None,
    ) -> None:
        self.models_base_url = models_base_url.rstrip("/")
        self.api_key = api_key
        self.agent_id = agent_id
        self.model = model  # None => S3's role default decides
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    # -- public API ---------------------------------------------------------

    def complete(self, question: str, context: str | None = None) -> str:
        """One-shot completion; returns model text or an HONEST error reply."""
        try:
            return asyncio.run(self._acomplete(question, context))
        except Exception as exc:  # noqa: BLE001 — every failure becomes honest copy
            return self.honest_error_reply(exc)

    async def _acomplete(self, question: str, context: str | None) -> str:
        import httpx

        messages: list[dict[str, str]] = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": question})
        payload: dict[str, Any] = {
            "agentId": self.agent_id,
            "role": "primary",
            "messages": messages,
            "apiKey": self.api_key,
        }
        if self.model:
            payload["model"] = self.model
        logger.debug("models/call dispatch agentId=%s", self.agent_id)
        # httpx enforces its timeout inside the real connection pool; injected
        # test transports bypass that, so the deadline is enforced HERE for
        # every path (prod included) with the same honest outcome.
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self._transport
            ) as client:
                resp = await asyncio.wait_for(
                    client.post(f"{self.models_base_url}/api/v1/models/call", json=payload),
                    timeout=self.timeout_seconds,
                )
        except (httpx.TimeoutException, TimeoutError) as exc:
            # Normalize transport timeouts so the honest reply is exact.
            raise TimeoutError("model router timed out") from exc
        if resp.status_code < 400:
            data = self._safe_json(resp)
            text = data.get("text") if isinstance(data, dict) else None
            if isinstance(text, str) and text.strip():
                return text.strip()
            logger.warning("models/call returned no text (status=%s)", resp.status_code)
            raise RuntimeError("model router returned no completion")
        code, message = self._error_fields(resp)
        raise TeamAIError(resp.status_code, code, message)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _safe_json(resp: Any) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error_fields(resp: Any) -> tuple[str, str]:
        """Pull (code, message) from contract/FastAPI envelopes without ever
        trusting provider text to be secret-free."""
        try:
            data = resp.json()
        except ValueError:
            return "", ""
        for holder in ("error",):
            inner = data.get(holder) if isinstance(data, dict) else None
            if isinstance(inner, dict):
                detail = inner.get("detail")
                if isinstance(detail, dict) and isinstance(inner.get("error"), dict):
                    inner = inner["error"]
                code = str(inner.get("code") or "")
                message = str(inner.get("message") or "")
                return code, message
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, str):
            return "", detail
        return "", ""

    def honest_error_reply(self, exc: Exception) -> str:
        """Map any failure to user-facing copy that names NO secret material."""
        status = getattr(exc, "status", None)
        code = getattr(exc, "code", "") or ""
        raw_message = str(exc)
        scrubbed = self._scrub(raw_message)
        lowered = f"{code} {scrubbed}".lower()
        if status == 429 or "rate limit" in lowered or "rate_limit" in lowered:
            return (
                "The AI provider is rate limiting this team's key right now — "
                "try again shortly."
            )
        if status in (401, 403) or "invalid" in lowered and "key" in lowered:
            return (
                "The team's stored AI key was rejected by the provider "
                "(invalid or unauthorized). Check it in Settings → API keys."
            )
        if isinstance(exc, asyncio.TimeoutError) or "timed out" in lowered:
            return "The AI service didn't answer in time — please try again."
        if isinstance(exc, TeamAIError):
            return (
                f"The AI service could not answer this request "
                f"(upstream error {getattr(exc, 'status', 'unknown')})."
            )
        return (
            "The AI service is temporarily unavailable — your request was not "
            "completed. Please try again."
        )

    def _scrub(self, text: str) -> str:
        """Remove our own secret material from anything we'd surface."""
        clean = text
        if self.api_key:
            clean = clean.replace(self.api_key, "[redacted]")
        return clean


class TeamAIError(Exception):
    """Non-2xx from the model router, normalized to status/code/message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message or code or f"HTTP {status}")
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Team BYO-key resolution (server-side only)
# ---------------------------------------------------------------------------


async def pick_team_key(
    session_factory: Any,
    vault: Any,
    provider: str,
    team_id: str | None = None,
) -> str | None:
    """Latest active key for `provider` belonging to `team_id`, decrypted.

    Reads ONLY frozen table/column names from Section 1's ``api_keys`` store
    via raw SQL — cross-section DB access never imports S1 models. Returns
    None when the team has no usable key (the honest-fallback case). The
    plaintext exists solely inside this call chain.
    """

    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    _sql_text(
                        "SELECT id, provider, key_encrypted FROM api_keys "
                        "WHERE provider = :provider AND revoked_at IS NULL "
                        "AND (:team_id::text IS NULL OR team_id = :team_id::text) "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"provider": provider, "team_id": team_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        try:
            return vault.decrypt(row["key_encrypted"])
        except Exception as exc:  # noqa: BLE001 — malformed key => honest failure
            # Never log ciphertext or plaintext: name the condition, not the value.
            logger.error("stored AI key could not be decrypted (%s)", type(exc).__name__)
            raise MalformedTeamKeyError(type(exc).__name__) from exc


def _sql_text(sql: str) -> Any:
    """``sqlalchemy.text`` when available (prod), else the raw string.

    The prod image installs Section 1 (which pulls sqlalchemy); lean dev/test
    environments may not have it, and the raw string still serves duck-typed
    fakes. No driver-specific behavior depends on this choice.
    """
    try:
        from sqlalchemy import text

        return text(sql)
    except ImportError:  # pragma: no cover - prod always has sqlalchemy
        return sql


class MalformedTeamKeyError(Exception):
    """The stored key exists but cannot be decrypted/used. Never carries the
    secret itself — only exception-type metadata."""


class AIParticipant:
    """Server-side AI participant that is muted by default.

    Rules (from brief §76-84):
    - Muted by default; speaks only when explicitly invoked.
    - Never interrupts.
    - Answers precisely first; elaborates only on request.
    - Avoids long default answers.
    """

    @staticmethod
    def is_ai_invoked(message_content: str) -> bool:
        """Returns True only if the message explicitly invokes the AI."""
        return bool(AI_INVOKE_PATTERN.search(message_content))

    @staticmethod
    def should_elaborate(message_content: str) -> bool:
        """Returns True only if user explicitly asks for elaboration."""
        return bool(ELABORATE_PATTERN.search(message_content))

    @staticmethod
    def extract_ai_command(message_content: str) -> tuple[str, str]:
        """Extracts an AI command and its arguments from a message.

        Supported commands:
        - @ai <question>        -> ("ask", "<question>")
        - /ask-ai <question>    -> ("ask", "<question>")
        - /research <topic>     -> ("research", "<topic>")
        - /summary              -> ("summary", "")
        - /brief                -> ("brief", "")

        Returns ("unknown", "") for unrecognised commands.
        """
        content = message_content.strip()

        if content.startswith("/research"):
            args = content[len("/research") :].strip()
            return ("research", args)
        if content.startswith("/summary"):
            return ("summary", "")
        if content.startswith("/brief"):
            return ("brief", "")
        if content.lower().startswith("@ai"):
            args = content[3:].strip()
            return ("ask", args)
        if content.lower().startswith("/ask-ai"):
            args = content[len("/ask-ai") :].strip()
            return ("ask", args)

        return ("unknown", "")

    @staticmethod
    def cap_response_length(text: str, elaborate: bool = False) -> str:
        """Enforces response length limits."""
        max_words = ELABORATE_MAX_WORDS if elaborate else DEFAULT_MAX_WORDS
        words = text.split()
        if len(words) <= max_words:
            return text
        return " ".join(words[:max_words]) + "..."
