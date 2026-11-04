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

Targets the shared stack by default; point it at the isolated ``loadtest``
profile when the shared stack belongs to another builder:

    python3 scripts/e2e/w17_event_freshness_drill.py
    python3 scripts/e2e/w17_event_freshness_drill.py \\
        --identity http://127.0.0.1:18001 --orchestrator http://127.0.0.1:18004 \\
        --identity-service identity-loadtest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import websockets

BOUND_SECONDS = 5.0


def http(method: str, url: str, *, token: str | None = None, body: dict | None = None,
         timeout: float = 30.0) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content, status = resp.read().decode("utf-8", "replace"), resp.status
    except urllib.error.HTTPError as e:
        content, status = e.read().decode("utf-8", "replace"), e.code
    try:
        return status, json.loads(content) if content else {}
    except ValueError:
        return status, content


def fetch_otp(email: str, identity_service: str) -> str:
    """Read the minted OTP from the container's own logs (never printed, never stored)."""
    pattern = re.compile(rf"HARNESS-OTP login code for {re.escape(email)}: (\d+)")
    out = subprocess.run(
        ["docker", "ps", "-q", "--filter",
         f"label=com.docker.compose.service={identity_service}"],
        capture_output=True, text=True, check=False).stdout.split()
    if not out:
        raise SystemExit(f"no container for compose service {identity_service!r}")
    deadline = time.time() + 30
    while time.time() < deadline:
        proc = subprocess.run(["docker", "logs", "--tail", "400", out[0]],
                              capture_output=True, text=True, check=False)
        match = None
        for match in pattern.finditer(proc.stdout + proc.stderr):
            pass
        if match:
            return match.group(1)
        time.sleep(1)
    raise SystemExit(f"no OTP for {email}")


def login(email: str, identity: str, identity_service: str) -> str:
    status, _ = http("POST", f"{identity}/api/v1/auth/request-login", body={"email": email})
    assert status == 200, f"request-login {status}"
    status, body = http("POST", f"{identity}/api/v1/auth/verify-otp",
                        body={"email": email, "code": fetch_otp(email, identity_service)})
    assert status == 200 and body.get("token"), f"verify-otp {status}"
    return body["token"]


LIVE_COUNT = "harness_event_delivery_lag_seconds_count"
LIVE_SUM = "harness_event_delivery_lag_seconds_sum"
LIVE_LE5 = 'harness_event_delivery_lag_seconds_bucket{le="5.0"}'
REPLAY_AGE_COUNT = "harness_event_replay_age_seconds_count"


def scrape(identity: str) -> dict[str, float]:
    """Parse the handful of series this drill asserts on; absent => not in dict."""
    status, body = http("GET", f"{identity}/metrics")
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", default="http://127.0.0.1:8001")
    ap.add_argument("--orchestrator", default="http://127.0.0.1:8004")
    ap.add_argument("--identity-service", default="identity",
                    help="compose service label holding the OTP log line")
    args = ap.parse_args()
    identity, orchestrator = args.identity, args.orchestrator
    ws_base = "ws" + identity.removeprefix("http")
    tag = uuid.uuid4().hex[:12]
    run_id = f"w17fresh-{tag}"
    evidence: dict = {
        "runId": run_id,
        "identity": identity,
        "orchestrator": orchestrator,
        "boundSeconds": BOUND_SECONDS,
        "checks": {},
        "verdict": "FAIL",
    }
    path = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w17"
    path.mkdir(parents=True, exist_ok=True)
    try:
        token = login(f"{run_id}@example.com", identity, args.identity_service)
        status, project = http("POST", f"{identity}/api/v1/projects", token=token,
                               body={"name": run_id})
        assert status == 201 and isinstance(project, dict), f"project status {status}"
        project_id = project["id"]
        evidence["projectId"] = project_id
        base = f"{orchestrator}/api/v1/orchestrator/runs/{run_id}"
        ws_url = f"{ws_base}/api/v1/ws/{project_id}"
        headers = {"Authorization": f"Bearer {token}"}

        # --- leg 1: commit -> live socket write --------------------------------
        before = scrape(identity)
        evidence["before"] = before
        sent_wall = time.time()
        async with websockets.connect(ws_url, additional_headers=headers) as socket:
            status, plan = await asyncio.to_thread(
                http, "POST", f"{base}/plan", token=token,
                body={"goal": "Create a minimal freshness drill plan", "projectId": project_id})
            assert status == 201 and isinstance(plan, dict), f"plan status {status}"
            frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
            received_wall = time.time()
        after_live = scrape(identity)
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
        before_replay = scrape(identity)
        async with websockets.connect(f"{ws_url}?afterSeq=0",
                                      additional_headers=headers) as socket:
            json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
        after_replay = scrape(identity)
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
