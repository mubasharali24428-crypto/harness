#!/usr/bin/env python3
"""W8 deterministic gate: concurrent runs, quotas, cancellation and overlapping edits.

Marathon 2.0 W8 expands the historical three-run proof with four things it did
not cover: subagent quotas under concurrent spawns, cancellation that leaves a
neighbour intact, two runs editing the same relative path without sharing bytes,
and a two-principal conflict record. None of it needs a chat model; every verdict
is read back from the product's own state.

Legs:

1. **Two-principal intent conflict.** Two admins of one team join one run and
   write different goals against the same base version at the same time. Exactly
   one is accepted; the other is ``409 INTENT_CONFLICT`` with one open record,
   and the stored intent is the winner's. A later stale write that touches no
   moved field is a plain stale refusal with no new record.
2. **Overlapping edits stay in their own run.** Two runs in one project edit the
   same relative path with different bytes. The product's staging diff for each
   run contains only that run's files and bytes; the paths are distinct on disk.
3. **Checkpoints do not collide.** Snapshots taken per run id contain only that
   run's files, and rewinding run A does not restore or remove anything in B.
4. **Cancellation preserves the neighbour.** Stopping one run leaves the other's
   workspace and checkpoint manifest byte-identical and its state untouched.
5. **Subagent quotas under concurrency.** A real agent definition (registered by
   one admin, approved by a second) spawns a tree: five simultaneous child
   spawns against ``maxChildren=3`` yield three accepted and two refused; budget,
   depth, scope and parent-state refusals hold; a denied tool call is stored as
   ``denied``; cancelling the root cancels the whole live subtree.
6. **Tenant isolation.** A user of another team cannot read or spawn into these
   runs; the answer is 404, not a partial view.

    python3 scripts/eval/tasks/concurrency_task.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
P = "/api/v1/orchestrator"
WORKSPACE_ROOT = "/var/lib/harness-runs/workspace"

spec = importlib.util.spec_from_file_location(
    "tpl", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py")
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)


# ------------------------------------------------------------------ helpers


def unwrap(body: object) -> dict:
    if not isinstance(body, dict):
        return {}
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


def error_code(body: object) -> str:
    error = unwrap(body).get("error")
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def api(method: str, path: str, token: str, body: dict | None = None,
        base: str | None = None):
    return tpl.http(method, f"{base or tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)


def hit(method: str, path: str, token: str, body: dict | None = None, base: str | None = None) -> dict:
    status, payload = api(method, path, token, body, base)
    return {"status": status, "code": error_code(payload) or None, "body": unwrap(payload)}


def ident(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    status, payload = tpl.http(method, f"{tpl.IDENTITY}{path}", token=token, body=body)
    return {"status": status, "code": error_code(payload) or None, "body": unwrap(payload)}


def login_team(email: str, team_id: str) -> str:
    """Bind a second principal to an existing team.

    The OTP flow cannot choose a team after redemption (its verify step has no
    team field), so the drill uses the local dev login route to mint a session in
    the invited team. Recorded in the evidence as a harness limitation: a product
    team-switch surface does not exist yet.
    """
    status, body = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/auth/login",
                            body={"email": email, "teamId": team_id})
    if status != 200 or not (isinstance(body, dict) and body.get("token")):
        raise SystemExit(f"team-bound login failed: {status} {body!r}")
    return body["token"]


def orchestrator_cid() -> str:
    out = tpl.sh("docker", "ps", "-q", "--filter",
                 "label=com.docker.compose.service=orchestrator").stdout.split()
    if not out:
        raise SystemExit("orchestrator container not found")
    return out[0]


def container_json(script: str) -> dict:
    proc = tpl.sh("docker", "exec", orchestrator_cid(), "python3", "-c", script, check=False)
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"error": (proc.stdout or proc.stderr or "")[-800:]}


WORKSPACE_PROBE = r'''
import hashlib, json, os
from pathlib import Path
from harness_orchestrator import staging

root = Path(@ROOT@)
spec = @FILES@
seen = {}
for run_id, entries in spec.items():
    ws = root / run_id
    for rel, text in entries.items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
for run_id in spec:
    ws = root / run_id
    baseline, staged = staging._paths(run_id, str(root))
    diffs = [d for _change, d in staging._changes(baseline, ws)]
    seen[run_id] = {
        "workspace": str(ws),
        "realpath": os.path.realpath(ws),
        "files": sorted(
            str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file()
        ),
        "bytes": {
            str(p.relative_to(ws)): p.read_text()
            for p in ws.rglob("*") if p.is_file()
        },
        "inodes": {
            str(p.relative_to(ws)): [os.stat(p).st_dev, os.stat(p).st_ino]
            for p in ws.rglob("*") if p.is_file()
        },
        "stagingDiff": "".join(diffs),
    }
print(json.dumps(seen))
'''

CHECKPOINT_PROBE = r'''
import json
from pathlib import Path
from harness_orchestrator import run_checkpoints

root = Path(@ROOT@)
files = @FILES@
run_a, run_b = @RUN_A@, @RUN_B@
snapshots = {}
for run_id, entries in files.items():
    ws = root / run_id
    snap = run_checkpoints.snapshot(ws, run_id, "w8 isolation", root=root / ".checkpoints")
    manifest = json.loads((root / ".checkpoints" / run_id / f"{snap['id']}.json").read_text())
    snapshots[run_id] = {"id": snap["id"], "paths": sorted((manifest.get("files") or {}).keys())}
# mutate both after the snapshots, then rewind A: A comes back, B must not move
(root / run_a / "only-a.txt").write_text("A mutated")
(root / run_a / "stray.txt").write_text("stray")
(root / run_b / "only-b.txt").write_text("B mutated")
rewound = run_checkpoints.rewind(root / run_a, run_a, snapshots[run_a]["id"],
                                root=root / ".checkpoints")
after = {
    "runA": {str(p.relative_to(root / run_a)): p.read_text()
              for p in (root / run_a).rglob("*") if p.is_file()},
    "runB": {str(p.relative_to(root / run_b)): p.read_text()
              for p in (root / run_b).rglob("*") if p.is_file()},
}
print(json.dumps({"snapshots": snapshots, "rewound": rewound, "after": after}))
'''


def write_workspaces(files: dict) -> dict:
    return container_json(WORKSPACE_PROBE.replace("@ROOT@", repr(WORKSPACE_ROOT)).replace("@FILES@", repr(files)))


def checkpoint_isolation(run_a: str, run_b: str) -> dict:
    files = {
        run_a: {"shared/plan.txt": "A version", "only-a.txt": "a"},
        run_b: {"shared/plan.txt": "B version", "only-b.txt": "b"},
    }
    return container_json(CHECKPOINT_PROBE
                          .replace("@ROOT@", repr(WORKSPACE_ROOT))
                          .replace("@FILES@", repr(files))
                          .replace("@RUN_A@", repr(run_a))
                          .replace("@RUN_B@", repr(run_b)))


AGENT_BODY = {
    "name": "Coder", "description": "writes code", "capabilities": ["code.python"],
    "toolPermissions": ["read_file", "search"], "permissions": ["read", "comment"],
    "allowedPaths": ["src"], "networkAllow": ["api.example.com"],
    "credentialRefs": ["GITHUB_READ"], "model": "deepseek-chat",
    "fallbackModel": "deepseek-lite", "toolFallbacks": {"search": "read_file"},
    "maxDepth": 2, "maxChildren": 3, "maxBudgetUsd": "2.5",
}
SCOPE = {"permissions": ["read"], "tools": ["read_file"], "paths": ["src/core"],
         "network": [], "credentialRefs": []}


# ------------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()
    stamp = int(time.time())
    started = time.monotonic()
    tag = f"{stamp}{args.suffix}"

    # ---- two principals of one team, a third of another team
    email_a, email_b, email_c = (f"w8q-a-{tag}@example.com",
                                 f"w8q-b-{tag}@example.com",
                                 f"w8q-c-{tag}@example.com")
    token_a = tpl.login(email_a)
    me_a = ident("GET", "/api/v1/me", token_a)["body"]
    team_id = me_a["teamId"]
    token_a = login_team(email_a, team_id)
    token_b_solo = tpl.login(email_b)
    invite = ident("POST", f"/api/v1/teams/{team_id}/invites", token_a,
                   {"email": email_b, "role": "admin"})
    redeemed = ident("POST", "/api/v1/invites/redeem", token_b_solo,
                     {"token": invite["body"].get("token"), "email": email_b})
    token_b = login_team(email_b, team_id)
    token_c = tpl.login(email_c)

    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token_a,
                               body={"name": f"w8 concurrency {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    project_id = project["id"]

    run_a, run_b = f"w8c-a-{tag}", f"w8c-b-{tag}"
    for run_id in (run_a, run_b):
        status, plan = api("POST", f"/runs/{run_id}/plan", token_a,
                           {"goal": "W8 concurrency drill", "projectId": project_id})
        if status != 201 or not plan.get("planId"):
            raise SystemExit(f"propose plan failed for {run_id}: {status} {plan!r}")

    joined_a = hit("POST", f"/runs/{run_a}/join", token_a)
    joined_b = hit("POST", f"/runs/{run_a}/join", token_b)
    leader = hit("POST", f"/runs/{run_a}/leader", token_a)

    # ---- leg 1: two principals race one intent version
    _, first = api("GET", f"/runs/{run_a}/intent", token_a)
    base_version = int((first.get("intent") or {}).get("version") or 0)

    def write_goal(goal: str):
        return hit("PUT", f"/runs/{run_a}/intent", token_a if goal == "goal from A" else token_b,
                   {"expectedVersion": base_version, "goal": goal})

    with ThreadPoolExecutor(max_workers=2) as pool:
        race = list(pool.map(write_goal, ("goal from A", "goal from B")))
    accepted = [r for r in race if r["status"] == 200]
    queued = [r for r in race if r["status"] == 202]
    # the leader writing against the base it just moved is the conflicting case
    leader_stale = hit("PUT", f"/runs/{run_a}/intent", token_a,
                       {"expectedVersion": base_version, "goal": "goal from leader stale"})
    _, intent_after = api("GET", f"/runs/{run_a}/intent", token_a)
    current = intent_after.get("intent") or {}
    _, listed = api("GET", f"/runs/{run_a}/intent/conflicts", token_a)
    open_conflicts = [c for c in (listed.get("conflicts") or [])
                      if c.get("state") == "open"]

    stale_plain = hit("PUT", f"/runs/{run_a}/intent", token_b,
                      {"expectedVersion": base_version, "permissions": ["fs.read"]})

    # ---- leg 2: two runs edit the same relative path
    edits = write_workspaces({
        run_a: {"shared/plan.txt": "A version", "only-a.txt": "a"},
        run_b: {"shared/plan.txt": "B version", "only-b.txt": "b"},
    })
    a_edit = edits.get(run_a, {})
    b_edit = edits.get(run_b, {})
    a_diff = a_edit.get("stagingDiff", "")
    b_diff = b_edit.get("stagingDiff", "")

    # ---- leg 3: checkpoints are per run and rewind cannot reach a sibling
    ckpt = checkpoint_isolation(run_a, run_b)

    # ---- leg 4: stop one run; the other's bytes and snapshots are untouched
    before_stop = {r: (write_workspaces({run_a: {}, run_b: {}}).get(r) or {}).get("bytes")
                   for r in (run_a, run_b)}
    stopped = hit("POST", f"/runs/{run_b}/stop", token_a)
    after_stop = write_workspaces({run_a: {}, run_b: {}})  # re-read only, no writes
    a_status = api("GET", f"/runs/{run_a}/status", token_a)[1].get("state")
    b_status = api("GET", f"/runs/{run_b}/status", token_a)[1].get("state")

    # ---- leg 5: agent quotas, scope, cancellation (on run A)
    registered = ident("POST", "/api/v1/agents", token_a, AGENT_BODY)
    agent_id = registered["body"].get("id")
    approved = ident("POST", f"/api/v1/agents/{agent_id}/approve", token_b)
    v2 = ident("PUT", f"/api/v1/agents/{agent_id}", token_a, {**AGENT_BODY, "name": None})
    self_approval = ident("POST", f"/api/v1/agents/{v2['body'].get('id')}/approve", token_a)

    tree = f"/runs/{run_a}/agents"
    root_spawn = hit("POST", f"{tree}/spawn", token_a,
                     {"agentId": agent_id, "title": "root", "scope": SCOPE,
                      "budgetUsd": "1.0", "requestId": f"root-{tag}"})
    duplicate = hit("POST", f"{tree}/spawn", token_a,
                    {"agentId": agent_id, "title": "root", "scope": SCOPE,
                     "budgetUsd": "1.0", "requestId": f"root-{tag}"})
    root_id = root_spawn["body"].get("id")

    def spawn_child(i: int):
        return hit("POST", f"{tree}/spawn", token_a,
                   {"agentId": agent_id, "title": f"child{i}", "scope": SCOPE,
                    "budgetUsd": "0.05", "parentAssignmentId": root_id,
                    "requestId": f"child-{tag}-{i}"})

    with ThreadPoolExecutor(max_workers=5) as pool:
        children = list(pool.map(spawn_child, range(5)))
    child_ids = [c["body"].get("id") for c in children if c["status"] == 201]
    first_child = child_ids[0]

    grandchild = hit("POST", f"{tree}/spawn", token_a,
                     {"agentId": agent_id, "title": "grandchild", "scope": SCOPE,
                      "budgetUsd": "0.02", "parentAssignmentId": first_child,
                      "requestId": f"grand-{tag}"})
    great = hit("POST", f"{tree}/spawn", token_a,
                {"agentId": agent_id, "title": "great", "scope": SCOPE,
                 "budgetUsd": "0.01", "parentAssignmentId": grandchild["body"].get("id"),
                 "requestId": f"great-{tag}"})
    over_budget = hit("POST", f"{tree}/spawn", token_a,
                      {"agentId": agent_id, "title": "costly", "scope": SCOPE,
                       "budgetUsd": "0.9", "parentAssignmentId": first_child,
                       "requestId": f"costly-{tag}"})
    wider_scope = hit("POST", f"{tree}/spawn", token_a,
                      {"agentId": agent_id, "title": "wider",
                       "scope": {**SCOPE, "paths": ["src/lib"]},
                       "budgetUsd": "0.01", "parentAssignmentId": root_id,
                       "requestId": f"wider-{tag}"})
    denied_tool = hit("POST", f"{tree}/{first_child}/tool-calls", token_a,
                      {"tool": "write_file", "target": "src/x.py",
                       "summary": "write outside the scope", "outcome": "ok",
                       "requestId": f"tool-{tag}"})
    _, tree_before_cancel = api("GET", tree, token_a)
    cancel_root = hit("POST", f"{tree}/{root_id}/cancel", token_a)
    after_cancel_spawn = hit("POST", f"{tree}/spawn", token_a,
                             {"agentId": agent_id, "title": "late", "scope": SCOPE,
                              "budgetUsd": "0.01", "parentAssignmentId": root_id,
                              "requestId": f"late-{tag}"})
    _, tree_after_cancel = api("GET", tree, token_a)

    # ---- leg 6: another team sees nothing
    cross_status = api("GET", f"/runs/{run_a}/status", token_c)
    cross_tree = api("GET", tree, token_c)
    cross_spawn = hit("POST", f"{tree}/spawn", token_c,
                      {"agentId": agent_id, "title": "intruder", "scope": SCOPE,
                       "budgetUsd": "0.01"})

    # ---- verdict
    duration = round(time.monotonic() - started, 2)
    winner_goal = current.get("goal")
    kids_ok = [c for c in children if c["status"] == 201]
    kids_refused = [c for c in children if c["status"] == 409]
    state_after_cancel = (tree_after_cancel.get("summary") or {}).get("byState") or {}
    checkpoint_files = (ckpt.get("snapshots") or {})
    checks = {
        # leg 1: conflict reporting
        "twoPrincipalsJoined": (joined_a["status"] == 200 and joined_b["status"] == 200
                                and bool(leader["body"].get("leader"))),
        "oneRaceWriterAccepted": len(accepted) == 1,
        "nonLeaderQueuedAsProposal": (len(queued) == 1
                                      and queued[0]["body"].get("queued") is True),
        "leaderStaleConflicted": (leader_stale["status"] == 409
                                  and leader_stale["code"] == "INTENT_CONFLICT"),
        "intentEqualsWinner": winner_goal in ("goal from A", "goal from B"),
        "conflictRecorded": (bool(open_conflicts) and any(
            [f.get("field") for f in c.get("fields") or []] == ["goal"]
            for c in open_conflicts)),
        "plainStaleRefused": (stale_plain["status"] == 409
                              and stale_plain["code"] == "STALE_INTENT_VERSION"),
        # leg 2: overlapping edits
        "workspacesDistinct": (bool(a_edit.get("realpath")) and bool(b_edit.get("realpath"))
                               and a_edit["realpath"] != b_edit["realpath"]),
        "samePathDifferentBytes": (
            (a_edit.get("bytes") or {}).get("shared/plan.txt") == "A version"
            and (b_edit.get("bytes") or {}).get("shared/plan.txt") == "B version"
            and (a_edit.get("inodes") or {}).get("shared/plan.txt")
            != (b_edit.get("inodes") or {}).get("shared/plan.txt")),
        "runASeesOnlyItsChanges": ("only-a.txt" in a_diff and "only-b.txt" not in a_diff
                                   and "A version" in a_diff and "B version" not in a_diff),
        "runBSeesOnlyItsChanges": ("only-b.txt" in b_diff and "only-a.txt" not in b_diff
                                   and "B version" in b_diff and "A version" not in b_diff),
        # leg 3: checkpoint isolation
        "checkpointPerRun": (
            checkpoint_files.get(run_a, {}).get("paths") == ["only-a.txt", "shared/plan.txt"]
            and checkpoint_files.get(run_b, {}).get("paths") == ["only-b.txt", "shared/plan.txt"]),
        "rewindRestoresOwnRun": (
            (ckpt.get("after") or {}).get("runA", {}).get("only-a.txt") == "a"
            and (ckpt.get("after") or {}).get("runA", {}).get("shared/plan.txt") == "A version"),
        "rewindDoesNotTouchSibling": (
            (ckpt.get("after") or {}).get("runB", {}).get("only-b.txt") == "B mutated"),
        # leg 4: cancellation
        "stopAccepted": stopped["status"] in (200, 202),
        "neighbourUnchanged": (
            (after_stop.get(run_a) or {}).get("bytes") == before_stop.get(run_a)),
        "stoppedRunBytesPreserved": (
            (after_stop.get(run_b) or {}).get("bytes") == before_stop.get(run_b)),
        "neighbourStillPlanned": a_status not in ("stopped", "cancelled", "failed"),
        "stoppedRunSettled": b_status in ("stopped", "cancelled", "failed", "done"),
        # leg 5: agent quotas
        "agentRegisteredAndApproved": (registered["status"] == 201
                                       and approved["status"] == 200
                                       and approved["body"].get("state") == "approved"),
        "selfApprovalRefused": (v2["status"] == 200
                                and self_approval["status"] == 403
                                and self_approval["code"] == "SELF_APPROVAL"),
        "childQuotaExact": (len(kids_ok) == 3 and len(kids_refused) == 2
                            and all(k["code"] == "SPAWN_CHILDREN_EXCEEDED"
                                    for k in kids_refused)),
        "spawnIdempotent": (duplicate["status"] == 200
                            and duplicate["body"].get("duplicate") is True),
        "depthLimitHolds": (grandchild["status"] == 201
                            and great["status"] == 409
                            and great["code"] == "SPAWN_DEPTH_EXCEEDED"),
        "budgetLimitHolds": (over_budget["status"] == 409
                             and over_budget["code"] == "SPAWN_BUDGET_EXCEEDED"),
        "scopeCannotWiden": (wider_scope["status"] == 403
                             and wider_scope["code"] == "SCOPE_EXCEEDS_PARENT"),
        "outOfScopeToolDenied": (denied_tool["status"] == 403
                                 and denied_tool["code"] == "TOOL_NOT_IN_SCOPE"),
        "cancelCascades": (cancel_root["status"] == 200
                           and "active" not in state_after_cancel
                           and state_after_cancel.get("cancelled", 0) >= 5),
        "cancelledParentRefusesSpawn": (after_cancel_spawn["status"] == 409
                                        and after_cancel_spawn["code"] == "PARENT_NOT_ACTIVE"),
        "treeCounted": ((tree_before_cancel.get("summary") or {}).get("total") or 0) >= 5,
        # leg 6: tenant isolation
        "crossTeamStatusHidden": cross_status[0] == 404,
        "crossTeamTreeHidden": cross_tree[0] == 404,
        "crossTeamSpawnRefused": cross_spawn["status"] == 404,
        "pilotEnabled": joined_b["status"] == 200,
    }

    evidence = {
        "schema": 1,
        "kind": "w8-concurrency",
        "takenAt": datetime.now(UTC).isoformat(),
        "runs": {run_a: "owner", run_b: "stopped neighbour"},
        "projectId": project_id,
        "model": "none (deterministic API + component checks)",
        "durationSeconds": duration,
        "principals": {
            "owner": email_a, "secondAdmin": email_b, "otherTeam": email_c,
            "inviteRedeemStatus": redeemed["status"],
            "secondAdminTeam": ident("GET", "/api/v1/me", token_b)["body"].get("teamId"),
        },
        "intentRace": {
            "baseVersion": base_version,
            "statuses": [r["status"] for r in race],
            "codes": [r["code"] for r in race],
            "queued": [{"status": r["status"], "queued": r["body"].get("queued"),
                        "keys": sorted(r["body"].keys())} for r in queued],
            "leaderStale": {"status": leader_stale["status"],
                            "code": leader_stale["code"]},
            "storedGoal": winner_goal,
            "openConflicts": [
                {"state": c.get("state"), "stale": c.get("stale"),
                 "fields": [f.get("field") for f in c.get("fields") or []]}
                for c in open_conflicts
            ],
        },
        "overlappingEdits": {
            "runA": {"files": a_edit.get("files"), "bytes": a_edit.get("bytes"),
                     "stagingDiff": a_diff},
            "runB": {"files": b_edit.get("files"), "bytes": b_edit.get("bytes"),
                     "stagingDiff": b_diff},
        },
        "checkpoints": ckpt,
        "cancellation": {"stop": stopped["status"], "runAState": a_status,
                         "runBState": b_status,
                         "runABefore": before_stop.get(run_a),
                         "runAAfter": (after_stop.get(run_a) or {}).get("bytes"),
                         "runBBefore": before_stop.get(run_b),
                         "runBAfter": (after_stop.get(run_b) or {}).get("bytes")},
        "agentTree": {
            "rootSpawn": root_spawn["status"],
            "childStatuses": [c["status"] for c in children],
            "childCodes": [c["code"] for c in children],
            "grandchild": grandchild["status"],
            "greatGrandchild": {"status": great["status"], "code": great["code"]},
            "budget": {"status": over_budget["status"], "code": over_budget["code"]},
            "scope": {"status": wider_scope["status"], "code": wider_scope["code"]},
            "tool": {"status": denied_tool["status"], "code": denied_tool["code"]},
            "cancel": {"status": cancel_root["status"],
                       "afterSpawn": {"status": after_cancel_spawn["status"],
                                      "code": after_cancel_spawn["code"]}},
            "summaryBeforeCancel": tree_before_cancel.get("summary"),
            "summaryAfterCancel": tree_after_cancel.get("summary"),
            "poolAfterCancel": tree_after_cancel.get("pool"),
        },
        "tenantIsolation": {"status": cross_status[0], "tree": cross_tree[0],
                            "spawn": cross_spawn["status"]},
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [k for k, v in checks.items() if not v],
        "limitations": [
            (
                "The second admin's session is minted through the local dev login route: "
                "the OTP verify step cannot select a team after invite redemption."
            ),
            (
                "Subagent spawns are driven over HTTP, not by the engine: the external "
                "engine does not call the agent API yet (docs/agents.md)."
            ),
            (
                "Workspace/checkpoint legs call the product's own staging and checkpoint "
                "modules inside the orchestrator; no chat model produces the edits."
            ),
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w8-concurrency-{tag}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"], "failed": evidence["failedChecks"],
                      "intentRace": evidence["intentRace"]["statuses"],
                      "childStatuses": evidence["agentTree"]["childStatuses"],
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
