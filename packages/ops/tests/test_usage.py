"""F120 acceptance: usage snapshots share dashboard semantics; webhook
delivery is signed, retried with bounded backoff, and receiver-dedupable."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import httpx
import pytest
from support import fake_receiver, fixture_records

from harness_ops.store import OpsStore
from harness_ops.usage import (
    KNOWN_EVENTS,
    DeliveryDeduper,
    UsageWebhookError,
    deliver_event,
    delivery_signature,
    dispatch_event,
    register_webhook,
    usage_snapshot,
    validate_webhook_url,
    verify_delivery_signature,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def make_store(tmp_path) -> OpsStore:
    return OpsStore(str(tmp_path / "ops.sqlite"))


# ----------------------------------------------------------------- URL policy


def test_url_policy():
    validate_webhook_url("https://hooks.example.com/x")
    with pytest.raises(UsageWebhookError) as exc:
        validate_webhook_url("http://hooks.example.com/x")
    assert exc.value.code == "URL_INSECURE"
    with pytest.raises(UsageWebhookError) as exc:
        validate_webhook_url("https://127.0.0.1/hook")
    assert exc.value.code == "URL_PRIVATE"
    validate_webhook_url("http://127.0.0.1/hook", allow_private=True)
    with pytest.raises(UsageWebhookError) as exc:
        validate_webhook_url("https://10.1.2.3/hook")
    assert exc.value.code == "URL_PRIVATE"


def test_registration_validates_events_and_shows_secret_once(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(UsageWebhookError) as exc:
        register_webhook(store, url="https://h.example.com/x", events=["nope"],
                         team_id=None, created_by="u", now=NOW)
    assert exc.value.code == "EVENT_UNKNOWN"
    with pytest.raises(UsageWebhookError) as exc:
        register_webhook(store, url="https://h.example.com/x", events=[],
                         team_id=None, created_by="u", now=NOW)
    assert exc.value.code == "EVENT_REQUIRED"
    row, secret = register_webhook(
        store, url="https://h.example.com/x", events=["alert.opened"],
        team_id="team-a", created_by="u", now=NOW,
    )
    assert secret.startswith("whsec_")
    assert row.secret == secret
    assert KNOWN_EVENTS == {"alert.opened", "alert.reminder", "alert.resolved",
                            "usage.report"}


# ------------------------------------------------------------------ snapshots


def test_usage_snapshot_matches_dashboard_semantics():
    snapshot = usage_snapshot(fixture_records(), as_of=NOW)
    assert snapshot["tasks"] == 8
    assert snapshot["acceptedChanges"] == 3
    assert snapshot["claimedCompletions"] == 4
    assert snapshot["totalCostUsd"] == "24.7000"
    assert snapshot["costPerTask"] == "3.0875"
    assert snapshot["costPerClaimedCompletion"] == "4.2750"
    assert snapshot["costPerAcceptedChange"] == "4.3667"
    assert snapshot["reworkRate"] == 0.4286
    assert "pr_opened" in snapshot["definitions"]["accepted"]
    assert "claimed_but_not_accepted" in snapshot["definitions"]


def test_usage_snapshot_team_and_window_scopes():
    team_a = usage_snapshot(fixture_records(), team="team-a", as_of=NOW)
    assert team_a["tasks"] == 6
    assert team_a["totalCostUsd"] == "17.2000"
    windowed = usage_snapshot(
        fixture_records(), from_=datetime(2026, 10, 14, tzinfo=UTC), as_of=NOW
    )
    assert windowed["totalCostUsd"] == "14.0000"
    assert windowed["tasks"] == 6


# ------------------------------------------------------------------ delivery


async def _noop_sleeper(seconds: float) -> None:
    return None


async def test_delivery_is_signed_and_verifiable(tmp_path):
    store = make_store(tmp_path)
    async with fake_receiver() as (url, received):
        row, secret = register_webhook(
            store, url=url, events=["alert.opened"], team_id=None, created_by="u",
            now=NOW, allow_private=True,
        )
        async with httpx.AsyncClient() as http:
            result = await deliver_event(
                store=store, webhook=row, event_type="alert.opened",
                payload={"alertId": "alr-1"}, http=http, now=NOW,
                sleeper=_noop_sleeper,
            )
        assert result.status == "delivered" and result.attempts == 1
        request = received["received"][0]
        body = request["body"]
        headers = request["headers"]
        ts = headers["x-harness-timestamp"]
        assert verify_delivery_signature(
            secret, body, ts, headers["x-harness-signature"],
            now=float(NOW.timestamp()),
        )
        assert not verify_delivery_signature(
            secret, body + b" ", ts, headers["x-harness-signature"],
            now=float(NOW.timestamp()),
        )
        assert not verify_delivery_signature(
            secret, body, ts, headers["x-harness-signature"],
            now=float((NOW + __import__("datetime").timedelta(minutes=10)).timestamp()),
        )


async def test_retry_on_5xx_recovers(tmp_path):
    store = make_store(tmp_path)
    slept: list[float] = []

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)

    async with fake_receiver(codes=[503, 200]) as (url, received):
        row, _ = register_webhook(store, url=url, events=["alert.opened"],
                                  team_id=None, created_by="u", now=NOW,
                                  allow_private=True)
        async with httpx.AsyncClient() as http:
            result = await deliver_event(
                store=store, webhook=row, event_type="alert.opened", payload={},
                http=http, now=NOW, sleeper=sleeper,
            )
        assert result.status == "delivered" and result.attempts == 2
        assert len(received["received"]) == 2
        assert slept == [2.0]
        history = store.delivery_history(row.webhookId)
        assert [(h["status"], h["attempt"]) for h in history] == [("delivered", 2)]


async def test_bounded_retries_then_failure(tmp_path):
    store = make_store(tmp_path)
    async with fake_receiver(codes=[500, 500, 500]) as (url, received):
        row, _ = register_webhook(store, url=url, events=["alert.opened"],
                                  team_id=None, created_by="u", now=NOW,
                                  allow_private=True)
        async with httpx.AsyncClient() as http:
            result = await deliver_event(
                store=store, webhook=row, event_type="alert.opened", payload={},
                http=http, now=NOW, sleeper=_noop_sleeper,
            )
        assert result.status == "failed" and result.attempts == 3
        assert len(received["received"]) == 3
        assert result.responseCode == 500


async def test_4xx_is_terminal_single_attempt(tmp_path):
    store = make_store(tmp_path)
    async with fake_receiver(codes=[404]) as (url, received):
        row, _ = register_webhook(store, url=url, events=["alert.opened"],
                                  team_id=None, created_by="u", now=NOW,
                                  allow_private=True)
        async with httpx.AsyncClient() as http:
            result = await deliver_event(
                store=store, webhook=row, event_type="alert.opened", payload={},
                http=http, now=NOW, sleeper=_noop_sleeper,
            )
        assert result.status == "failed" and result.attempts == 1
        assert len(received["received"]) == 1


async def test_stable_delivery_id_is_receiver_dedupable(tmp_path):
    store = make_store(tmp_path)
    deduper = DeliveryDeduper()
    async with fake_receiver() as (url, received):
        row, _ = register_webhook(store, url=url, events=["alert.opened"],
                                  team_id=None, created_by="u", now=NOW,
                                  allow_private=True)
        async with httpx.AsyncClient() as http:
            for _ in (1, 2):
                result = await deliver_event(
                    store=store, webhook=row, event_type="alert.opened", payload={},
                    http=http, now=NOW, sleeper=_noop_sleeper, delivery_id="dlv-fixed",
                )
                assert result.status == "delivered"
        assert [deduper.first_seen(r["headers"]["x-harness-delivery"])
                for r in received["received"]] == [True, False]


def test_deduper_reference_helper():
    deduper = DeliveryDeduper()
    assert deduper.first_seen("a") is True
    assert deduper.first_seen("a") is False
    assert deduper.first_seen("b") is True


async def test_dispatch_fans_out_by_event_and_team(tmp_path):
    store = make_store(tmp_path)
    async with fake_receiver() as (url_a, received_a), \
            fake_receiver() as (url_b, received_b):
        row_a, _ = register_webhook(store, url=url_a, events=["alert.opened"],
                                    team_id=None, created_by="u", now=NOW,
                                    allow_private=True)
        register_webhook(store, url=url_b, events=["usage.report"],
                         team_id=None, created_by="u", now=NOW, allow_private=True)
        row_c, _ = register_webhook(store, url=url_b, events=["alert.opened"],
                                    team_id="team-b", created_by="u", now=NOW,
                                    allow_private=True)
        async with httpx.AsyncClient() as http:
            for_team_a = await dispatch_event(
                store=store, event_type="alert.opened", payload={}, http=http,
                now=NOW, team_id="team-a", sleeper=_noop_sleeper,
            )
        assert [r.webhookId for r in for_team_a] == [row_a.webhookId]
        assert len(received_a["received"]) == 1
        assert received_b["received"] == []

        async with httpx.AsyncClient() as http:
            global_dispatch = await dispatch_event(
                store=store, event_type="alert.opened", payload={}, http=http,
                now=NOW, team_id=None, sleeper=_noop_sleeper,
            )
        assert {r.webhookId for r in global_dispatch} == {row_a.webhookId,
                                                          row_c.webhookId}


def test_signature_construction_shape():
    stamp = "1700000000"
    sig = delivery_signature("whsec_abc", b"{}", stamp)
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64
    assert sig == delivery_signature("whsec_abc", BytesIO(b"{}").read(), stamp)
