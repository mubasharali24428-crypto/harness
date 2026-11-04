#!/usr/bin/env python3
"""Compare live and replayed events while fresh plans are being published."""
import asyncio
import json
import uuid
from pathlib import Path

import websockets
from w3_stop_spike import IDENTITY, ORCHESTRATOR, http, login


async def main():
    tag = uuid.uuid4().hex[:12]
    record = {"tag": tag, "verdict": "FAIL"}
    path = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"replay-{tag}.json"
    try:
        token = login(f"w22-overlap-{tag}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w22 overlap {tag}"})
        assert status == 201
        pid = project["id"]
        record["projectId"] = pid
        headers = {"Authorization": f"Bearer {token}"}
        url = f"ws://127.0.0.1:8001/api/v1/ws/{pid}"
        async with websockets.connect(url, additional_headers=headers) as socket:
            async def produce():
                for i in range(10):
                    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/w22-{tag}-{i}"
                    status, _ = await asyncio.to_thread(http, "POST", f"{base}/plan", token=token,
                                                       body={"goal": "Observe events", "projectId": pid})
                    assert status == 201

            producer = asyncio.create_task(produce())
            live = [json.loads(await asyncio.wait_for(socket.recv(), 30)) for _ in range(10)]
            await producer
        cursor = live[4]["seq"]
        status, replay = await asyncio.to_thread(
            http, "GET", f"{IDENTITY}/api/v1/projects/{pid}/events/replay?after_seq={cursor}",
            token=token)
        assert status == 200 and not replay.get("replayTruncated")
        async with websockets.connect(f"{url}?afterSeq={cursor}", additional_headers=headers) as ws:
            recovered = [json.loads(await asyncio.wait_for(ws.recv(), 30)) for _ in range(5)]
        live_seq = [e["seq"] for e in live]
        expected = live_seq[5:]
        record.update({"live": live_seq, "cursor": cursor,
                       "rest": [e["seq"] for e in replay["items"]],
                       "resumed": [e["seq"] for e in recovered]})
        assert live_seq == sorted(set(live_seq))
        assert expected == record["rest"] == record["resumed"]
        record["verdict"] = "PASS"
    except Exception as exc:  # noqa: BLE001 — preserve failed drill, omit credential-bearing text
        record["errorType"] = type(exc).__name__
    finally:
        path.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"verdict": record["verdict"], "evidence": str(path)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
