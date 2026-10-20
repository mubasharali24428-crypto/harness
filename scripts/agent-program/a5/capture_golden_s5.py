"""Capture deterministic S5 evidence: ops API goldens (usage, alerts+delivery,
evidence export, CI intake, chat actions, schedules) + DST grid.

Run with the extensions venv (has harness-ops face installed? use PYTHONPATH):
    packages/extensions/.venv/bin/python scripts/agent-program/a5/capture_golden_s5.py

Everything is pinned to a fixed NOW and in-process ASGI transports, so ids,
signatures, and manifests are deterministic. Generated uuids are avoided by
inserting fixed webhook rows and passing explicit delivery ids.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from harness_ops.app import make_ops_app
from harness_ops.chat import (
    ApprovePlanGate,
    SlackActionHandler,
    sign_action_token,
)
from harness_ops.ci import GITHUB_SCHEME, CIIntake, build_ci_adapters
from harness_ops.evidence import verify_evidence_zip
from harness_ops.schedule import next_occurrence
from harness_ops.store import OpsStore, ScheduleRow, WebhookRow
from harness_ops.usage import deliver_event, delivery_signature
from harness_telemetry import Archive, build_records, fixtures, parse_cost, parse_stream
from harness_workflow_connectors.core import IdempotencyStore, sign
from harness_workflow_connectors.plan_gate import ProposePlanGate
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/agent-program/a5/session-5/interop/golden"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
GH_SECRET = "golden-gh-secret"
GL_SECRET = "golden-gl-secret"
SLACK_SIGNING = "golden-slack-signing"
ACTION_SECRET = "golden-action-secret"


@dataclass
class Principal:
    userId: str
    teamId: str
    role: str


TOKENS = {
    "admin-token": Principal("u-admin", "team-a", "admin"),
    "alice-token": Principal("u-alice", "team-a", "member"),
    "bob-token": Principal("u-bob", "team-b", "member"),
}


def principal_dep(mapping: dict[str, Principal]):
    def resolve(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return mapping.get(token)

    return resolve


def write(name: str, payload) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"  {name}")


def write_bytes(name: str, payload: bytes) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    print(f"  {name}")


_AUTO_ID_MAP: dict[str, str] = {}


def _normalize_delivery_ids(payload):
    """Auto-generated delivery ids carry uuid4 hex; replace them with their
    deterministic sequence position (explicitly passed ids like dlv-golden-1
    are kept verbatim) so captures are byte-reproducible."""
    def norm(value):
        if isinstance(value, str) and value.startswith("dlv-") \
                and value != "dlv-golden-1":
            if value not in _AUTO_ID_MAP:
                _AUTO_ID_MAP[value] = f"dlv-auto-{len(_AUTO_ID_MAP) + 1}"
            return _AUTO_ID_MAP[value]
        if isinstance(value, dict):
            return {k: norm(v) for k, v in value.items()}
        if isinstance(value, list):
            return [norm(v) for v in value]
        return value

    return norm(payload)


def build_orchestrator():
    state: dict = {"plans": {}, "approve_calls": [], "plan_seq": 0, "run_seq": 0}

    async def propose(request: Request) -> JSONResponse:
        state["plan_seq"] += 1
        plan_id = f"plan-{state['plan_seq']}"
        body = await request.json()
        state["plans"][plan_id] = {"goal": body.get("goal", ""), "runId": None}
        return JSONResponse({"planId": plan_id}, status_code=202)

    async def approve(request: Request) -> JSONResponse:
        plan_id = request.path_params["planId"]
        state["approve_calls"].append(plan_id)
        plan = state["plans"].setdefault(plan_id, {"goal": "", "runId": None})
        if plan["runId"] is None:
            state["run_seq"] += 1
            plan["runId"] = f"run-{state['run_seq']}"
        return JSONResponse({"runId": plan["runId"]})

    return Starlette(routes=[
        Route("/api/v1/orchestrator/plans", propose, methods=["POST"]),
        Route("/api/v1/orchestrator/plans/{planId}/approve", approve, methods=["POST"]),
    ]), state


def github_headers(raw: bytes, delivery: str) -> dict[str, str]:
    return {
        "X-GitHub-Event": "workflow_run",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sign(GITHUB_SCHEME, GH_SECRET, raw, ""),
        "Content-Type": "application/json",
    }


def github_payload() -> bytes:
    return json.dumps({
        "action": "completed",
        "repository": {"full_name": "acme/app"},
        "workflow_run": {
            "id": 7, "name": "CI", "conclusion": "failure", "head_branch": "main",
            "head_sha": "a" * 40, "html_url": "https://gh.example/runs/7",
            "jobs": [{"name": "unit", "conclusion": "failure"},
                     {"name": "lint", "conclusion": "success"}],
        },
    }, sort_keys=True).encode()


def slack_body(action_id: str, token: str) -> bytes:
    from urllib.parse import urlencode

    payload = json.dumps({
        "type": "block_actions", "user": {"id": "U1"},
        "actions": [{"action_id": action_id, "value": token}],
    })
    return urlencode({"payload": payload}).encode()


def slack_headers(raw: bytes) -> dict[str, str]:
    from harness_workflow_connectors.core import SignatureScheme
    from harness_workflow_connectors.slack import SLACK_SCHEME_NAME, SLACK_SIGNATURE

    scheme = SignatureScheme(name=SLACK_SCHEME_NAME, **SLACK_SIGNATURE)
    ts = str(int(NOW.timestamp()))
    return {
        "X-Slack-Signature": sign(scheme, SLACK_SIGNING, raw, ts),
        "X-Slack-Request-Timestamp": ts,
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def main() -> None:
    events = parse_stream(fixtures.events())
    costs = [parse_cost(c) for c in fixtures.costs()]
    records = build_records(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)

    orch_app, orch_state = build_orchestrator()
    orch_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=orch_app),
                                    base_url="http://orch.test", timeout=5.0)
    calls: list[dict] = []

    async def receiver(request: httpx.Request) -> httpx.Response:
        delivery = request.headers.get("x-harness-delivery", "")
        ts = request.headers.get("x-harness-timestamp", "")
        body = request.content
        canonicalized = False
        if delivery.startswith("dlv-") and delivery != "dlv-golden-1":
            # Auto deliveryIds are uuid4; canonicalize them out of the body and
            # re-sign with the fixed golden secret so captures are reproducible.
            body = body.replace(delivery.encode(), b"<auto-deliveryId>")
            canonicalized = True
        calls.append({
            "url": str(request.url),
            "deliveryId": delivery,
            "timestamp": ts,
            "bodySha256": hashlib.sha256(body).hexdigest(),
            "signature": delivery_signature("whsec_golden", body, ts),
            "signatureCanonicalized": canonicalized,
        })
        return httpx.Response(200, json={"ok": True})

    delivery_client = httpx.AsyncClient(transport=httpx.MockTransport(receiver))

    with tempfile.TemporaryDirectory() as tmp:
        store = OpsStore(str(Path(tmp) / "ops.sqlite"))
        archive = Archive(str(Path(tmp) / "archive.sqlite"))
        archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)

        store.insert_webhook(WebhookRow(
            webhookId="wh-golden-global", url="https://receiver.test/hook",
            events=["alert.opened", "alert.reminder", "alert.resolved"],
            secret="whsec_golden", teamId=None, active=True, createdBy="u-admin",
            createdAt=NOW.isoformat(),
        ))

        propose_gate = ProposePlanGate("http://orch.test", bearer_token="ops-token",
                                       client=orch_client)
        approve_gate = ApprovePlanGate("http://orch.test", bearer_token="approve-token",
                                       client=orch_client)
        ci = CIIntake(adapters=build_ci_adapters(github_secret=GH_SECRET,
                                              gitlab_secret=GL_SECRET),
                      gate=propose_gate,
                      idempotency=IdempotencyStore(str(Path(tmp) / "idem.sqlite")))
        slack = SlackActionHandler(signing_secret=SLACK_SIGNING,
                                   action_secret=ACTION_SECRET, store=store,
                                   approve_gate=approve_gate)
        app = make_ops_app(
            store=store, records_provider=lambda: records, archive=archive,
            ci_intake=ci, ci_adapters=ci._adapters, slack_handler=slack,
            schedule_gate=propose_gate, http=delivery_client, now_fn=lambda: NOW,
            get_principal_dep=principal_dep(TOKENS),
        )
        client = TestClient(app)
        admin = {"Authorization": "Bearer admin-token"}
        alice = {"Authorization": "Bearer alice-token"}
        bob = {"Authorization": "Bearer bob-token"}

        print("usage goldens:")
        write("usage_org.json", client.get("/api/v1/ops/usage", headers=admin).json())
        write("usage_team_a.json", client.get("/api/v1/ops/usage", headers=alice).json())
        write("usage_forbidden.json", client.get(
            "/api/v1/ops/usage", params={"team": "team-b"}, headers=alice).json())
        write("usage_unauthenticated.json", client.get("/api/v1/ops/usage").json())
        write("routes.json", sorted(
            ({"path": route.path, "methods": sorted(route.methods or [])}
             for route in app.routes if hasattr(route, "methods")),
            key=lambda r: r["path"],
        ))

        print("alert goldens:")
        write("alerts_evaluate_round1.json", client.post(
            "/api/v1/ops/alerts/evaluate", headers=admin).json())
        write("alerts_evaluate_round2.json", client.post(
            "/api/v1/ops/alerts/evaluate", headers=admin).json())
        write("alerts_list_org.json", client.get("/api/v1/ops/alerts",
                                                 headers=admin).json())
        write("alerts_list_member_team_a.json", client.get(
            "/api/v1/ops/alerts", headers=alice).json())
        write("alerts_list_member_team_b.json", client.get(
            "/api/v1/ops/alerts", headers=bob).json())
        write("webhook_delivery.json", {
            "receiverCalls": _normalize_delivery_ids(calls),
            "history": sorted(_normalize_delivery_ids(
                store.delivery_history("wh-golden-global")),
                key=lambda row: row["deliveryId"]),
        })

        print("evidence goldens:")
        export = client.get("/api/v1/ops/evidence/export", headers=admin)
        write_bytes("evidence_export_org.zip", export.content)
        manifest = verify_evidence_zip(export.content)
        write("evidence_verify.json", {
            "verified": True,
            "packageId": manifest["packageId"],
            "counts": manifest["counts"],
            "zipSha256": hashlib.sha256(export.content).hexdigest(),
        })
        team_export = client.get("/api/v1/ops/evidence/export", headers=alice)
        write("evidence_team_a_counts.json",
              verify_evidence_zip(team_export.content)["counts"])

        print("ci goldens:")
        raw = github_payload()
        headers = github_headers(raw, "golden-delivery-1")
        write("ci_github_proposed.json", client.post(
            "/api/v1/ops/ci/github", content=raw, headers=headers).json())
        write("ci_github_duplicate.json", client.post(
            "/api/v1/ops/ci/github", content=raw, headers=headers).json())
        bad_headers = dict(headers)
        bad_headers["X-Hub-Signature-256"] = sign(GITHUB_SCHEME, "wrong", raw, "")
        write("ci_github_signature_invalid.json", client.post(
            "/api/v1/ops/ci/github", content=raw, headers=bad_headers).json())
        gitlab_raw = json.dumps({
            "object_kind": "pipeline",
            "object_attributes": {"status": "failed", "ref": "main", "sha": "b" * 40,
                                  "id": 42, "finished_at": "2026-10-17T11:00:00Z"},
            "project": {"path_with_namespace": "acme/app",
                        "web_url": "https://gl.example/acme/app"},
        }, sort_keys=True).encode()
        write("ci_gitlab_proposed.json", client.post(
            "/api/v1/ops/ci/gitlab", content=gitlab_raw,
            headers={"X-Gitlab-Token": GL_SECRET, "X-Gitlab-Event-UUID": "golden-gl-1"},
        ).json())
        write("ci_gitlab_token_invalid.json", client.post(
            "/api/v1/ops/ci/gitlab", content=gitlab_raw,
            headers={"X-Gitlab-Token": "wrong"},
        ).json())

        print("chat goldens:")
        approve_token = sign_action_token(ACTION_SECRET, plan_id="plan-1", run_id="run-1",
                                          action="approve", now=NOW)
        raw_appr = slack_body("harness_approve", approve_token)
        hdr = {**slack_headers(raw_appr), "x-actor": "u-alice",
               "x-actor-roles": "approver"}
        write("chat_approve.json", client.post(
            "/api/v1/ops/chat/slack/actions", content=raw_appr, headers=hdr).json())
        write("chat_replay_duplicate.json", client.post(
            "/api/v1/ops/chat/slack/actions", content=raw_appr, headers=hdr).json())
        reject_token = sign_action_token(ACTION_SECRET, plan_id="plan-9", run_id="run-9",
                                         action="reject", now=NOW)
        raw_rej = slack_body("harness_reject", reject_token)
        write("chat_reject.json", client.post(
            "/api/v1/ops/chat/slack/actions", content=raw_rej,
            headers={**slack_headers(raw_rej), "x-actor": "u-alice",
                     "x-actor-roles": "approver"}).json())
        inspect_token = sign_action_token(ACTION_SECRET, plan_id="plan-1",
                                          run_id="run-1", action="inspect", now=NOW)
        raw_ins = slack_body("harness_inspect", inspect_token)
        write("chat_inspect.json", client.post(
            "/api/v1/ops/chat/slack/actions", content=raw_ins,
            headers={**slack_headers(raw_ins), "x-actor": "u-bob",
                     "x-actor-roles": "engineer"}).json())
        expired = sign_action_token(ACTION_SECRET, plan_id="plan-1", run_id="run-1",
                                    action="approve", now=NOW, ttl_s=-60)
        raw_exp = slack_body("harness_approve", expired)
        write("chat_expired.json", client.post(
            "/api/v1/ops/chat/slack/actions", content=raw_exp,
            headers={**slack_headers(raw_exp), "x-actor": "u-alice",
                     "x-actor-roles": "approver"}).json())
        write("chat_role_forbidden.json", client.post(
            "/api/v1/ops/chat/slack/actions", content=raw_appr,
            headers={**hdr, "x-actor-roles": "viewer", "x-actor": "u-vic"}).json())
        write("orchestrator_state.json", {
            "approveCalls": orch_state["approve_calls"],
            "planGoals": {k: v["goal"][:120] for k, v in
                          sorted(orch_state["plans"].items())},
        })

        print("schedule goldens:")
        created = client.post("/api/v1/ops/schedules", headers=admin, json={
            "spec": {"kind": "daily", "time": "02:30"}, "tz": "America/New_York",
            "goalTemplate": "Run nightly maintenance checks",
        })
        write("schedules_create.json", created.json())
        schedule_id = created.json()["scheduleId"]
        write("schedules_trigger_empty.json", client.post(
            f"/api/v1/ops/schedules/{schedule_id}/trigger", headers=admin).json())
        store.insert_schedule(ScheduleRow(
            scheduleId="sch-golden-old", name="Old nightly", teamId="team-a",
            spec={"kind": "daily", "time": "02:30"}, tz="America/New_York",
            goalTemplate="Run nightly maintenance checks", misfirePolicy="skip",
            enabled=True, createdBy="u-admin", createdAt=NOW.isoformat(),
            lastRunAt=(NOW - timedelta(days=1)).isoformat(),
        ))
        write("schedules_trigger_proposed.json", client.post(
            "/api/v1/ops/schedules/sch-golden-old/trigger", headers=admin).json())
        write("schedules_list.json", client.get("/api/v1/ops/schedules",
                                                headers=admin).json())

        print("DST grid golden:")
        grid = []
        cursor = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
        spec = {"kind": "daily", "time": "02:30"}
        for _ in range(4):
            nxt = next_occurrence(spec, "America/New_York", after=cursor)
            local = nxt.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
            grid.append({"utc": nxt.isoformat(), "local": local.isoformat(),
                         "tzname": local.tzname(), "fold": local.fold})
            cursor = nxt
        cursor = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
        fallback = {"kind": "daily", "time": "01:30"}
        for _ in range(4):
            nxt = next_occurrence(fallback, "America/New_York", after=cursor)
            local = nxt.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
            grid.append({"utc": nxt.isoformat(), "local": local.isoformat(),
                         "tzname": local.tzname(), "fold": local.fold})
            cursor = nxt
        write("schedule_dst_grid.json", grid)

        print("delivery goldens:")
        webhook = store.webhooks(active_only=True)[0]
        results = []
        for seq in (1, 2):
            result = await deliver_event(
                store=store, webhook=webhook, event_type="alert.opened",
                payload={"alertId": "alr-golden", "seq": seq},
                http=delivery_client, now=NOW, delivery_id="dlv-golden-1",
                timestamp=str(int(NOW.timestamp())),
            )
            results.append(result.__dict__)
        write("webhook_delivery_manual.json", {
            "results": results,
            "receiverCallCount": len(calls),
            "receiverCalls": _normalize_delivery_ids(calls),
            "history": sorted(
                _normalize_delivery_ids(
                    store.delivery_history(webhook.webhookId)),
                key=lambda row: row["deliveryId"]),
        })

    await orch_client.aclose()
    await delivery_client.aclose()
    print(f"done -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
