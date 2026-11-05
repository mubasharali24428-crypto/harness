#!/usr/bin/env python3
"""W28 live drill: two delegated agents, one cancelled, then the parent; nothing is left running.

Drives the real stack over HTTP (needs HARNESS_SUBAGENTS=1 on the orchestrator):

  1. a parent run, an approved agent definition, a root delegation
  2. two children under the root (each becomes its own run awaiting plan approval)
  3. child B is cancelled before approval            -> its run is stopped
  4. child A's plan is approved and the run starts   -> a real engine run (cost-capped)
  5. the ROOT is cancelled while A runs              -> A's run and the root's run are stopped
  6. verdict from durable state, never from the agent: run states, run_claims rows, worker
     containers on the run's network, live assignments, spend recorded in cost_ledger

Honest limits are written into the evidence: the assignment scope is recorded but not yet bound to
the child engine (a later slice), and if A finishes before the cancel lands the drill says so
instead of claiming a mid-run cancel.

    python3 scripts/e2e/w28_two_agent_drill.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs" / "briefs" / "evidence" / "w28" / "two-agent-drill.json"


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "e2e" / file)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


tpl = load("w4_template", "w4_web_fetch_engine_drill.py")
w8 = load("w8_cleanup", "w8_crash_cleanup_drill.py")
O = f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs"
SCOPE = {"permissions": ["read"], "tools": ["read_file", "write_file"], "paths": ["src"],
         "network": [], "credentialRefs": []}
GOAL = ("Create the files src/a.txt, src/b.txt and src/c.txt, one tool call each, each containing "
        "its own letter, then reply with the single word: done")


def psql(sql: str) -> str:
    cid = tpl.sh("docker", "ps", "-q", "--filter",
                 "label=com.docker.compose.service=db").stdout.split()[0]
    return tpl.sh("docker", "exec", cid, "sh", "-lc",
                  f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "{sql}"').stdout.strip()


def stopped(run_id: str) -> bool:
    """A stopped run's durable trace: a `paused` event with the cancel reason (the stop encoding)."""
    return psql("select count(*) from agent_events where type='paused' and "
                f"run_id='{run_id}' and payload->>'reason'='assignment_cancelled'") not in ("", "0")


def status_of(token: str, run_id: str) -> str | None:
    _, body = tpl.http("GET", f"{O}/{run_id}/status", token=token)
    return body.get("state") if isinstance(body, dict) else None


def main() -> int:
    stamp = int(time.time())
    parent = f"w28-parent-{stamp}"
    email = f"w28-drill-{stamp}@example.com"
    steps: list[dict] = []

    def step(name: str, ok: bool, **detail) -> bool:
        steps.append({"step": name, "ok": bool(ok), **detail})
        print(("PASS " if ok else "FAIL ") + name, detail or "")
        return bool(ok)

    token = tpl.login(email)
    _, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                          body={"name": f"w28 drill {stamp}"})
    pid = project["id"]
    st, plan = tpl.http("POST", f"{O}/{parent}/plan", token=token,
                        body={"goal": "parent of the W28 drill", "projectId": pid})
    if not step("parent run exists", st == 201, status=st):
        return finish(steps, {}, False)

    definition = {"name": f"w28-agent-{stamp}", "model": "deepseek/deepseek-v4-flash",
                  "toolPermissions": ["read_file", "write_file"], "permissions": ["read"],
                  "allowedPaths": ["src"], "maxDepth": 2, "maxChildren": 3, "maxBudgetUsd": "1"}
    st, agent = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/agents", token=token, body=definition)
    agent_id = agent.get("id") if isinstance(agent, dict) else None
    # Four-eyes: a definition cannot be approved by its creator, so a second admin joins the team
    # (invite, redeem, then the local dev team-bound login, as w16_scim_deprovision_drill does).
    team_id = tpl.http("GET", f"{tpl.IDENTITY}/api/v1/me", token=token)[1]["teamId"]
    email2 = f"w28-approver-{stamp}@example.com"
    _, invite = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/teams/{team_id}/invites", token=token,
                         body={"email": email2, "role": "admin"})
    tok2 = tpl.login(email2)
    tpl.http("POST", f"{tpl.IDENTITY}/api/v1/invites/redeem", token=tok2,
             body={"token": invite.get("token"), "email": email2})
    _, second = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/auth/login",
                         body={"email": email2, "teamId": team_id})
    approver = second.get("token") if isinstance(second, dict) else None
    st2, _ = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/agents/{agent_id}/approve", token=approver)
    if not step("agent definition approved", st == 201 and st2 == 200, create=st, approve=st2):
        return finish(steps, {}, False)

    def delegate(rid: str, **body):
        return tpl.http("POST", f"{O}/{parent}/delegations", token=token, body={
            "agentId": agent_id, "scope": SCOPE, "budgetUsd": "0.2",
            "requestId": f"w28-{stamp}-{rid}", **body})

    st, out = delegate("root", title="coordinator", description="coordinates two workers",
                        budgetUsd="0.5")
    root = out["children"][0] if st == 201 else {}
    st, out = delegate("kids", title="worker", description=GOAL, count=2,
                       parentAssignmentId=root.get("id"))
    kids = out["children"] if st == 201 else []
    runs = {"root": root.get("childRunId"), "A": kids[0].get("childRunId") if kids else None,
            "B": kids[1].get("childRunId") if len(kids) > 1 else None}
    if not step("root and two children delegated, each with its own run",
                len(set(filter(None, runs.values()))) == 3, runs=runs, status=st,
                response=out if st != 201 else None):
        return finish(steps, runs, False)
    step("children wait for human approval", all(
        status_of(token, runs[k]) == "awaiting_plan_approval" for k in ("A", "B")))

    st, _ = tpl.http("POST", f"{O}/{parent}/agents/{kids[1]['id']}/cancel", token=token)
    time.sleep(1)
    step("child B cancelled before approval, its run stopped",
         st == 200 and stopped(runs["B"]), runStatus=status_of(token, runs["B"]))

    base = f"{O}/{runs['A']}"
    pid_a = kids[0].get("childPlanId")
    tpl.http("PUT", f"{base}/execution-limits", token=token, body={"maxToolCalls": 8})
    sa, _ = tpl.http("POST", f"{base}/plan/approve", token=token, body={"planId": pid_a})
    ss, _ = tpl.http("POST", f"{base}/start", token=token)
    step("child A approved by a human and started", sa == 200 and ss == 200, approve=sa, start=ss)

    deadline, state = time.time() + 60, None
    while time.time() < deadline:
        state = status_of(token, runs["A"])
        if state in ("executing", *tpl.SETTLED):
            break
        time.sleep(1)
    # "executing" can precede the worker by seconds. Cancel only once the engine has a network or has
    # spent money, so the cancel is genuinely mid-run; otherwise say it was not.
    net_before, deadline = None, time.time() + 90
    while time.time() < deadline and state == "executing":
        net_before = w8.network_of(runs["A"])
        if net_before or psql(f"select count(*) from cost_ledger where run_id='{runs['A']}'") != "0":
            break
        time.sleep(1)
        state = status_of(token, runs["A"])
    mid_run = state == "executing" and bool(net_before or psql(
        f"select count(*) from cost_ledger where run_id='{runs['A']}'") != "0")
    step("child A observed", state is not None, state=state, midRun=mid_run,
         networkBeforeCancel=net_before)

    st, _ = tpl.http("POST", f"{O}/{parent}/agents/{root['id']}/cancel", token=token)
    step("root cancelled", st == 200, status=st)
    time.sleep(8)
    final = {k: status_of(token, runs[k]) for k in runs}
    step("every delegated run was stopped by the cascade",
         all(stopped(runs[k]) or final[k] in tpl.SETTLED for k in runs), final=final,
         stopEvents={k: stopped(runs[k]) for k in runs})
    claims = {k: w8.claim_row(runs[k]) for k in runs}
    step("no run_claims row left", all(v is None for v in claims.values()), claims=claims)
    net_after = w8.network_of(runs["A"])
    leftovers = w8.containers_on(net_after) if net_after else []
    step("no worker container or network left for A", not leftovers, network=net_after,
         containers=leftovers)
    _, listing = tpl.http("GET", f"{O}/{parent}/agents", token=token)
    live = [a for a in (listing or {}).get("items", [])
            if a.get("state") not in ("cancelled", "done", "failed")]
    step("no live assignment left", not live, live=[a.get("id") for a in live])
    st, syn = tpl.http("GET", f"{O}/{parent}/synthesis", token=token)
    seen = {c["childRunId"]: c for c in (syn or {}).get("children", [])} if st == 200 else {}
    step("synthesis lists all three delegated runs for review, merging nothing",
         st == 200 and set(seen) == set(runs.values()) and syn["reviewRequired"] is True
         and syn["autoMerge"] is False, status=st,
         states={k: seen.get(v, {}).get("state") for k, v in runs.items()},
         runStatus={k: seen.get(v, {}).get("runStatus") for k, v in runs.items()},
         conflicts=(syn or {}).get("conflicts"))
    return finish(steps, runs, all(s["ok"] for s in steps), midRunCancel=mid_run)


def finish(steps: list[dict], runs: dict, ok: bool, **extra) -> int:
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps({
        "date": time.strftime("%Y-%m-%d"), "week": "W28", "verdict": "PASS" if ok else "FAIL",
        "runs": runs, "steps": steps, "drive": "HTTP + docker/psql reads",
        "limits": ["assignment scope is recorded but not yet bound to the child engine",
                   "one trial; the cancel is mid-run only where midRunCancel is true"],
        **extra}, indent=2) + "\n")
    print("VERDICT", "PASS" if ok else "FAIL", "->", EVIDENCE)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
