"""Capture A5-S3 interop artifacts: golden fixtures for connector intake.

Full journeys against the connector service with real fake providers and the
fake plan gate; responses and the exact goal text handed to the plan gate are
normalized (ids/timestamps) and written to
docs/agent-program/a5/session-3/interop/.

Run from the repo root:
    ./packages/extensions/.venv/bin/python scripts/agent-program/a5/capture_golden_s3.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "integrations" / "connectors" / "workflows" / "tests"))

from conftest import connector_harness, sign_payload

OUT = ROOT / "docs" / "agent-program" / "a5" / "session-3" / "interop"


def normalize(obj):
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    if isinstance(obj, str):
        s = re.sub(r"plan-[0-9a-f]{6,}", "<plan-id>", obj)
        s = re.sub(r"run-[0-9a-f]{6,}", "<run-id>", s)
        s = re.sub(r"\b17\d{8,}\.\d+\b", "<slack-ts>", s)
        return s
    return obj


class Recorder:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def note(self, name: str, provider: str | None, status: int, body, gate_goals=None) -> None:
        entry: dict = {
            "name": name,
            "provider": provider,
            "response": {"status": status, "body": normalize(body)},
        }
        if gate_goals is not None:
            entry["gate"] = {
                "plans": len(gate_goals),
                "lastGoal": normalize(gate_goals[-1]) if gate_goals else None,
            }
        self.entries.append(entry)


def slack_body(text: str, event_id: str, team_id: str = "T123") -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "event_id": event_id,
            "team_id": team_id,
            "event": {"type": "message", "text": text, "ts": "1729001.000100", "channel": "C1"},
        }
    ).encode()


def sentry_body(action: str = "created", resource: str = "issue") -> bytes:
    return json.dumps(
        {
            "action": action,
            "installation": {"uuid": "org-1"},
            "data": {
                "issue": {
                    "id": "55501",
                    "title": "TypeError: NoneType has no attribute 'x'",
                    "culprit": "checkout.views.pay",
                    "count": 42,
                    "firstSeen": "2026-10-17T00:00:00Z",
                    "project": {"slug": "checkout"},
                    "web_url": "https://sentry.io/issues/55501",
                }
            },
        }
    ).encode(), {"Sentry-Hook-Resource": resource}


def alert_body(status: str, source: str = "grafana") -> bytes:
    return json.dumps(
        {
            "source": source,
            "alerts": [
                {
                    "status": status,
                    "fingerprint": "fp-abc",
                    "startsAt": "2026-10-17T10:00:00Z",
                    "labels": {"alertname": "API5xxHigh", "severity": "critical", "org_id": "1"},
                    "annotations": {
                        "summary": "5xx rate above 5% for 10m",
                        "description": "error rate 12% since deploy 42",
                    },
                    "generatorURL": "https://grafana.local/alert/1",
                }
            ],
        }
    ).encode()


def linear_body(
    action: str = "create", delivery: str = "lin-del-1"
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(
        {
            "type": "Issue",
            "action": action,
            "organizationId": "org-1",
            "data": {
                "id": "issue-uuid-1",
                "identifier": "ENG-123",
                "title": "Checkout latency spikes",
                "description": "p95 went from 200ms to 3s after deploy 42",
                "url": "https://linear.app/acme/issue/ENG-123",
            },
        }
    ).encode()
    headers = sign_payload("linear", body)
    headers["Linear-Delivery"] = delivery
    return body, headers


def jira_body() -> bytes:
    return json.dumps(
        {
            "webhookEvent": "jira:issue_created",
            "timestamp": 12345,
            "baseUrl": "https://acme.atlassian.net",
            "issue": {
                "id": "10001",
                "key": "OPS-7",
                "fields": {
                    "summary": "Nightly build red",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "compiler crashed on main"}],
                            }
                        ],
                    },
                },
            },
        }
    ).encode()


def teams_body() -> bytes:
    return json.dumps(
        {
            "type": "message",
            "id": "m1",
            "text": "please triage the flaky test",
            "channelData": {"tenant": {"id": "tenant-1"}, "messageId": "m1"},
        }
    ).encode()


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rec = Recorder()
    tmp = tempfile.TemporaryDirectory(prefix="a5-s3-capture-")

    # --- journey group A: happy paths + attacks (shared instance) -----------
    (group := pathlib.Path(tmp.name) / "a").mkdir(parents=True, exist_ok=True)
    async with connector_harness(group) as h:
        res = await h.client.get("/healthz")
        rec.note("healthz", None, res.status_code, res.json())

        ts = str(int(time.time()))
        body = slack_body("deploy is broken on prod", "Ev001")
        res = await h.post("slack", body, sign_payload("slack", body, ts))
        rec.note("slack_message_proposed", "slack", res.status_code, res.json(), h.gate.goals)

        res = await h.post("slack", body, sign_payload("slack", body, ts))
        rec.note("slack_duplicate_delivery", "slack", res.status_code, res.json(), h.gate.goals)

        tampered = sign_payload("slack", body, ts)
        tampered["X-Slack-Signature"] = tampered["X-Slack-Signature"][:-4] + "ffff"
        res = await h.post("slack", body, tampered)
        rec.note("slack_tampered_signature", "slack", res.status_code, res.json())

        stale_ts = str(int(time.time()) - 3600)
        stale_body = slack_body("stale replay", "Ev-stale")
        res = await h.post("slack", stale_body, sign_payload("slack", stale_body, stale_ts))
        rec.note("slack_stale_timestamp", "slack", res.status_code, res.json())

        evil = slack_body("hi from unknown tenant", "Ev-evil", team_id="T-EVIL")
        res = await h.post("slack", evil, sign_payload("slack", evil, ts))
        rec.note("slack_tenant_spoof_refused", "slack", res.status_code, res.json())

        secret_msg = slack_body("leak check xoxb-1234567890-abcdefghij", "Ev-secret")
        res = await h.post("slack", secret_msg, sign_payload("slack", secret_msg, ts))
        rec.note(
            "slack_secret_redacted_in_goal", "slack", res.status_code, res.json(), h.gate.goals
        )

        sb, sh_extra = sentry_body()
        sh = sign_payload("sentry", sb)
        sh.update(sh_extra)
        sh["Request-ID"] = "req-1"
        res = await h.post("sentry", sb, sh)
        rec.note("sentry_issue_created", "sentry", res.status_code, res.json(), h.gate.goals)

        sb2, sh2_extra = sentry_body(action="resolved")
        sh2 = sign_payload("sentry", sb2)
        sh2.update(sh2_extra)
        sh2["Request-ID"] = "req-2"
        res = await h.post("sentry", sb2, sh2)
        rec.note("sentry_resolved_ignored", "sentry", res.status_code, res.json(), h.gate.goals)

        ab = alert_body("firing")
        res = await h.post("alerts", ab, sign_payload("alerts", ab))
        rec.note("alert_firing_proposed", "alerts", res.status_code, res.json(), h.gate.goals)

        ab2 = alert_body("resolved")
        res = await h.post("alerts", ab2, sign_payload("alerts", ab2))
        rec.note("alert_resolved_ignored", "alerts", res.status_code, res.json(), h.gate.goals)

        lb, lh = linear_body()
        res = await h.post("linear", lb, lh)
        rec.note("linear_issue_proposed", "linear", res.status_code, res.json(), h.gate.goals)

        jb = jira_body()
        res = await h.post("jira", jb, sign_payload("jira", jb))
        rec.note("jira_issue_proposed", "jira", res.status_code, res.json(), h.gate.goals)

        tb = teams_body()
        res = await h.post("teams", tb, sign_payload("teams", tb))
        rec.note("teams_message_proposed", "teams", res.status_code, res.json(), h.gate.goals)

        res = await h.post("teams", tb, {"Authorization": "Bearer nope"})
        rec.note("teams_bad_bearer", "teams", res.status_code, res.json())

        for path in ("/plans/plan-1/approve", "/plans/plan-1/start", "/execute"):
            res = await h.client.post(path, json={})
            try:
                body_404 = res.json()
            except ValueError:
                body_404 = res.text
            rec.note(
                f"no_route_{path.strip('/').replace('/', '_')}", None, res.status_code, body_404
            )

    # --- journey group B: per-tenant rate limit (fresh counters) ------------
    (group := pathlib.Path(tmp.name) / "b").mkdir(parents=True, exist_ok=True)
    async with connector_harness(group) as h:
        ts = str(int(time.time()))
        for i in range(4):
            body = slack_body(f"flood {i}", f"Ev-rate-{i}")
            res = await h.post("slack", body, sign_payload("slack", body, ts))
            rec.note(f"slack_rate_limit_step_{i + 1}", "slack", res.status_code, res.json(),
                     h.gate.goals)

    # --- journey group C: upstream outage then provider retry ---------------
    (group := pathlib.Path(tmp.name) / "c").mkdir(parents=True, exist_ok=True)
    async with connector_harness(group) as h:
        h.gate.fail_next = 1
        ts = str(int(time.time()))
        body = slack_body("outage test", "Ev-outage")
        res = await h.post("slack", body, sign_payload("slack", body, ts))
        rec.note("plan_gate_outage_502", "slack", res.status_code, res.json(), h.gate.goals)
        res = await h.post("slack", body, sign_payload("slack", body, ts))
        rec.note("plan_gate_recovered_on_retry", "slack", res.status_code, res.json(), h.gate.goals)

    # --- interface contract: connector routes -------------------------------
    (group := pathlib.Path(tmp.name) / "d").mkdir(parents=True, exist_ok=True)
    async with connector_harness(group) as h:
        routes = []
        for route in h.app.routes:
            methods = sorted(getattr(route, "methods", []) or [])
            routes.append({"path": route.path, "methods": methods})
        (OUT / "routes.json").write_text(json.dumps(routes, indent=2, sort_keys=True) + "\n")
        print("wrote routes.json")

    golden = OUT / "golden"
    golden.mkdir(exist_ok=True)
    for i, entry in enumerate(rec.entries, start=1):
        path = golden / f"{i:02d}_{entry['name']}.json"
        path.write_text(json.dumps(entry, indent=2) + "\n")
        print("wrote", path.name, entry["response"]["status"])

    tmp.cleanup()
    print(f"{len(rec.entries)} golden fixtures + routes.json written")


if __name__ == "__main__":
    asyncio.run(main())
