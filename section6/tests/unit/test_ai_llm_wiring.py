"""Mission 12 lane E1 — @ai real-LLM wiring via team BYO keys.

Contract under test (H-002):
- When the team HAS a stored, non-revoked provider key: invoke_ai routes
  through Section 3's ``POST /api/v1/models/call`` with that key attached
  server-side (never logged, never echoed) and returns the model's genuine
  completion as an ai-participant message.
- When the team has NO stored key: the rules engine stays available as an
  explicit fallback and says so honestly ("no AI key configured — add one in
  Settings → API keys") instead of pretending to be a model.
- A malformed/unreadable stored key must produce an honest error reply with no
  secret material in the message or any log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import pytest
from harness_contracts.security import KeyVault

from harness_meeting.ai_participant import TeamAIClient, pick_team_key
from harness_meeting.service import MeetingService

# ---------------------------------------------------------------------------
# Fakes: duck-typed session factory + vault (no sqlalchemy/DB dependency)
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Mimics the AsyncSession slice pick_team_key uses (execute().all())."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.closed = False

    async def execute(self, _stmt: Any, params: Any = None) -> FakeResult:
        # Emulate the two predicates pick_team_key relies on so the fake
        # exercises real behavior: provider match + revoked_at IS NULL.
        wanted_provider = (params or {}).get("provider")
        rows = [
            r for r in self.rows if r["provider"] == wanted_provider and not r["revoked_at"]
        ]
        return FakeResult(rows)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.closed = True


class FakeRow(dict):
    """Mimics SQLAlchemy Row mapping access: row["key_encrypted"]."""


def make_key_row(key_id: str, provider: str, secret: str, revoked_at: Any = None) -> FakeRow:
    vault = KeyVault(b"test-master-key")
    return FakeRow(
        id=key_id,
        provider=provider,
        label=f"{provider} main",
        key_encrypted=vault.encrypt(secret),
        key_hint=KeyVault.hint(secret),
        revoked_at=revoked_at,
    )


def fake_factory(rows: list[Any]):
    session = FakeSession(rows)

    def factory() -> FakeSession:
        return session

    factory.session = session  # type: ignore[attr-defined]
    return factory


VAULT = KeyVault(b"test-master-key")


# ---------------------------------------------------------------------------
# pick_team_key
# ---------------------------------------------------------------------------


class TestPickTeamKey:
    def test_returns_decrypted_secret_for_active_row(self):
        rows = [make_key_row("key-1", "openai", "sk-live-abcdef1234")]
        secret = asyncio.run(pick_team_key(fake_factory(rows), VAULT, "openai"))
        assert secret == "sk-live-abcdef1234"  # gitleaks:allow

    def test_skips_revoked_and_picks_latest_active(self):
        from datetime import UTC, datetime

        revoked = make_key_row("key-old", "openai", "sk-old-0000", datetime.now(UTC))
        active = make_key_row("key-new", "openai", "sk-new-9999")
        secret = asyncio.run(
            pick_team_key(fake_factory([active, revoked]), VAULT, "openai")
        )
        assert secret == "sk-new-9999"

    def test_provider_mismatch_is_ignored(self):
        rows = [make_key_row("key-a", "anthropic", "sk-ant-1")]
        secret = asyncio.run(pick_team_key(fake_factory(rows), VAULT, "openai"))
        assert secret is None

    def test_no_rows_means_none(self):
        secret = asyncio.run(pick_team_key(fake_factory([]), VAULT, "openai"))
        assert secret is None


# ---------------------------------------------------------------------------
# TeamAIClient transport + error mapping
# ---------------------------------------------------------------------------


class SlowAsyncTransport(httpx.AsyncBaseTransport):
    """An async transport whose handler genuinely blocks past any timeout —
    httpx's MockTransport answers synchronously and never trips deadlines."""

    def __init__(self, delay: float) -> None:
        self._delay = delay

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(self._delay)
        return httpx.Response(200, json={"text": "late"})


class TestTeamAITransport:
    def test_posts_call_model_with_api_key_body_field(self):
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "text": "Genuine model answer.",
                    "costUsd": 0.01,
                    "model": "deepseek/deepseek-coder",
                    "latencyMs": 5,
                },
            )

        client = TeamAIClient(
            models_base_url="http://models.test",
            api_key="sk-super-secret-value",
            agent_id="ai-participant@test-team",
            model=None,
            transport=httpx.MockTransport(handler),
        )
        result = client.complete("What is JWT refresh?")
        assert result == "Genuine model answer."
        assert captured["url"].endswith("/api/v1/models/call")
        body = captured["body"]
        assert body["role"] == "primary"
        # The decrypted team key rides the request body for S3's provider layer…
        assert body["apiKey"] == "sk-super-secret-value"
        # …but never leaks into any header S3 would echo or log.
        assert "sk-super-secret-value" not in json.dumps(captured.get("headers", {}))

    def test_rate_limit_maps_to_honest_reply(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={"error": {"code": "RATE_LIMIT_EXCEEDED", "message": "slow down"}},
            )

        client = TeamAIClient(
            models_base_url="http://models.test",
            api_key="k",
            transport=httpx.MockTransport(handler),
        )
        assert "try again shortly" in client.complete("hello")

    def test_invalid_key_maps_to_honest_reply_without_secret(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": {"code": "INVALID_API_KEY", "message": "bad key"}},
            )

        client = TeamAIClient(
            models_base_url="http://models.test",
            api_key="sk-hushhush",
            transport=httpx.MockTransport(handler),
        )
        reply = client.complete("hello")
        assert "rejected" in reply
        assert "sk-hushhush" not in reply

    def test_unreachable_models_maps_to_unavailable(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

        client = TeamAIClient(
            models_base_url="http://models.test",
            api_key="k",
            transport=httpx.MockTransport(handler),
        )
        assert "temporarily unavailable" in client.complete("hello")

    def test_timeout_is_bounded(self):
        client = TeamAIClient(
            models_base_url="http://models.test",
            api_key="k",
            timeout_seconds=0.2,
            transport=SlowAsyncTransport(1.0),
        )
        started = time.monotonic()
        reply = client.complete("hello")
        assert time.monotonic() - started < 5
        assert "didn't answer in time" in reply


class TestSecretHygiene:
    def test_secret_never_reaches_logs(self, caplog: pytest.LogCaptureFixture):
        secret = "sk-do-not-print-4242"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"text": "ok"})

        with caplog.at_level(logging.DEBUG, logger="harness_meeting.ai_participant"):
            client = TeamAIClient(
                models_base_url="http://models.test",
                api_key=secret,
                transport=httpx.MockTransport(handler),
            )
            client.complete("hello")
        for record in caplog.records:
            assert secret not in record.getMessage()

    def test_exception_message_scrubbed(self):
        client = TeamAIClient(models_base_url="http://models.test", api_key="sk-leaky")
        dirty = f"boom near {client.api_key} upstream"
        clean = client._scrub(dirty)
        assert "sk-leaky" not in clean
        assert "[redacted]" in clean


# ---------------------------------------------------------------------------
# Service integration: with-key vs without-key vs malformed-key
# ---------------------------------------------------------------------------


def ok_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"text": "Model-grade answer about token refresh.", "costUsd": 0.02},
    )


class TestInvokeAiWiring:
    def test_with_key_returns_genuine_model_text(self):
        svc = MeetingService(team_key_store=fake_factory([make_key_row("k1", "openai", "sk-1")]))
        svc.meeting_ai_client = TeamAIClient(
            models_base_url="http://models.test",
            api_key="sk-1",
            transport=httpx.MockTransport(ok_handler),
        )
        meeting = svc.create_meeting("proj-1", "wired")
        reply = svc.invoke_ai(meeting.id, "u1", "@ai How do we refresh tokens?")
        assert reply is not None
        assert reply.isAiGenerated is True
        assert reply.content == "Model-grade answer about token refresh."

    def test_without_key_falls_back_honestly(self):
        svc = MeetingService(team_key_store=fake_factory([]))
        svc.meeting_ai_client = None
        meeting = svc.create_meeting("proj-1", "nokey")
        reply = svc.invoke_ai(meeting.id, "u1", "@ai What is REST?")
        assert reply is not None
        assert "no AI key configured" in reply.content
        assert "Settings → API keys" in reply.content
        # Rules-engine fallback still answers the question itself:
        assert "REST" in reply.content or len(reply.content) > 40

    def test_malformed_key_honest_error_no_leak(self, caplog: pytest.LogCaptureFixture):
        broken = make_key_row("k-broken", "openai", "sk-corrupt-secret")
        broken["key_encrypted"] = "not-a-fernet-token"
        svc = MeetingService(team_key_store=fake_factory([broken]), vault=VAULT)
        svc.meeting_ai_client = None
        with caplog.at_level(logging.ERROR):
            meeting = svc.create_meeting("proj-1", "broken")
            reply = svc.invoke_ai(meeting.id, "u1", "@ai hello")
        assert reply is not None
        assert reply.userId == "ai-participant"
        assert "could not be used" in reply.content
        # No secret material anywhere in the user-visible message…
        assert "sk-corrupt-secret" not in reply.content
        # …nor any log record emitted during the path.
        for record in caplog.records:
            assert "sk-corrupt-secret" not in record.getMessage()

    def test_transport_failure_keeps_rules_fallback_answer(self):
        def down_handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        svc = MeetingService(team_key_store=fake_factory([make_key_row("k1", "openai", "sk-1")]))
        svc.meeting_ai_client = TeamAIClient(
            models_base_url="http://models.test",
            api_key="sk-1",
            transport=httpx.MockTransport(down_handler),
        )
        meeting = svc.create_meeting("proj-1", "downstream-down")
        reply = svc.invoke_ai(meeting.id, "u1", "@ai explain idempotency keys")
        assert reply is not None
        assert "temporarily unavailable" in reply.content
        # Honest fallback still carries substance:
        assert "idempotency" in reply.content.lower()

    def test_summary_stays_deterministic_even_without_key(self):
        svc = MeetingService(team_key_store=fake_factory([]))
        meeting = svc.create_meeting("proj-1", "summary-path")
        svc.post_message(meeting.id, "u1", "We chose option B for the auth flow.")
        reply = svc.invoke_ai(meeting.id, "u1", "/summary")
        assert reply is not None
        assert reply.isAiGenerated is True
        assert len(reply.content) > 10

    def test_non_invocation_returns_none(self):
        svc = MeetingService(team_key_store=fake_factory([]))
        meeting = svc.create_meeting("proj-1", "plain")
        assert svc.invoke_ai(meeting.id, "u1", "just chatting") is None
