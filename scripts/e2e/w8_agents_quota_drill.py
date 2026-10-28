#!/usr/bin/env python3
"""W8 gate: subagent quotas are enforced server-side, before anything spawns.

`section4/src/harness_orchestrator/agent_tree.py` gates every spawn with
compare-and-set updates on the parent assignment and the per-run pool, so
concurrent spawns cannot overshoot. Its own docstring records the honest limit:
*the external engine does not call these endpoints yet* — they record and gate,
they do not start processes. W8's roadmap row asks for "subagent quotas", and
this drill proves the gate that exists rather than implying the wiring that does
not:

  1. register and approve an agent definition (depth 1, one live child);
  2. spawn a root and one child — both allowed;
  3. a second child is refused `SPAWN_CHILDREN_EXCEEDED`;
  4. a grandchild is refused `SPAWN_DEPTH_EXCEEDED`;
  5. a child asking for a tool the parent does not hold is refused
     `SCOPE_EXCEEDS_PARENT` — a child cannot widen what its parent may do;
  6. cancelling the root cascades to the child and rolls the run pool's
     reservation back to zero.

No model is called and no worker starts: this is the API boundary, proven.

    python3 scripts/e2e/w8_agents_quota_drill.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)


def otp_login(email: str) -> tuple[str, dict]:
    status, _ = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/auth/request-login",
                         body={"email": email})
    if status != 200:
        raise SystemExit(f"request-login for {email} answered {status}")
    status, body = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/auth/verify-otp",
                            body={"email": email, "code": tpl.fetch_otp(email)})
    if status != 200 or not isinstance(body, dict) or not body.get("token"):
        raise SystemExit(f"verify-otp for {email} answered {status}: {body!r}")
    return body["token"], body["principal"]


def second_admin(email: str, team_id: str, admin_token: str) -> str:
    """A second admin of ``team_id``: invite (by an existing admin), redeem, sign in.

    Approval is deliberately not self-service (`SELF_APPROVAL`), so the drill
    needs a real second person. ``/auth/login`` names the team explicitly
    because a user in two teams cannot be resolved implicitly.
    """
    status, invite = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/teams/{team_id}/invites",
                              token=admin_token, body={"email": email, "role": "admin"})
    if status != 201 or not isinstance(invite, dict) or not invite.get("token"):
        raise SystemExit(f"invite mint answered {status}: {invite!r}")
    invitee_token, _ = otp_login(email)
    status, redeemed = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/invites/redeem",
                                token=invitee_token,
                                body={"token": invite["token"], "email": email})
    if status != 200:
        raise SystemExit(f"redeem answered {status}: {redeemed!r}")
    status, body = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/auth/login",
                            body={"email": email, "teamId": team_id})
    if status != 200 or not isinstance(body, dict) or not body.get("token"):
        raise SystemExit(f"team login answered {status}: {body!r}")
    return body["token"]


def code_of(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, dict):
        error = detail.get("error")
        if isinstance(error, dict):
            return error.get("code")
    return None


def main() -> int:
    stamp = int(time.time())
    email_a = f"w8-quota-a-{stamp}@example.com"
    email_b = f"w8-quota-b-{stamp}@example.com"
    token, principal = otp_login(email_a)
    # A definition may not approve itself (`SELF_APPROVAL`): the second person
    # is part of the gate this drill has to cross honestly.
    approver = second_admin(email_b, principal["teamId"], token)

    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w8 quota {stamp}"})
    if not (status == 201 and isinstance(project, dict) and project.get("id")):
        raise SystemExit(f"create project failed: {status} {project!r}")

    # A real run id: the plan gate creates the run the assignments hang off.
    run_id = f"w8-quota-{stamp}"
    base = f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
    status, plan = tpl.http("POST", f"{base}/plan", token=token,
                            body={"goal": "Describe the workspace.", "projectId": project["id"]})
    if not (status == 201 and isinstance(plan, dict) and plan.get("planId")):
        raise SystemExit(f"propose failed: {status} {plan!r}")
    tpl.http("POST", f"{base}/plan/approve", token=token, body={"planId": plan["planId"]})

    agent_body = {
        "name": f"w8 quota {stamp}",
        "description": "W8 subagent quota proof",
        "permissions": ["read"],
        "toolPermissions": ["bash", "deploy"],
        "allowedPaths": ["src"],
        "networkAllow": [],
        "model": os.environ.get("DSH_MODEL") or "deepseek-chat",
        "maxDepth": 1,
        "maxChildren": 1,
        "maxBudgetUsd": 1,
    }
    status, definition = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/agents", token=token,
                                  body=agent_body)
    if status != 201 or not isinstance(definition, dict) or not definition.get("id"):
        raise SystemExit(f"register agent failed: {status} {definition!r}")
    status, approved = tpl.http("POST",
                                f"{tpl.IDENTITY}/api/v1/agents/{definition['id']}/approve",
                                token=approver)
    agent_id = definition["id"]
    base_scope = {"permissions": ["read"], "tools": ["bash"], "paths": ["src"]}

    def spawn(title: str, scope: dict, parent: str | None = None,
              budget: float = 0.1) -> tuple[int, dict]:
        return tpl.http("POST", f"{base}/agents/spawn", token=token, body={
            "agentId": agent_id, "title": title, "scope": scope,
            "budgetUsd": budget,
            **({"parentAssignmentId": parent} if parent else {}),
        })

    root_status, root = spawn("root", base_scope)
    root_id = (root or {}).get("id") if isinstance(root, dict) else None
    child_status, child = spawn("child", base_scope, parent=root_id)
    child_id = (child or {}).get("id") if isinstance(child, dict) else None
    dup_status, dup = spawn("second child", base_scope, parent=root_id)
    grand_status, grand = spawn("grandchild", base_scope,
                                parent=child_id or root_id)
    wide_status, wide = spawn("wide child", {**base_scope, "tools": ["bash", "deploy"]},
                              parent=root_id)

    cancel_status, cancel_body = tpl.http(
        "POST", f"{base}/agents/{root_id}/cancel", token=token) if root_id else (None, {})
    time.sleep(1)
    _, listing = tpl.http("GET", f"{base}/agents", token=token)
    items = (listing or {}).get("items", []) if isinstance(listing, dict) else []
    states = {item.get("title"): item.get("state") for item in items}
    pool = (listing or {}).get("pool") if isinstance(listing, dict) else None

    checks = {
        "definitionApproved": status == 200 and isinstance(approved, dict)
        and (approved.get("state") == "approved"),
        "rootSpawned": root_status == 201 and isinstance(root, dict)
        and root.get("state") == "active" and root.get("depth") == 0,
        "childSpawned": child_status == 201 and isinstance(child, dict)
        and child.get("depth") == 1 and child.get("parentId") == root_id,
        "childrenQuotaEnforced": dup_status == 409
        and code_of(dup) == "SPAWN_CHILDREN_EXCEEDED",
        "depthQuotaEnforced": grand_status == 409
        and code_of(grand) == "SPAWN_DEPTH_EXCEEDED",
        "scopeInheritanceEnforced": wide_status == 403
        and code_of(wide) == "SCOPE_EXCEEDS_PARENT",
        "cancelCascades": cancel_status == 200
        and isinstance(cancel_body, dict) and cancel_body.get("changed") is True
        and states.get("root") == "cancelled" and states.get("child") == "cancelled",
        "onlyQuotaAllowingSpawnsRecorded": len(items) == 2,
        "poolReservationRolledBack": isinstance(pool, dict)
        and float(pool.get("reservedUsd") or 0) == 0.0,
    }
    evidence = {
        "schema": 1,
        "kind": "w8-agent-quotas",
        "takenAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "claim": "subagent depth, live-children and scope are enforced before any spawn",
        "drive": "orchestrator /agents/spawn and /agents/{id}/cancel on the compose stack; "
                 "no model call, no worker",
        "honestLimit": "the external engine does not call these endpoints yet "
                       "(agent_tree.py docstring): this proves the gate, not delegation",
        "runId": run_id,
        "agentDefinition": {"id": agent_id, "maxDepth": 1, "maxChildren": 1},
        "approval": {"approver": "second admin (SELF_APPROVAL refused the author)"},
        "responses": {
            "root": {"status": root_status, "id": root_id},
            "child": {"status": child_status, "id": child_id},
            "secondChild": {"status": dup_status, "code": code_of(dup)},
            "grandchild": {"status": grand_status, "code": code_of(grand)},
            "wideChild": {"status": wide_status, "code": code_of(wide)},
            "cancel": {"status": cancel_status,
                       "changed": (cancel_body or {}).get("changed")
                       if isinstance(cancel_body, dict) else None},
        },
        "assignments": states,
        "pool": pool,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [k for k, v in checks.items() if not v],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w8-agent-quotas-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"],
                      "failed": evidence["failedChecks"],
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
