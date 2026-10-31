#!/usr/bin/env python3
"""W17 Stage-2: prove the event-freshness histogram end to end on the deployed stack.

The runbook's demand (docs/runbooks/slo-and-capacity.md) is narrow and precise:
"No production end-to-end freshness histogram is currently confirmed ... add a
bounded-lag histogram before claiming attainment." The histogram exists in
section1 ``realtime_metrics``; this drill proves that it is populated by a real
commit -> live-socket-write cycle on the *deployed* identity service, not merely
present in a unit test.

Method (no provider call, no worker):

1. scrape ``/metrics`` before,
2. commit a real collaboration event (orchestrator plan create) while a project
   socket is connected, and read the frame it pushes,
3. scrape again: the live-lag histogram must have grown by at least one
   observation and that observation must be inside the 5 s bound,
4. reconnect with ``afterSeq=0`` and read the replay: the replay-age histogram
   (diagnostic, separate from the SLO) must record the catch-up frames,
5. assert both histograms are actually exported by the deployed process.

A one-off measurement, not a rate. It says the instrument works on this
deployment; it does not prove the 99% objective over a window, and it does not
stand in for the owner's SLO decision.

    python3 scripts/e2e/w17_event_freshness_drill.py
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import websockets
from w3_stop_spike import IDENTITY, ORCHESTRATOR, http, login

LIVE_COUNT = "harness_event_delivery_lag_seconds_count"
LIVE_SUM = "harness_event_delivery_lag_seconds_sum"
LIVE_LE5 = 'harness_event_delivery_lag_seconds_bucket{le="5.0"}'
REPLAY_AGE_COUNT = "harness_event_replay_age_seconds_count"
BOUND_SECONDS = 5.0


def scrape() -> dict[str, float]:
    """Parse the handful of series this drill asserts on; absent => not in dict."""
    status, body = http("GET", f"{IDENTITY}/metrics")
    assert status == 200 and isinstance(body, str), f"/metrics status {status}"
    wanted = {LIVE_COUNT, LIVE_SUM, LIVE_LE5, REPLAY_AGE_COUNT}
    out: dict[str, float] = {}
    for line in body.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        name, _, value = line.rpartition(" ")
        if name in wanted:
            try:
                out[name] = float(value)
            except ValueError:
                continue
    return out


def delta(after: dict[str, float], before: dict[str, float], key: str) -> float:
    return after.get(key, 0.0) - before.get(key, 0.0)


async def main() -> int:
    tag = uuid.uuid4().hex[:12]
    run_id = f"w17fresh-{tag}"
    evidence: dict = {
        "runId": run_id,
        "revision": None,
        "identity": IDENTITY,
        "boundSeconds": BOUND_SECONDS,
        "checks": {},
        "verdict": "FAIL",
    }
    path = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w17"
    path.mkdir(parents=True, exist_ok=True)
    try:
        token = login(f"{run_id}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": run_id})
        assert status == 201 and isinstance(project, dict), f"project status {status}"
        project_id = project["id"]
        evidence["projectId"] = project_id
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        ws_url = f"ws://127.0.0.1:8001/api/v1/ws/{project_id}"
        headers = {"Authorization": f"Bearer {token}"}

        # --- leg 1: commit -> live socket write --------------------------------
        before = scrape()
        evidence["before"] = before
        sent_wall = time.time()
        async with websockets.connect(ws_url, additional_headers=headers) as socket:
            status, plan = await asyncio.to_thread(
                http, "POST", f"{base}/plan", token=token,
                body={"goal": "Create a minimal freshness drill plan", "projectId": project_id})
            assert status == 201 and isinstance(plan, dict), f"plan status {status}"
            frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
            received_wall = time.time()
        after_live = scrape()
        evidence["afterLive"] = after_live
        live_n = delta(after_live, before, LIVE_COUNT)
        live_le5 = delta(after_live, before, LIVE_LE5)
        live_sum = delta(after_live, before, LIVE_SUM)
        evidence["live"] = {
            "frameType": frame.get("type"),
            "frameSeq": frame.get("seq"),
            "observations": live_n,
            "within5s": live_le5,
            "meanLagSeconds": round(live_sum / live_n, 4) if live_n else None,
            "wallClockToFrameSeconds": round(received_wall - sent_wall, 4),
        }
        assert LIVE_LE5 in after_live, (
            "the deployed identity /metrics does not export the live freshness histogram"
        )
        assert live_n >= 1, "no live-lag observation was recorded for a delivered frame"
        assert live_le5 == live_n, (
            f"{(live_n - live_le5):g} of {live_n:g} live observations missed the 5 s bound"
        )
        assert live_sum <= live_n * BOUND_SECONDS, "mean live lag exceeded the 5 s bound"

        # --- leg 2: catch-up replay (diagnostic histogram) ---------------------
        before_replay = scrape()
        async with websockets.connect(f"{ws_url}?afterSeq=0",
                                      additional_headers=headers) as socket:
            json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
        after_replay = scrape()
        replay_n = delta(after_replay, before_replay, REPLAY_AGE_COUNT)
        evidence["replay"] = {
            "framesRead": replay_n,
            "observations": replay_n,
            "exported": REPLAY_AGE_COUNT in after_replay,
        }
        assert REPLAY_AGE_COUNT in after_replay, (
            "the deployed identity /metrics does not export the replay-age histogram"
        )
        assert replay_n >= 1, "no replay-age observation was recorded for replayed history"

        evidence["checks"] = {
            "histogramExported": True,
            "liveDeliveryObserved": True,
            "liveDeliveryWithin5s": True,
            "replayLegObserved": True,
        }
        evidence["verdict"] = "PASS"
    except Exception as exc:  # every failure must land in evidence
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        out = path / f"w17-freshness-{tag}.json"
        out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"verdict": evidence["verdict"], "evidence": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
