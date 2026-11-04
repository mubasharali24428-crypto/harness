#!/usr/bin/env python3
"""W4 live gate: a run uses an MCP tool, and the audit trail records it.

Drives a real run over HTTP on the confined worker (the same path a web user
takes) with the reviewed first-party stub server mounted, and then reads the
durable `agent_events` rows — not the folded `/engine` view — because "the audit
records it" is a claim about the audit.

What makes the proof a proof:

  * the marker the tool returns (`HARNESS-MCP-STUB:<text>`) appears nowhere in
    the goal, so it can only be in the transcript if the call crossed a real
    server process;
  * the stub's second tool answers with credential variable NAMES only, and the
    drill asserts no credential VALUE from the deployment .env appears in any
    tool result or in the final answer — the MCP server was given no credentials
    (`docs/week-4-plan.md` decision 3, enforced in `harness_orchestrator/mcp.py`);
  * the worker container is observed on its own run network while it executes,
    so the evidence names the mode the claim was taken in.

Prerequisite: DSH_MCP_SERVERS set for the orchestrator AND the worker daemon
(Docker resolves `--env KEY` values from the daemon's own environment).

    python3 scripts/e2e/w4_mcp_client_drill.py [--timeout 420]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
MARKER_TEXT = "MCPwired"
MARKER = f"HARNESS-MCP-STUB:{MARKER_TEXT}"
GOAL = (
    "You have a tool named stub_echo from an MCP server.\n"
    "Call stub_echo exactly once with text=" + MARKER_TEXT + ".\n"
    "Then call env_probe exactly once.\n"
    "Reply with exactly two lines:\n"
    "STUB: <what stub_echo returned, verbatim>\n"
    "PROBE: <what env_probe returned, verbatim>"
)
SETTLED = {"done", "error", "failed", "execution_failed", "stopped", "cancelled", "blocked",
           "awaiting_done_signal"}


def _load_template():
    """Reuse the W4 web drill's transport/login/audit helpers: one way to drive a run."""
    spec = importlib.util.spec_from_file_location(
        "w4_web_fetch_engine_drill", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def credential_values() -> dict[str, str]:
    """Secret VALUES from the deployment .env, keyed by variable name.

    Read only to be searched for in the transcript; never written into evidence.
    """
    found: dict[str, str] = {}
    text = (REPO / ".env").read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if len(value) >= 8 and re.search(r"KEY|TOKEN|SECRET|PASSWORD", key, re.IGNORECASE):
            found[key] = value
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=420.0)
    args = ap.parse_args()
    tpl = _load_template()

    stamp = int(time.time())
    run_id = f"w4mcp-{stamp}"
    email = f"w4-mcp-{stamp}@example.com"
    secrets = credential_values()
    if not secrets:
        raise SystemExit("no credential values found in .env — the leak check would prove nothing")

    daemon_list = tpl.sh("docker", "exec", "harness-worker-daemon-1",
                        "printenv", "DSH_MCP_SERVERS", check=False).stdout.strip()
    if daemon_list != "/opt/harness/mcp/servers.stub.json":
        raise SystemExit(
            f"the worker daemon does not carry the reviewed list (got {daemon_list!r}); "
            "Docker resolves --env KEY values from ITS environment, so set DSH_MCP_SERVERS "
            "for the daemon, not only the orchestrator"
        )
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W4",
        "claim": "a run uses an MCP tool and the audit records it; the server holds no credentials",
        "runId": run_id,
        "drive": "HTTP only",
        "goalNamesTheMarker": False,
        "reviewedListOnDaemon": daemon_list,
        "credentialKeysChecked": sorted(secrets),
    }
    token = tpl.login(email)
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w4 mcp {run_id}"})
    if not (status == 201 and isinstance(project, dict) and project.get("id")):
        raise SystemExit(f"create project failed: {status} {project!r}")
    project_id = project["id"]
    base = f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"

    status, plan = tpl.http("POST", f"{base}/plan", token=token,
                            body={"goal": GOAL, "projectId": project_id})
    if not (status == 201 and isinstance(plan, dict) and plan.get("planId")):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    tpl.http("PUT", f"{base}/execution-limits", token=token, body={"maxToolCalls": 6})
    status, _ = tpl.http("POST", f"{base}/plan/approve", token=token, body={"planId": plan["planId"]})
    if status != 200:
        raise SystemExit(f"approve failed: {status}")

    seen: dict = {}
    stop = threading.Event()
    watcher = threading.Thread(target=tpl.inspect_workers, args=(seen, stop), daemon=True)
    watcher.start()
    status, _ = tpl.http("POST", f"{base}/start", token=token)
    if status != 200:
        raise SystemExit(f"start failed: {status}")

    deadline = time.time() + args.timeout
    state = None
    while time.time() < deadline:
        _, body = tpl.http("GET", f"{base}/status", token=token)
        state = body.get("state") if isinstance(body, dict) else None
        if state in SETTLED:
            break
        time.sleep(2)
    stop.set()
    watcher.join(timeout=5)

    _, engine = tpl.http("GET", f"{base}/engine", token=token)
    answer = str((engine or {}).get("finalResponse") or "") if isinstance(engine, dict) else ""
    audit = tpl.audit_tool_events(run_id)
    results = [row for row in audit if row.get("type") == "tool_result"]
    names = sorted({str(row.get("tool")) for row in audit if row.get("tool")})
    haystacks = [str(row.get("output") or "") for row in audit] + [answer]

    marker_seen = any(MARKER in text for text in haystacks)
    probe_seen = any("credentialLikeNames" in text for text in haystacks)
    leaked = {
        key: True
        for key, value in secrets.items()
        if any(value in text for text in haystacks)
    }

    workers = [w for w in seen.values() if w["role"] == "worker"]
    evidence.update({
        "settledState": state,
        "toolEventNames": names,
        "toolResultCount": len(results),
        "markerInAudit": marker_seen,
        "envProbeCalled": probe_seen,
        "answerContainsMarker": MARKER in answer,
        "credentialValuesFoundInTranscript": sorted(leaked),
        "workerContainers": [
            {"name": w["name"], "networks": w["networks"]} for w in workers
        ],
        "verdict": "PASS" if (marker_seen and not leaked and workers) else "FAIL",
    })

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w4-mcp-client-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    print(f"state={state} tool events={names}")
    print(f"marker in audit: {marker_seen} | env_probe called: {probe_seen} | "
          f"credentials leaked: {sorted(leaked) or 'none'}")
    if not workers:
        print("FAIL  no confined worker container was observed")
    if evidence["verdict"] == "PASS":
        print(f"PASS  evidence -> {path.relative_to(REPO)}")
        return 0
    print(f"FAIL  evidence -> {path.relative_to(REPO)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
