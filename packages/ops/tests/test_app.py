"""Ops app integration: auth scoping on every surface, CI intake end-to-end,
chat actions end-to-end, evidence export scoped by principal, schedules
propose-only. All upstreams are real local servers, not mocks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from harness_workflow_connectors.core import IdempotencyStore, sign
from harness_workflow_connectors.plan_gate import ProposePlanGate
from support import (
    Principal,
    fake_orchestrator,
    fake_receiver,
    fixture_archive,
    fixture_records,
    principal_dep,
    slack_action_body,
)

from harness_ops.app import make_ops_app
from harness_ops.chat import ApprovePlanGate, SlackActionHandler, sign_action_token
from harness_ops.ci import GITHUB_SCHEME, CIIntake, build_ci_adapters
from harness_ops.store import OpsStore, ScheduleRow
from harness_ops.usage import verify_delivery_signature

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
GH_SECRET = "gh-secret"
SLACK_SIGNING = "slack-signing"
ACTION_SECRET = "action-secret"

PRINCIPALS = {
    "tok-admin": Principal("admin", "team-a", "admin"),
    "tok-alice": Principal("alice", "team-a", "approver"),
    "tok-bob": Principal("bob", "team-b", "viewer"),
}


@pytest.fixture()
async def service(tmp_path):
    async with fake_orchestrator() as (orch_base, orch_state), \
            fake_receiver() as (receiver_url, receiver_state):
        store = OpsStore(str(tmp_path / "ops.sqlite"))
        archive = fixture_archive(str(tmp_path / "archive.sqlite"))
        propose_gate = ProposePlanGate(orch_base, bearer_token="ops-token")
        approve_gate = ApprovePlanGate(orch_base, bearer_token="approve-token")
        ci = CIIntake(adapters=build_ci_adapters(github_secret=GH_SECRET),
                      gate=propose_gate,
                      idempotency=IdempotencyStore(str(tmp_path / "idem.sqlite")))
        slack = SlackActionHandler(signing_secret=SLACK_SIGNING,
                                   action_secret=ACTION_SECRET, store=store,
                                   approve_gate=approve_gate)
        http = httpx.AsyncClient(timeout=5.0)
        app = make_ops_app(
            store=store, records_provider=fixture_records, archive=archive,
            ci_intake=ci, ci_adapters=ci._adapters, slack_handler=slack,
            schedule_gate=propose_gate, http=http, now_fn=lambda: NOW,
            get_principal_dep=principal_dep(PRINCIPALS),
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                   base_url="http://ops.test")
        yield {
            "client": client, "store": store, "orch": orch_state,
            "receiver_url": receiver_url, "receiver": receiver_state,
            "http": http,
        }
        await client.aclose()
        await http.aclose()
        await propose_gate.aclose()
        await approve_gate.aclose()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_healthz_and_unwired_auth_fail_closed(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    app = make_ops_app(store=store, records_provider=fixture_records)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://ops.test")
    assert (await client.get("/healthz")).status_code == 200
    response = await client.get("/api/v1/ops/usage")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"
    await client.aclose()


async def test_authentication_required(service):
    client = service["client"]
    assert (await client.get("/api/v1/ops/usage")).status_code == 401
    assert (await client.get("/api/v1/ops/usage",
                             headers=auth("bogus"))).status_code == 401


async def test_usage_scope_pinning(service):
    client = service["client"]
    org = (await client.get("/api/v1/ops/usage", headers=auth("tok-admin"))).json()
    assert org["tasks"] == 8 and org["totalCostUsd"] == "24.7000"
    team = (await client.get("/api/v1/ops/usage", headers=auth("tok-alice"))).json()
    assert team["scope"]["team"] == "team-a"
    assert team["tasks"] == 6 and team["totalCostUsd"] == "17.2000"
    forbidden = await client.get("/api/v1/ops/usage?team=team-b",
                                 headers=auth("tok-alice"))
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "TEAM_FORBIDDEN"


async def test_webhook_registry_is_admin_only_and_hides_secret(service):
    client = service["client"]
    body = {"url": service["receiver_url"], "events": ["alert.opened"],
            "allowPrivate": True}
    denied = await client.post("/api/v1/ops/usage/webhooks", json=body,
                               headers=auth("tok-alice"))
    assert denied.status_code == 403

    created = await client.post("/api/v1/ops/usage/webhooks", json=body,
                                headers=auth("tok-admin"))
    assert created.status_code == 201
    payload = created.json()
    assert payload["secret"].startswith("whsec_")
    listing = (await client.get("/api/v1/ops/usage/webhooks",
                                headers=auth("tok-admin"))).json()
    assert len(listing["webhooks"]) == 1
    assert "secret" not in listing["webhooks"][0]


async def test_alert_evaluate_pages_once_and_delivers(service):
    client = service["client"]
    await client.post("/api/v1/ops/usage/webhooks",
                      json={"url": service["receiver_url"],
                            "events": ["alert.opened"], "allowPrivate": True},
                      headers=auth("tok-admin"))

    denied = await client.post("/api/v1/ops/alerts/evaluate", headers=auth("tok-alice"))
    assert denied.status_code == 403

    first = (await client.post("/api/v1/ops/alerts/evaluate",
                               headers=auth("tok-admin"))).json()
    assert first == {"opened": 4, "reminders": 0, "suppressed": 0, "resolved": 0,
                     "delivered": 4, "active": 4}
    assert len(service["receiver"]["received"]) == 4
    request = service["receiver"]["received"][0]
    row = service["store"].webhooks()[0]
    assert verify_delivery_signature(
        row.secret, request["body"], request["headers"]["x-harness-timestamp"],
        request["headers"]["x-harness-signature"], now=float(NOW.timestamp()),
    )

    second = (await client.post("/api/v1/ops/alerts/evaluate",
                                headers=auth("tok-admin"))).json()
    assert second["opened"] == 0 and second["suppressed"] == 4
    assert len(service["receiver"]["received"]) == 4  # no duplicate pages

    admin_list = (await client.get("/api/v1/ops/alerts",
                                   headers=auth("tok-admin"))).json()
    assert len(admin_list["alerts"]) == 4
    alice_list = (await client.get("/api/v1/ops/alerts",
                                   headers=auth("tok-alice"))).json()
    assert {a["rule"] for a in alice_list["alerts"]} == {
        "blocked_run", "expensive_run", "claimed_but_rejected"}
    bob_list = (await client.get("/api/v1/ops/alerts",
                                 headers=auth("tok-bob"))).json()
    assert {a["rule"] for a in bob_list["alerts"]} == {"expensive_run"}


async def test_evidence_export_scoped_and_verifiable(service):
    client = service["client"]
    from harness_ops.evidence import verify_evidence_zip

    org = await client.get("/api/v1/ops/evidence/export", headers=auth("tok-admin"))
    assert org.status_code == 200
    assert org.headers["content-type"] == "application/zip"
    manifest = verify_evidence_zip(org.content)
    assert manifest["counts"]["runs"] == 8
    assert "harness-evidence-" in org.headers["content-disposition"]

    team = await client.get("/api/v1/ops/evidence/export", headers=auth("tok-alice"))
    assert verify_evidence_zip(team.content)["counts"]["runs"] == 6

    forbidden = await client.get("/api/v1/ops/evidence/export?team=team-b",
                                 headers=auth("tok-alice"))
    assert forbidden.status_code == 403


async def test_ci_github_end_to_end(service):
    client = service["client"]
    raw = json.dumps({
        "action": "completed",
        "repository": {"full_name": "acme/app"},
        "workflow_run": {"id": 9, "name": "CI", "conclusion": "failure",
                         "head_branch": "main", "head_sha": "a" * 40,
                         "html_url": "https://gh.example/9", "jobs": []},
    }).encode()
    headers = {"X-GitHub-Event": "workflow_run", "X-GitHub-Delivery": "del-app-1",
               "X-Hub-Signature-256": sign(GITHUB_SCHEME, GH_SECRET, raw, "")}
    response = await client.post("/api/v1/ops/ci/github", content=raw, headers=headers)
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "plan_proposed"
    assert payload["awaitingApproval"] is True
    assert service["orch"]["approve_calls"] == []

    duplicate = await client.post("/api/v1/ops/ci/github", content=raw,
                                  headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"

    bad = dict(headers)
    bad["X-Hub-Signature-256"] = sign(GITHUB_SCHEME, "wrong", raw, "")
    rejected = await client.post("/api/v1/ops/ci/github", content=raw, headers=bad)
    assert rejected.status_code == 401

    unconfigured = await client.post("/api/v1/ops/ci/circleci", content=b"{}",
                                     headers={})
    assert unconfigured.status_code == 404
    assert unconfigured.json()["error"]["code"] == "PROVIDER_UNKNOWN"


async def test_chat_approve_end_to_end(service):
    client = service["client"]
    token = sign_action_token(ACTION_SECRET, plan_id="plan-1", run_id="run-1",
                              action="approve", now=NOW)
    raw, headers = slack_action_body(signing_secret=SLACK_SIGNING,
                                     action_id="harness_approve", token=token,
                                     ts=str(int(NOW.timestamp())))
    headers = {**headers, "x-actor": "alice", "x-actor-roles": "approver"}
    response = await client.post("/api/v1/ops/chat/slack/actions", content=raw,
                                 headers=headers)
    assert response.status_code == 200
    assert response.json()["outcome"]["runId"] == "run-1"
    assert service["orch"]["approve_calls"] == ["plan-1"]

    forbidden = {**headers, "x-actor-roles": "viewer"}
    denied = await client.post("/api/v1/ops/chat/slack/actions", content=raw,
                               headers=forbidden)
    assert denied.status_code == 403


async def test_schedules_surface(service):
    client = service["client"]
    store = service["store"]

    denied = await client.post("/api/v1/ops/schedules",
                               json={"spec": {"kind": "daily", "time": "02:30"},
                                     "tz": "America/New_York",
                                     "goalTemplate": "T"},
                               headers=auth("tok-alice"))
    assert denied.status_code == 403

    bad_tz = await client.post("/api/v1/ops/schedules",
                               json={"spec": {"kind": "daily", "time": "02:30"},
                                     "tz": "Nowhere/Void"},
                               headers=auth("tok-admin"))
    assert bad_tz.status_code == 400
    assert bad_tz.json()["error"]["code"] == "TZ_UNKNOWN"

    created = await client.post(
        "/api/v1/ops/schedules",
        json={"spec": {"kind": "daily", "time": "02:30"},
              "tz": "America/New_York", "goalTemplate": "Nightly checks"},
        headers=auth("tok-admin"),
    )
    assert created.status_code == 201
    assert created.json()["nextRunAt"]

    listing = (await client.get("/api/v1/ops/schedules",
                                headers=auth("tok-admin"))).json()
    assert len(listing["schedules"]) == 1

    no_due = await client.post(
        f"/api/v1/ops/schedules/{created.json()['scheduleId']}/trigger",
        headers=auth("tok-admin"))
    assert no_due.status_code == 200
    assert no_due.json()["results"] == []

    store.insert_schedule(ScheduleRow(
        scheduleId="sch-old", name="Old schedule", teamId="team-a",
        spec={"kind": "daily", "time": "02:30"}, tz="America/New_York",
        goalTemplate="Old maintenance", misfirePolicy="skip", enabled=True,
        createdBy="admin", createdAt=NOW.isoformat(),
        lastRunAt=(NOW - timedelta(days=1)).isoformat(),
    ))
    triggered = await client.post("/api/v1/ops/schedules/sch-old/trigger",
                                  headers=auth("tok-admin"))
    assert triggered.status_code == 200
    results = triggered.json()["results"]
    assert results[0]["status"] == "proposed"
    assert service["orch"]["plans"][results[0]["planId"]]["goal"].count(
        "requires human plan approval") == 1
    runs = store.schedule_runs("sch-old")
    assert runs[0]["planId"] == results[0]["planId"]
    assert service["orch"]["approve_calls"] == []  # propose-only

    disabled = await client.post("/api/v1/ops/schedules/sch-old/disable",
                                 headers=auth("tok-admin"))
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    unknown = await client.post("/api/v1/ops/schedules/sch-old/frobnicate",
                                headers=auth("tok-admin"))
    assert unknown.status_code == 404
