#!/usr/bin/env python3
"""W28 S1b-2 live drill: one delegated child runs to completion, then /synthesis is read.

Needs HARNESS_SUBAGENTS=1 on the orchestrator. Child A is approved and run to completion on the stack
model (cost-capped); sibling B is delegated with the same paths and left unapproved, so it is live and
overlaps A, which is what `conflicts` must flag. The root is cancelled at the end and nothing may stay live.

    python3 scripts/e2e/w28_synthesis_drill.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs" / "briefs" / "evidence" / "w28" / "synthesis-drill.json"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "e2e" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tpl = load("w4_template", "w4_web_fetch_engine_drill.py")
drill = load("w28_two", "w28_two_agent_drill.py")
O, SCOPE, GOAL, psql = drill.O, drill.SCOPE, drill.GOAL, drill.psql


def main() -> int:
    stamp = int(time.time())
    parent = f"w28-syn-{stamp}"
    steps: list[dict] = []

    def step(name: str, ok: bool, **detail) -> bool:
        steps.append({"step": name, "ok": bool(ok), **detail})
        print(("PASS " if ok else "FAIL ") + name, detail or "")
        return bool(ok)

    token = tpl.login(f"w28-syn-{stamp}@example.com")
    pid = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                   body={"name": f"w28 synthesis {stamp}"})[1]["id"]
    tpl.http("POST", f"{O}/{parent}/plan", token=token, body={"goal": "parent", "projectId": pid})
    _, agent = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/agents", token=token, body={
        "name": f"w28-syn-{stamp}", "model": "deepseek/deepseek-v4-flash",
        "toolPermissions": ["read_file", "write_file"], "permissions": ["read"],
        "allowedPaths": ["src"], "maxDepth": 2, "maxChildren": 3, "maxBudgetUsd": "1"})
    team = tpl.http("GET", f"{tpl.IDENTITY}/api/v1/me", token=token)[1]["teamId"]
    e2 = f"w28-syn-approver-{stamp}@example.com"
    inv = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/teams/{team}/invites", token=token,
                   body={"email": e2, "role": "admin"})[1]
    tok2 = tpl.login(e2)
    tpl.http("POST", f"{tpl.IDENTITY}/api/v1/invites/redeem", token=tok2,
             body={"token": inv.get("token"), "email": e2})
    approver = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/auth/login",
                        body={"email": e2, "teamId": team})[1].get("token")
    st, _ = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/agents/{agent['id']}/approve", token=approver)
    if not step("agent approved", st == 200, status=st):
        return finish(steps, False)

    def delegate(rid, **body):
        return tpl.http("POST", f"{O}/{parent}/delegations", token=token, body={
            "agentId": agent["id"], "scope": SCOPE, "budgetUsd": "0.2",
            "requestId": f"w28s-{stamp}-{rid}", **body})

    root = delegate("root", title="coordinator", description="coordinates", budgetUsd="0.5")[1]["children"][0]
    kids = delegate("kids", title="worker", description=GOAL, count=2,
                    parentAssignmentId=root["id"])[1]["children"]
    a, b = kids
    step("root and two siblings delegated", len(kids) == 2)

    base = f"{O}/{a['childRunId']}"
    tpl.http("PUT", f"{base}/execution-limits", token=token, body={"maxToolCalls": 8})
    sa, _ = tpl.http("POST", f"{base}/plan/approve", token=token, body={"planId": a["childPlanId"]})
    ss, _ = tpl.http("POST", f"{base}/start", token=token)
    step("child A approved and started", sa == 200 and ss == 200, approve=sa, start=ss)

    state, deadline = None, time.time() + 240
    while time.time() < deadline:
        state = drill.status_of(token, a["childRunId"])
        if state in tpl.SETTLED:
            break
        time.sleep(3)
    step("child A ran to a settled state", state in tpl.SETTLED, state=state)

    spend = psql(f"select coalesce(sum(cost_usd),0) from cost_ledger where run_id='{a['childRunId']}'")
    _, syn = tpl.http("GET", f"{O}/{parent}/synthesis", token=token)
    by = {c["assignmentId"]: c for c in syn.get("children", [])}
    ca = by.get(a["id"], {})
    step("synthesis shows A's real result", ca.get("runStatus") == "completed" and bool(ca.get("result")),
         runStatus=ca.get("runStatus"), result=(ca.get("result") or "")[:200], spentUsd=ca.get("spentUsd"))
    flagged = [c for c in syn.get("conflicts", []) if {a["id"], b["id"]} == set(c["assignmentIds"])]
    step("synthesis spend equals the run's cost_ledger total",
         float(ca.get("spentUsd") or 0) == float(spend) > 0, synthesis=ca.get("spentUsd"), ledger=spend)
    step("synthesis flags A and B as overlapping", len(flagged) == 1,
         conflicts=syn.get("conflicts"))
    step("review required, nothing merged", syn.get("reviewRequired") is True
         and syn.get("autoMerge") is False)

    tpl.http("POST", f"{O}/{parent}/agents/{root['id']}/cancel", token=token)
    time.sleep(6)
    live = [x for x in tpl.http("GET", f"{O}/{parent}/agents", token=token)[1].get("items", [])
            if x.get("state") not in ("cancelled", "done", "failed")]
    step("nothing live after the root is cancelled", not live, live=[x["id"] for x in live])
    return finish(steps, all(s["ok"] for s in steps), childSpendUsd=spend)


def finish(steps, ok, **extra) -> int:
    EVIDENCE.write_text(json.dumps({
        "date": time.strftime("%Y-%m-%d"), "week": "W28", "slice": "S1b-2",
        "verdict": "PASS" if ok else "FAIL", "steps": steps,
        "limits": ["one trial, one small child", "conflicts judge declared scope, not files written"],
        **extra}, indent=2) + "\n")
    print("VERDICT", "PASS" if ok else "FAIL", "->", EVIDENCE)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
