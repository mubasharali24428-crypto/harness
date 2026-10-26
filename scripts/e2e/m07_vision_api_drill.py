#!/usr/bin/env python3
"""M07 leg B: a screenshot crosses the container boundary and a real model reads it.

The 2026-10-26 attempt failed: 37 bash calls over 516 s and "the model returned an
empty response" (`m07-confined-vision-failure.json`). Two things were missing then
and exist now — a durable tool budget (W2 N01) and an API path that can set it.

This drill drives everything over HTTP: upload the screenshot as a run artifact,
cap the run at a handful of tool calls, plan, approve, start, and read the answer.
The goal never names a colour, so a correct answer is proof the image itself
reached the model through the confined worker — text alone could not produce it.

Spend: one short OpenRouter call on the cheapest measured vision model
(qwen/qwen3.7-flash, $0.03/M in, $0.13/M out). Output is a single word.

    section4/.venv/bin/python scripts/e2e/m07_vision_api_drill.py
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ORCHESTRATOR_NAME = "harness-orchestrator-1"
IDENTITY = "http://127.0.0.1:8001"
ORCHESTRATOR = "http://127.0.0.1:8004"
GOAL = (
    "A screenshot image is attached to this run as an artifact. Reply with the dominant "
    "colour you see in it: one word, lowercase, nothing else. You do not need any tools — "
    "do not run commands, do not read files, do not search. Answer from the image."
)
# One correct answer could be a lucky guess, so the colour is an argument: the
# claim is sound only if a SECOND, different colour comes back right too.
EXPECTED = "orange"
COLOUR_RGB = (0xE8, 0x6A, 0x0C)  # solid orange; the goal never mentions a colour
MAX_TOOL_CALLS = 4
WORKER_NAME = re.compile(r"^harness-[0-9a-f]{32}(-relay)?$")
SETTLED = {"done", "error", "failed", "execution_failed", "stopped", "cancelled", "blocked",
           "awaiting_done_signal"}


def png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A solid-colour PNG, generated here: no image library in the drill's deps."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def http(
    method: str, url: str, *, token: str | None = None, body: dict | None = None,
    raw: bytes | None = None, content_type: str | None = None, timeout: float = 30.0,
) -> tuple[int, dict | str]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    elif data is not None:
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


def sh(*argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, check=check)


def fetch_otp(email: str) -> str:
    pattern = re.compile(rf"HARNESS-OTP login code for {re.escape(email)}: (\d+)")
    cid = sh("docker", "ps", "-q", "--filter",
             "label=com.docker.compose.service=identity").stdout.split()
    if not cid:
        raise SystemExit("identity container not found")
    deadline = time.time() + 30
    while time.time() < deadline:
        proc = sh("docker", "logs", "--tail", "400", cid[0], check=False)
        match = None
        for match in pattern.finditer(proc.stdout + proc.stderr):
            pass
        if match:
            return match.group(1)
        time.sleep(1)
    raise SystemExit(f"no OTP for {email} in identity logs")


def login(email: str) -> str:
    status, body = http("POST", f"{IDENTITY}/api/v1/auth/request-login", body={"email": email})
    assert status == 200, f"request-login {status}: {body!r}"
    status, body = http("POST", f"{IDENTITY}/api/v1/auth/verify-otp",
                        body={"email": email, "code": fetch_otp(email)})
    assert status == 200 and isinstance(body, dict) and body.get("token"), f"verify-otp {status}"
    return body["token"]


def inspect_workers(seen: dict, stop: threading.Event) -> None:
    while not stop.is_set():
        rows = sh("docker", "ps", "--format", "{{.ID}} {{.Names}}", check=False).stdout.splitlines()
        for line in rows:
            cid, _, name = line.partition(" ")
            if not WORKER_NAME.match(name) or cid in seen:
                continue
            out = sh("docker", "inspect", cid, check=False).stdout
            if out.strip():
                c = json.loads(out)[0]
                seen[cid] = {
                    "name": name, "role": "relay" if name.endswith("-relay") else "worker",
                    "user": c["Config"]["User"],
                    "readonlyRootfs": c["HostConfig"]["ReadonlyRootfs"],
                    "capDrop": c["HostConfig"]["CapDrop"],
                    "networks": sorted(c["NetworkSettings"]["Networks"]),
                    "mounts": [
                        {"src": m.get("Source"), "dst": m.get("Target"),
                         "readOnly": bool(m.get("ReadOnly")),
                         "subpath": (m.get("VolumeOptions") or {}).get("Subpath")}
                        for m in c["HostConfig"].get("Mounts") or []
                    ],
                }
        stop.wait(0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=str(
        REPO / "docs/briefs/evidence/pilot-prep/m07-vision-api.json"))
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--expected", default=EXPECTED,
                    help="colour word the answer must contain; never appears in the goal")
    ap.add_argument("--rgb", default=",".join(str(part) for part in COLOUR_RGB),
                    help="solid fill as r,g,b (0-255)")
    args = ap.parse_args()
    expected = args.expected
    rgb = tuple(int(part) for part in args.rgb.split(","))

    stamp = int(time.time())
    run_id = f"m07vis-{stamp}"
    email = f"m07-vis-{stamp}@example.com"
    steps: list[dict] = []
    seen: dict = {}
    stop = threading.Event()
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"), "milestone": "M07 screenshot through the container",
        "runId": run_id, "drive": "HTTP only", "paidSmokeOptIn": True,
        "expectedColour": expected, "goalNamesAColour": False, "solidRgb": list(rgb),
        "maxToolCalls": MAX_TOOL_CALLS,
    }
    t0 = time.monotonic()
    try:
        token = login(email)
        steps.append({"step": "login", "ok": True})

        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"m07 vision {run_id}"})
        ok = status == 201 and isinstance(project, dict) and project.get("id")
        steps.append({"step": "create project", "ok": ok, "status": status})
        if not ok:
            raise SystemExit(f"create project failed: {status} {project!r}")
        project_id = project["id"]
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"

        # The run row does not exist until a plan is proposed for it — both the
        # artifact route and the limits route answer 404 before that. So: plan
        # first, then attach the screenshot and the budget, then approve.
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": GOAL, "projectId": project_id})
        ok = status == 201 and isinstance(plan, dict) and plan.get("planId")
        steps.append({"step": "propose plan", "ok": ok, "status": status})
        if not ok:
            raise SystemExit(f"propose plan failed: {status} {plan!r}")

        # The budget exists BEFORE the first call, which is the whole N01 lesson:
        # a limit read from a table written after the run is no limit at all.
        status, _limits = http("PUT", f"{base}/execution-limits", token=token,
                               body={"maxToolCalls": MAX_TOOL_CALLS})
        steps.append({"step": "set tool budget", "ok": status in (200, 201), "status": status})
        evidence["budgetSet"] = status in (200, 201)

        png = png_bytes(64, 64, rgb)  # type: ignore[arg-type]
        status, artifact = http(
            "POST", f"{base}/artifacts?name=shot.png&kind=upload", token=token,
            raw=png, content_type="image/png",
        )
        # The upload answers {"artifact": {...}, "replayed": bool}, not the row.
        row = artifact.get("artifact") if isinstance(artifact, dict) else None
        ok = status == 201 and isinstance(row, dict) and row.get("id")
        steps.append({"step": "upload screenshot", "ok": ok, "status": status,
                      "bytes": len(png)})
        if not ok:
            raise SystemExit(f"upload failed: {status} {artifact!r}")
        evidence["artifactId"] = row["id"]
        status, _ = http("POST", f"{base}/plan/approve", token=token,
                         body={"planId": plan["planId"]})
        steps.append({"step": "approve plan", "ok": status == 200, "status": status})
        if status != 200:
            raise SystemExit(f"approve failed: {status}")

        watcher = threading.Thread(target=inspect_workers, args=(seen, stop), daemon=True)
        watcher.start()
        status, _ = http("POST", f"{base}/start", token=token)
        steps.append({"step": "start", "ok": status == 200, "status": status})
        if status != 200:
            raise SystemExit(f"start failed: {status}")

        deadline = time.time() + args.timeout
        states: list[str] = []
        state = None
        while time.time() < deadline:
            _, body = http("GET", f"{base}/status", token=token)
            state = body.get("state") if isinstance(body, dict) else None
            if state and (not states or states[-1] != state):
                states.append(state)
            if state in SETTLED:
                break
            time.sleep(2)
        stop.set()
        watcher.join(timeout=5)
        steps.append({"step": "settled", "ok": state in SETTLED, "state": state})

        _, engine = http("GET", f"{base}/engine", token=token)
        engine = engine if isinstance(engine, dict) else {}
        answer = str(engine.get("finalResponse") or "")
        evidence.update({
            "engine": {k: engine.get(k) for k in ("engine", "status", "finalResponse")},
            "engineKeys": sorted(engine),
            "usage": engine.get("usage"),  # null on this transport unless the SDK reports it
            "answer": answer[:300],
            "answerNamesTheColour": expected in answer.lower(),
            "statePath": states,
            "workersObserved": list(seen.values()),
            "elapsedSeconds": round(time.monotonic() - t0, 2),
        })
        evidence["steps"] = steps
        evidence["ok"] = bool(
            all(s["ok"] for s in steps)
            and evidence["answerNamesTheColour"]
            and any(w["role"] == "worker" for w in evidence["workersObserved"])
        )
    finally:
        Path(args.evidence).write_text(json.dumps(evidence, indent=1) + "\n")
        print(json.dumps(evidence, indent=1))

    print(f"\nevidence: {args.evidence}")
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
