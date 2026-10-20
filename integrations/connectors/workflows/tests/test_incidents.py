"""F126 Sentry + F127 Datadog/Grafana: incident -> plan, never -> execution."""

from __future__ import annotations

import json

from conftest import sign_payload


def sentry_delivery(
    action: str = "created",
    resource: str = "issue",
    request_id: str = "req-1",
    title: str = "TypeError: NoneType has no attribute 'x'",
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "action": action,
            "installation": {"uuid": "org-1"},
            "data": {
                "issue": {
                    "id": "55501",
                    "title": title,
                    "culprit": "checkout.views.pay",
                    "count": 42,
                    "firstSeen": "2026-10-17T00:00:00Z",
                    "project": {"slug": "checkout"},
                    "web_url": "https://sentry.io/issues/55501",
                }
            },
        }
    ).encode()
    headers = sign_payload("sentry", body)
    headers["Sentry-Hook-Resource"] = resource
    headers["Request-ID"] = request_id
    return body, headers


async def test_sentry_created_issue_proposes_plan(harness):
    body, headers = sentry_delivery()
    res = await harness.post("sentry", body, headers)
    assert res.status_code == 202, res.text
    goal = harness.gate.goals[0]
    assert "provider=sentry" in goal
    assert "TypeError: NoneType" in goal
    assert "checkout.views.pay" in goal
    assert "untrusted context" in goal


async def test_sentry_duplicate_request_id_single_plan(harness):
    body, headers = sentry_delivery()
    assert (await harness.post("sentry", body, headers)).status_code == 202
    assert (await harness.post("sentry", body, headers)).status_code == 200
    assert len(harness.gate.goals) == 1


async def test_sentry_signature_failures(harness):
    body, headers = sentry_delivery()
    bad = dict(headers)
    bad["Sentry-Hook-Signature"] = "f" * 64
    assert (await harness.post("sentry", body, bad)).status_code == 401
    assert (await harness.post("sentry", body, {})).status_code == 401


async def test_sentry_non_actionable_paths_ignored(harness):
    body, headers = sentry_delivery(action="resolved", request_id="req-2")
    res = await harness.post("sentry", body, headers)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "EVENT_IGNORED"

    body2, headers2 = sentry_delivery(resource="event", request_id="req-3")
    res2 = await harness.post("sentry", body2, headers2)
    assert res2.status_code == 400
    assert harness.gate.goals == []


async def test_sentry_error_text_with_secret_is_redacted(harness):
    body, headers = sentry_delivery(title="auth failed with Bearer sk_live_abcdef1234567890")
    res = await harness.post("sentry", body, headers)
    assert res.status_code == 202
    goal = harness.gate.goals[0]
    assert "sk_live_abcdef1234567890" not in goal
    assert "[REDACTED]" in goal


def alert_delivery(
    status: str = "firing",
    fingerprint: str = "fp-abc",
    starts_at: str = "2026-10-17T10:00:00Z",
    source: str = "grafana",
    org: str = "1",
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "source": source,
            "alerts": [
                {
                    "status": status,
                    "fingerprint": fingerprint,
                    "startsAt": starts_at,
                    "labels": {
                        "alertname": "API5xxHigh",
                        "severity": "critical",
                        "org_id": org,
                    },
                    "annotations": {
                        "summary": "5xx rate above 5% for 10m",
                        "description": "error rate 12% since deploy 42",
                    },
                    "generatorURL": "https://grafana.local/alert/1",
                }
            ],
        }
    ).encode()
    return body, sign_payload("alerts", body)


async def test_firing_alert_proposes_plan(harness):
    body, headers = alert_delivery()
    res = await harness.post("alerts", body, headers)
    assert res.status_code == 202, res.text
    goal = harness.gate.goals[0]
    assert "API5xxHigh" in goal and "error rate 12%" in goal
    assert "untrusted context" in goal


async def test_same_alert_episode_dedupes_fingerprint_startsat(harness):
    body, headers = alert_delivery()
    assert (await harness.post("alerts", body, headers)).status_code == 202
    assert (await harness.post("alerts", body, headers)).status_code == 200
    assert len(harness.gate.goals) == 1


async def test_resolved_only_alert_ignored(harness):
    body, headers = alert_delivery(status="resolved")
    res = await harness.post("alerts", body, headers)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "EVENT_IGNORED"
    assert harness.gate.goals == []


async def test_alert_from_datadog_source_accepted_via_same_contract(harness):
    body, headers = alert_delivery(source="datadog", fingerprint="fp-dd", org="1")
    res = await harness.post("alerts", body, headers)
    assert res.status_code == 202
    assert "source: datadog" in harness.gate.goals[0]


async def test_no_execution_route_exists_on_connector(harness):
    """The connector has no approve/start surface: 404, not 405/403 — the
    capability is absent by construction, mirroring 'no automatic execution
    before human approval'."""
    for path in (
        "/plans/plan-1/approve",
        "/plans/plan-1/start",
        "/runs/run-1/start",
        "/execute",
    ):
        res = await harness.client.post(path, json={})
        assert res.status_code == 404, path
    plan_gate_calls = harness.client  # noqa: F841 — assertion is the routing above
