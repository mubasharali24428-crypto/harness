#!/usr/bin/env python3
"""Exercise project WS disconnect and replay using a fresh, non-executing run.

No worker is started and no provider is called. The script deliberately never
logs credentials. It creates an isolated project on the selected deployment;
it does not change shared service configuration or remove other runs.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import websockets
from w3_stop_spike import IDENTITY, ORCHESTRATOR, http, login


async def main() -> int:
    tag = uuid.uuid4().hex[:12]
    run_id = f"w22-replay-{tag}"
    evidence = {"runId": run_id, "checks": {}, "spend": 0, "verdict": "FAIL"}
    path = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22"
    path.mkdir(parents=True, exist_ok=True)
    try:
        token = login(f"{run_id}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": run_id})
        assert status == 201 and isinstance(project, dict), f"project status {status}"
        project_id = project["id"]
        evidence["projectId"] = project_id
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        url = f"ws://127.0.0.1:8001/api/v1/ws/{project_id}"
        headers = {"Authorization": f"Bearer {token}"}
        async with websockets.connect(url, additional_headers=headers) as socket:
            status, plan = await asyncio.to_thread(
                http, "POST", f"{base}/plan", token=token,
                body={"goal": "Create a minimal recovery drill plan", "projectId": project_id})
            assert status == 201 and isinstance(plan, dict), f"plan status {status}"
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
            cursor = event["seq"]
            evidence["beforeDisconnect"] = {"seq": cursor, "type": event["type"]}
        status, _ = await asyncio.to_thread(
            http, "POST", f"{base}/plan/approve", token=token, body={"planId": plan["planId"]})
        assert status == 200, f"approve status {status}"
        status, replay = await asyncio.to_thread(
            http, "GET", f"{IDENTITY}/api/v1/projects/{project_id}/events/replay?after_seq={cursor}",
            token=token)
        assert status == 200 and isinstance(replay, dict), f"replay status {status}"
        expected = replay.get("items", [])
        assert expected, "no events persisted while disconnected"
        assert not replay.get("replayTruncated"), "unexpected truncated replay"
        async with websockets.connect(f"{url}?afterSeq={cursor}", additional_headers=headers) as socket:
            actual = [json.loads(await asyncio.wait_for(socket.recv(), timeout=20)) for _ in expected]
        expected_pairs = [(e["seq"], e["type"]) for e in expected]
        actual_pairs = [(e["seq"], e["type"]) for e in actual]
        evidence["persistedAfterCursor"] = expected_pairs
        evidence["replayedAfterCursor"] = actual_pairs
        evidence["checks"] = {
            "eventObservedBeforeDisconnect": cursor > 0,
            "eventsPersistedWhileDisconnected": bool(expected_pairs),
            "resumeMatchesRestReplay": actual_pairs == expected_pairs,
            "orderedUniqueAfterCursor": [s for s, _ in actual_pairs] == sorted({s for s, _ in actual_pairs})
            and all(s > cursor for s, _ in actual_pairs),
        }
        evidence["verdict"] = "PASS" if all(evidence["checks"].values()) else "FAIL"
    except Exception as exc:  # noqa: BLE001 — record failed drills without leaking credentials
        # Avoid printing exception messages that could include request headers or URLs.
        evidence["errorType"] = type(exc).__name__
    finally:
        evidence["recordedAtUnix"] = int(time.time())
        output = path / f"{run_id}.json"
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))
        print(f"evidence: {output}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
