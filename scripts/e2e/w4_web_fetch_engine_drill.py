#!/usr/bin/env python3
"""W4 live gate: an agent answers from live docs, and a denied fetch is recorded.

Drives a real run over HTTP (plan -> approve -> start) on the cheapest configured
model. The goal makes the agent call ``web_fetch`` twice:

1. an allow-listed provider docs page — the answer must contain the page title,
   which appears nowhere in the goal (so only a real fetch can produce it);
2. a host outside the list — the answer must contain the refusal reason.

Then it reads ``GET /runs/{id}/engine`` and asserts the audit trail contains a
``web_fetch`` tool call per attempt. One evidence file per run.

Honest scope: this proves the tool is mounted, gated, and audited at the engine
level. It does not prove a confined worker cannot reach the network another way
— that is the container/seatbelt boundary.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IDENTITY = "http://127.0.0.1:8001"
ORCHESTRATOR = "http://127.0.0.1:8004"
GOAL = (
    "Use the web_fetch tool exactly twice, and no other tool.\n"
    "1) web_fetch https://api-docs.deepseek.com/ and read the page title.\n"
    "2) web_fetch https://example.com/ and read the refusal reason.\n"
    "Then reply with exactly two lines and nothing else:\n"
    "TITLE: <the page title verbatim>\n"
    "REFUSAL: <the refusal reason verbatim>"
)
EXPECTED_TITLE = "Your First API Call"   # page title; never mentioned in the goal
EXPECTED_REFUSAL = "not in the web allow-list"
MAX_TOOL_CALLS = 6
WORKER_NAME = re.compile(r"^harness-[0-9a-f]{32}(-relay)?$")
SETTLED = {"done", "error", "failed", "execution_failed", "stopped", "cancelled", "blocked",
           "awaiting_done_signal"}


def http(
    method: str, url: str, *, token: str | None = None, body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict | str]:
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
            if not out.strip():
                continue
            c = json.loads(out)[0]
            seen[cid] = {
                "name": name,
                "role": "relay" if name.endswith("-relay") else "worker",
                "networks": sorted(c["NetworkSettings"]["Networks"]),
                "mounts": [
                    {"dst": m.get("Target"), "readOnly": bool(m.get("ReadOnly"))}
                    for m in c["HostConfig"].get("Mounts") or []
                ],
            }
        stop.wait(0.5)


def audit_tool_events(run_id: str) -> list[dict]:
    """The durable audit trail: agent_events tool_call/tool_result rows for this run.

    Reads the same table the product uses for its transcript. ``/engine`` only
    carries folded calls once the run finishes, so the audit is the authority on
    "was the tool used, and what did it answer".
    """
    sql = (
        "select type, payload from agent_events where run_id='%s' "
        "and type in ('tool_call','tool_result') order by seq" % run_id
    )
    cid = sh("docker", "ps", "-q", "--filter",
             "label=com.docker.compose.service=db").stdout.split()
    if not cid:
        return [{"type": "error", "error": "db container not found"}]
    proc = sh("docker", "exec", cid[0], "sh", "-lc",
              f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "{sql}"', check=False)
    rows = []
    for line in proc.stdout.splitlines():
        kind, _, raw = line.partition("|")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        rows.append({
            "type": kind,
            "tool": payload.get("name"),
            "callId": payload.get("callId"),
            "arguments": payload.get("arguments"),
            "ok": payload.get("ok"),
            "output": str(payload.get("output") or "")[:600],
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w4web-{stamp}"
    email = f"w4-web-{stamp}@example.com"
    steps: list[dict] = []
    seen: dict = {}
    stop = threading.Event()
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W4",
        "claim": "an agent answers from live docs; a denied destination is recorded",
        "runId": run_id,
        "drive": "HTTP only",
        "goalNamesTheTitle": False,
        "maxToolCalls": MAX_TOOL_CALLS,
    }
    t0 = time.monotonic()
    try:
        token = login(email)
        steps.append({"step": "login", "ok": True})
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w4 web {run_id}"})
        ok = status == 201 and isinstance(project, dict) and project.get("id")
        steps.append({"step": "create project", "ok": ok, "status": status})
        if not ok:
            raise SystemExit(f"create project failed: {status} {project!r}")
        project_id = project["id"]
        base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"

        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": GOAL, "projectId": project_id})
        ok = status == 201 and isinstance(plan, dict) and plan.get("planId")
        steps.append({"step": "propose plan", "ok": ok, "status": status})
        if not ok:
            raise SystemExit(f"propose plan failed: {status} {plan!r}")

        status, _ = http("PUT", f"{base}/execution-limits", token=token,
                         body={"maxToolCalls": MAX_TOOL_CALLS})
        steps.append({"step": "set tool budget", "ok": status in (200, 201), "status": status})

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
        audit = audit_tool_events(run_id)
        calls = [row for row in audit if row.get("type") == "tool_call"]
        results = [row for row in audit if row.get("type") == "tool_result"]
        calls_by_id = {row.get("callId"): row for row in calls}
        fetches = []
        for result in results:
            call = calls_by_id.get(result.get("callId")) or {}
            if call.get("tool") != "web_fetch":
                continue
            try:
                url = json.loads(call.get("arguments") or "{}").get("url", "")
            except (TypeError, ValueError):
                url = ""
            fetches.append({
                "url": url,
                "ok": result.get("ok"),
                "output": result.get("output"),
            })
        evidence.update({
            "engine": {k: engine.get(k) for k in ("engine", "status", "finalResponse")},
            "answer": answer[:800],
            "auditToolCalls": calls,
            "auditedWebFetches": fetches,
            "answerHasTitle": EXPECTED_TITLE.lower() in answer.lower(),
            "answerHasRefusal": EXPECTED_REFUSAL.lower() in answer.lower(),
            "statePath": states,
            "workersObserved": list(seen.values()),
            "elapsedSeconds": round(time.monotonic() - t0, 2),
        })
        evidence["steps"] = steps
        allowed = next((f for f in fetches if f["ok"]), None)
        denied = next((f for f in fetches if f["ok"] is False), None)
        evidence["ok"] = bool(
            all(s["ok"] for s in steps)
            and len(fetches) == 2
            and allowed and EXPECTED_TITLE.lower() in (allowed["output"] or "").lower()
            and denied and EXPECTED_REFUSAL.lower() in (denied["output"] or "").lower()
            and evidence["answerHasTitle"]
            and evidence["answerHasRefusal"]
            and any(w["role"] == "worker" for w in evidence["workersObserved"])
        )
    finally:
        Path(__file__).resolve().parents[2].joinpath(
            "docs", "briefs", "evidence", "pilot-prep", f"w4-web-fetch-engine-{stamp}.json"
        ).write_text(json.dumps(evidence, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(evidence, indent=1))
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
