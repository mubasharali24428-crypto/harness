from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from harness_identity.audit_emit import _emit
from harness_identity.auth import hash_token
from harness_identity.models import AuthSession, Team, TeamMember, User
from harness_identity.webhooks import IntegrationWebhook, WebhookDelivery, deliver_due


async def _seed(factory, email: str, role: str = "admin") -> dict[str, str]:
    async with factory() as db:
        team = Team(name=f"team-{email}")
        user = User(email=email, name=email.split("@")[0])
        db.add_all([team, user])
        await db.flush()
        db.add(TeamMember(team_id=team.id, user_id=user.id, display_name=user.name, role=role))
        token = secrets.token_urlsafe(32)
        db.add(
            AuthSession(
                token_hash=hash_token(token),
                user_id=user.id,
                team_id=team.id,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        await db.commit()
        return {"team_id": team.id, "user_id": user.id, "token": token}


def _url(team_id: str) -> str:
    return f"/api/v1/teams/{team_id}/settings/integrations/v1/webhooks"


@pytest.mark.asyncio
async def test_webhook_admin_scope_rotation_and_version_conflict(client, monkeypatch):
    monkeypatch.setenv("HARNESS_WEBHOOK_ALLOWED_HOSTS", "hooks.example.test")
    factory = client.app.state.session_factory
    admin = await _seed(factory, "webhook-admin@example.test")
    engineer = await _seed(factory, "webhook-engineer@example.test", "engineer")
    headers = {"Authorization": f"Bearer {admin['token']}"}
    response = await client.post(
        _url(admin["team_id"]),
        headers=headers,
        json={"endpointUrl": "https://hooks.example.test/events", "eventTypes": ["run.paused"]},
    )
    assert response.status_code == 201
    created = response.json()
    first_secret = created["signingSecret"]
    assert len(first_secret) >= 40
    assert created["version"] == 1
    assert (
        "signingSecret"
        not in (await client.get(_url(admin["team_id"]), headers=headers)).json()["items"][0]
    )

    async with factory() as db:
        stored = await db.get(IntegrationWebhook, created["id"])
        assert stored.secret_encrypted != first_secret
        assert client.app.state.vault.decrypt(stored.secret_encrypted) == first_secret

    denied = await client.post(
        _url(engineer["team_id"]),
        headers={"Authorization": f"Bearer {engineer['token']}"},
        json={"endpointUrl": "https://hooks.example.test/events", "eventTypes": ["run.paused"]},
    )
    assert denied.status_code == 403
    cross_team = await client.get(_url(engineer["team_id"]), headers=headers)
    assert cross_team.status_code == 404
    unlisted = await client.post(
        _url(admin["team_id"]),
        headers=headers,
        json={"endpointUrl": "https://other.example.test/events", "eventTypes": ["run.paused"]},
    )
    assert unlisted.status_code == 422

    stale = await client.patch(
        f"{_url(admin['team_id'])}/{created['id']}",
        headers=headers,
        json={"active": False, "expectedVersion": 9},
    )
    assert stale.status_code == 409
    disabled = await client.patch(
        f"{_url(admin['team_id'])}/{created['id']}",
        headers=headers,
        json={"active": False, "expectedVersion": 1},
    )
    assert disabled.status_code == 200 and disabled.json()["version"] == 2
    rotated = await client.post(
        f"{_url(admin['team_id'])}/{created['id']}/rotate-secret", headers=headers
    )
    assert rotated.status_code == 200 and rotated.json()["signingSecret"] != first_secret
    assert rotated.json()["version"] == 3
    deleted = await client.delete(f"{_url(admin['team_id'])}/{created['id']}", headers=headers)
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_audit_fanout_is_id_only_signed_and_retried(client, monkeypatch):
    monkeypatch.setenv("HARNESS_WEBHOOK_ALLOWED_HOSTS", "hooks.example.test")
    factory = client.app.state.session_factory
    actor = await _seed(factory, "webhook-events@example.test")
    headers = {"Authorization": f"Bearer {actor['token']}"}
    created = await client.post(
        _url(actor["team_id"]),
        headers=headers,
        json={"endpointUrl": "https://hooks.example.test/events", "eventTypes": ["run.paused"]},
    )
    assert created.status_code == 201
    signing_secret = created.json()["signingSecret"]

    async with factory() as db:
        await _emit(
            db,
            "run.paused",
            user_id=actor["user_id"],
            team_id=actor["team_id"],
            target_type="run",
            target_id="run-7",
            detail={"prompt": "do-not-forward", "state": "paused"},
        )
        delivery = (
            await db.execute(
                select(WebhookDelivery).where(WebhookDelivery.team_id == actor["team_id"])
            )
        ).scalar_one()
        assert delivery.payload["eventType"] == "run.paused"
        assert delivery.payload["resource"] == {"type": "run", "id": "run-7"}
        assert "prompt" not in json.dumps(delivery.payload)

    calls: list[tuple[bytes, dict[str, str]]] = []

    async def sender(_url: str, body: bytes, outgoing_headers: dict[str, str]) -> int:
        calls.append((body, outgoing_headers))
        return 503 if len(calls) == 1 else 204

    now = datetime.now(UTC) + timedelta(seconds=1)
    first = await deliver_due(factory, vault=client.app.state.vault, now=now, sender=sender)
    assert first == {"delivered": 0, "retrying": 1, "failed": 0, "skipped": 0}
    signature = calls[0][1]["X-Harness-Signature"]
    timestamp = int(signature.split(",", 1)[0].split("=", 1)[1])
    digest = hmac.new(
        signing_secret.encode(), str(timestamp).encode() + b"." + calls[0][0], hashlib.sha256
    ).hexdigest()
    assert signature == f"t={timestamp},v1={digest}"
    assert calls[0][1]["Idempotency-Key"] == calls[0][1]["X-Harness-Delivery"]

    second = await deliver_due(
        factory,
        vault=client.app.state.vault,
        now=now + timedelta(seconds=31),
        sender=sender,
    )
    assert second["delivered"] == 1, second
    assert calls[0][1]["X-Harness-Delivery"] == calls[1][1]["X-Harness-Delivery"]
    async with factory() as db:
        delivery = (
            await db.execute(
                select(WebhookDelivery).where(WebhookDelivery.team_id == actor["team_id"])
            )
        ).scalar_one()
        assert delivery.state == "delivered" and delivery.attempts == 2


def test_webhook_migration_has_delivery_uniqueness_and_bounded_state():
    path = Path(__file__).resolve().parents[1] / "migrations" / "0051_webhook_integrations.sql"
    sql = path.read_text()
    assert "CREATE TABLE integration_webhooks" in sql
    assert "CREATE TABLE integration_webhook_deliveries" in sql
    assert "uq_webhook_audit_event UNIQUE(webhook_id, audit_seq)" in sql
    assert "CHECK (state IN ('pending', 'sending', 'delivered', 'failed'))" in sql


@pytest.mark.asyncio
async def test_slack_format_is_an_explicit_choice_and_sends_one_id_only_line(client, monkeypatch):
    """Part 49.003: a chat webhook only exists when a team admin asks for payloadFormat=slack; it
    posts {"text": ...} built from the closed id payload, still signed, retried and host-gated."""
    monkeypatch.setenv("HARNESS_WEBHOOK_ALLOWED_HOSTS", "hooks.example.test")
    factory = client.app.state.session_factory
    actor = await _seed(factory, "webhook-chat@example.test")
    headers = {"Authorization": f"Bearer {actor['token']}"}
    default = await client.post(
        _url(actor["team_id"]), headers=headers,
        json={"endpointUrl": "https://hooks.example.test/plain", "eventTypes": ["run.paused"]},
    )
    assert default.status_code == 201 and default.json()["payloadFormat"] == "harness"
    chat = await client.post(
        _url(actor["team_id"]), headers=headers,
        json={"endpointUrl": "https://hooks.example.test/chat", "eventTypes": ["run.paused"],
              "payloadFormat": "slack"},
    )
    assert chat.status_code == 201 and chat.json()["payloadFormat"] == "slack"
    bogus = await client.post(
        _url(actor["team_id"]), headers=headers,
        json={"endpointUrl": "https://hooks.example.test/x", "eventTypes": ["run.paused"],
              "payloadFormat": "teams"},
    )
    assert bogus.status_code == 422

    async with factory() as db:
        await _emit(db, "run.paused", user_id=actor["user_id"], team_id=actor["team_id"],
                    target_type="run", target_id="run-9", detail={"prompt": "do-not-forward"})

    sent: dict[str, tuple[bytes, dict[str, str]]] = {}

    async def sender(url: str, body: bytes, outgoing_headers: dict[str, str]) -> int:
        sent[url.rsplit("/", 1)[-1]] = (body, outgoing_headers)
        return 200

    now = datetime.now(UTC) + timedelta(seconds=1)
    totals = await deliver_due(factory, vault=client.app.state.vault, now=now, sender=sender)
    assert totals["delivered"] == 2
    message = json.loads(sent["chat"][0])
    assert list(message) == ["text"]
    assert message["text"].startswith("Harness: run.paused on run run-9 (team ")
    assert "do-not-forward" not in sent["chat"][0].decode()
    assert sent["chat"][1]["X-Harness-Signature"].startswith("t=")  # still signed
    assert json.loads(sent["plain"][0])["eventType"] == "run.paused"  # default unchanged
