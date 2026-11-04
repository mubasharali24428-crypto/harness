#!/usr/bin/env python3
"""W3 deterministic gate: a stale edit is refused and the content is unchanged.

Covers the safe-edit half of the W3 gate without a model in the loop. Every leg
is an independent check of the server's own state after the call, not of a
response message:

1. **Run intent is versioned (CAS).** A write computed against an older intent
   version is refused. When the fields it changes also moved, the refusal is
   ``409 INTENT_CONFLICT`` and an open conflict record is created; when they did
   not move, it is ``409 STALE_INTENT_VERSION`` and no record is created. An
   ``expectedVersion`` ahead of the intent is ``422 BAD_VERSION``. The intent
   itself is byte-identical to what the last accepted write produced.
2. **Steering cannot expand the approved scope.** A ``scope_change`` redirect is
   an event, not an intent write: the version, scope paths and permissions after
   it are exactly what they were before it.
3. **The plan is versioned too.** A plan version write against a superseded
   version is ``409 VERSION_CONFLICT`` and the newest content is unchanged; a
   patch proposed against a superseded version is ``409 VERSION_NOT_OPEN``.
4. **Checkpoint/rewind is exact.** Inside the orchestrator container (same user,
   same code) a snapshot is taken, the tree is mutated three ways, and a rewind
   restores the snapshot byte-for-byte and removes the file created after it.
   The HTTP route reports an empty list and refuses a rewind on a run that has
   no snapshots instead of pretending to restore something.

    python3 scripts/eval/tasks/stale_edit_task.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
P = "/api/v1/orchestrator"

spec = importlib.util.spec_from_file_location(
    "tpl", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py")
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

CHECKPOINT_PROBE = r'''
import json, os, tempfile
from pathlib import Path
from harness_orchestrator import run_checkpoints

base = tempfile.mkdtemp(prefix="w3-rewind-")
root = Path(base) / ".checkpoints"
ws = Path(base) / "workspace"
os.makedirs(ws / "sub")
(ws / "a.txt").write_text("v1")
(ws / "sub" / "b.txt").write_text("keep")
snap = run_checkpoints.snapshot(ws, "w3-run", "before attempt", root=root)
(ws / "a.txt").write_text("v2")
(ws / "sub" / "b.txt").unlink()
(ws / "c.txt").write_text("stray")
rewound = run_checkpoints.rewind(ws, "w3-run", snap["id"], root=root)
print(json.dumps({
    "checkpoint": snap["id"],
    "restoredFiles": rewound["restoredFiles"],
    "removedFiles": rewound["removedFiles"],
    "a": (ws / "a.txt").read_text(),
    "b": (ws / "sub" / "b.txt").read_text(),
    "strayExists": (ws / "c.txt").exists(),
}))
'''


# ------------------------------------------------------------------ API helpers


def unwrap(body: object) -> dict:
    """FastAPI renders ``HTTPException.detail`` under a ``detail`` key; tool code
    sometimes sees the envelope directly. Accept both."""
    if not isinstance(body, dict):
        return {}
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


def error_code(body: object) -> str:
    error = unwrap(body).get("error")
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def api(method: str, path: str, token: str, body: dict | None = None):
    return tpl.http(method, f"{tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)


def orchestrator_cid() -> str:
    out = tpl.sh("docker", "ps", "-q", "--filter",
                 "label=com.docker.compose.service=orchestrator").stdout.split()
    if not out:
        raise SystemExit("orchestrator container not found")
    return out[0]


def checkpoint_probe() -> dict:
    proc = tpl.sh("docker", "exec", orchestrator_cid(), "python3", "-c",
                  CHECKPOINT_PROBE, check=False)
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return {"error": (proc.stdout or proc.stderr or "")[-600:]}


def recorded(outcomes: dict, name: str, status: int, body: object) -> dict:
    """Keep what the route answered, and return the unwrapped body for checks."""
    payload = unwrap(body)
    outcomes[name] = {
        "status": status,
        "code": error_code(body) or None,
        **{k: payload[k] for k in
           ("version", "conflictId", "conflictFields", "currentVersion", "changed",
            "unchanged", "state", "resolution", "queued") if k in payload},
    }
    return payload


# ------------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="", help="run-id suffix (unused by default)")
    args = ap.parse_args()
    stamp = int(time.time())
    run_id = f"w3-stale-{stamp}{args.suffix}"
    outcomes: dict = {}
    started = time.monotonic()

    token = tpl.login(f"w3-stale-{stamp}@example.com")
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w3 stale {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    project_id = project["id"]

    # ---- a run with an open plan and a leader (the control surface needs both)
    status, plan = api("POST", f"/runs/{run_id}/plan", token,
                       {"goal": "W3 stale-edit drill", "projectId": project_id})
    if status != 201 or not plan.get("planId"):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    status, joined = api("POST", f"/runs/{run_id}/join", token)
    pilot_enabled = error_code(joined) != "PILOT_NOT_ENABLED"
    if status != 200:
        raise SystemExit(f"join failed: {status} {joined!r}")
    status, leadership = api("POST", f"/runs/{run_id}/leader", token)
    if status != 200 or not leadership.get("leader"):
        raise SystemExit(f"claim leader failed: {status} {leadership!r}")
    leader_id = leadership["leader"]

    # ---- intent CAS
    _, first = api("GET", f"/runs/{run_id}/intent", token)
    base_version = int((first.get("intent") or {}).get("version") or 0)

    status, body = api("PUT", f"/runs/{run_id}/intent", token,
                       {"expectedVersion": base_version, "goal": "intent one",
                        "scopePaths": ["src"]})
    recorded(outcomes, "intentAccepted", status, body)

    status, body = api("PUT", f"/runs/{run_id}/intent", token,
                       {"expectedVersion": base_version, "goal": "intent stale"})
    conflict = recorded(outcomes, "staleClash", status, body)
    conflict_id = str(conflict.get("conflictId") or "")

    # permissions were not touched by the accepted write, so this stale edit has
    # no clashing field and must be a plain stale-version refusal (no record).
    status, body = api("PUT", f"/runs/{run_id}/intent", token,
                       {"expectedVersion": base_version, "permissions": ["fs.read"]})
    recorded(outcomes, "staleNoClash", status, body)

    status, body = api("PUT", f"/runs/{run_id}/intent", token,
                       {"expectedVersion": base_version + 8, "goal": "intent ahead"})
    recorded(outcomes, "ahead", status, body)

    status, body = api("PUT", f"/runs/{run_id}/intent", token,
                       {"expectedVersion": base_version + 1, "goal": "intent one"})
    recorded(outcomes, "unchanged", status, body)

    status, body = api("PUT", f"/runs/{run_id}/intent", token,
                       {"expectedVersion": base_version + 1, "scopePaths": ["../etc"]})
    recorded(outcomes, "traversal", status, body)

    _, after_stale = api("GET", f"/runs/{run_id}/intent", token)
    intent = after_stale.get("intent") or {}

    _, listed = api("GET", f"/runs/{run_id}/intent/conflicts", token)
    conflicts = listed.get("conflicts") or []
    open_conflicts = [c for c in conflicts if c.get("state") == "open"]

    # ---- steering is an event, not a scope write
    status, body = api("POST", f"/runs/{run_id}/redirect", token,
                       {"kind": "scope_change", "reason": "w3 drill",
                        "content": "expand the scope to every path and permission"})
    recorded(outcomes, "redirect", status, body)
    _, after_redirect = api("GET", f"/runs/{run_id}/intent", token)
    steered = after_redirect.get("intent") or {}

    # ---- conflict resolution: keep_current changes nothing
    status, body = api("POST", f"/runs/{run_id}/intent/conflicts/{conflict_id}/resolve",
                       token, {"resolution": "keep_current",
                               "expectedVersion": after_redirect.get("intent", {}).get("version")})
    recorded(outcomes, "resolveKeep", status, body)
    _, after_keep = api("GET", f"/runs/{run_id}/intent", token)

    # ---- a second conflict, taken as proposed, does move the version
    status, body = api("PUT", f"/runs/{run_id}/intent", token,
                       {"expectedVersion": base_version, "goal": "intent proposed"})
    second = recorded(outcomes, "staleClashSecond", status, body)
    current_version = int((after_keep.get("intent") or {}).get("version") or 0)
    status, body = api(
        "POST", f"/runs/{run_id}/intent/conflicts/{second.get('conflictId')}/resolve",
        token, {"resolution": "take_proposed", "expectedVersion": current_version})
    recorded(outcomes, "resolveTake", status, body)
    _, after_take = api("GET", f"/runs/{run_id}/intent", token)

    # ---- plan versions
    _, version_one = api("GET", f"/runs/{run_id}/plan/versions/1", token)
    content_two = dict(version_one.get("content") or {})
    content_two["goal"] = "plan two"
    status, body = api("POST", f"/runs/{run_id}/plan/versions", token,
                       {"content": content_two, "expectedVersion": 1})
    recorded(outcomes, "planV2", status, body)

    stale_content = dict(content_two)
    stale_content["goal"] = "plan stale"
    status, body = api("POST", f"/runs/{run_id}/plan/versions", token,
                       {"content": stale_content, "expectedVersion": 1})
    recorded(outcomes, "planStale", status, body)

    status, body = api("POST", f"/runs/{run_id}/plan/versions/1/edits", token,
                       {"patch": [{"op": "replace", "path": "/goal", "value": "patch"}]})
    recorded(outcomes, "stalePatchProposal", status, body)

    _, latest_versions = api("GET", f"/runs/{run_id}/plan/versions", token)
    latest_version = int(latest_versions.get("latestVersion") or 0)
    _, latest_full = api("GET", f"/runs/{run_id}/plan/versions/{latest_version}", token)
    latest_content = latest_full.get("content") or {}

    # ---- checkpoints: component proof inside the orchestrator + route contract
    rewind_probe = checkpoint_probe()
    status, checkpoints = api("GET", f"/runs/{run_id}/checkpoints", token)
    recorded(outcomes, "checkpointsList", status, checkpoints)
    status, body = api("POST", f"/runs/{run_id}/rewind", token, {"checkpoint": None})
    recorded(outcomes, "rewindEmpty", status, body)

    duration = round(time.monotonic() - started, 2)
    conflict_fields = conflict.get("conflictFields") or []
    accepted_version = base_version + 1
    checks = {
        "intentAcceptedAsV1": (outcomes["intentAccepted"]["status"] == 200
                               and outcomes["intentAccepted"].get("version")
                               == accepted_version),
        "staleConflictRefused": (outcomes["staleClash"]["status"] == 409
                                 and outcomes["staleClash"]["code"] == "INTENT_CONFLICT"
                                 and bool(conflict_id)
                                 and conflict_fields == ["goal"]),
        "staleNoClashRefused": (outcomes["staleNoClash"]["status"] == 409
                                and outcomes["staleNoClash"]["code"]
                                == "STALE_INTENT_VERSION"),
        "aheadRefused": (outcomes["ahead"]["status"] == 422
                         and outcomes["ahead"]["code"] == "BAD_VERSION"),
        "unchangedIsNoop": (outcomes["unchanged"]["status"] == 200
                            and outcomes["unchanged"].get("unchanged") is True
                            and outcomes["unchanged"].get("version") == accepted_version),
        "traversalRefused": (outcomes["traversal"]["status"] == 422
                             and outcomes["traversal"]["code"] == "BAD_PATHS"),
        "intentUnchangedAfterStale": (int(intent.get("version") or -1) == accepted_version
                                      and intent.get("goal") == "intent one"
                                      and intent.get("scopePaths") == ["src"]
                                      and intent.get("permissions") == []),
        "conflictRecorded": (len(open_conflicts) == 1
                             and open_conflicts[0].get("stale") is True
                             and any(f.get("field") == "goal"
                                     and f.get("conflicting") is True
                                     for f in open_conflicts[0].get("fields") or [])),
        "steeringCannotExpandScope": (outcomes["redirect"]["status"] == 200
                                      and int(steered.get("version") or -1)
                                      == accepted_version
                                      and steered.get("goal") == "intent one"
                                      and steered.get("scopePaths") == ["src"]
                                      and steered.get("permissions") == []),
        "keepCurrentChangesNothing": (outcomes["resolveKeep"]["status"] == 200
                                      and outcomes["resolveKeep"].get("version")
                                      == accepted_version
                                      and (after_keep.get("intent") or {}).get("goal")
                                      == "intent one"),
        "takeProposedApplies": (outcomes["resolveTake"]["status"] == 200
                                and outcomes["resolveTake"].get("version")
                                == accepted_version + 1
                                and (after_take.get("intent") or {}).get("goal")
                                == "intent proposed"),
        "planStaleWriteRefused": (outcomes["planStale"]["status"] == 409
                                  and outcomes["planStale"]["code"] == "VERSION_CONFLICT"
                                  and outcomes["planStale"].get("currentVersion") == 2),
        "planContentUnchanged": (latest_version == 2
                                 and latest_content.get("goal") == "plan two"),
        "stalePatchProposalRefused": (outcomes["stalePatchProposal"]["status"] == 409
                                      and outcomes["stalePatchProposal"]["code"]
                                      == "VERSION_NOT_OPEN"),
        "checkpointRewindExact": (rewind_probe.get("a") == "v1"
                                  and rewind_probe.get("b") == "keep"
                                  and rewind_probe.get("strayExists") is False
                                  and rewind_probe.get("restoredFiles") == 2
                                  and rewind_probe.get("removedFiles") == ["c.txt"]),
        "checkpointsEmptyList": (outcomes["checkpointsList"]["status"] == 200
                                 and checkpoints.get("items") == []),
        "rewindWithoutSnapshotRefused": (
            outcomes["rewindEmpty"]["status"] == 409
            and outcomes["rewindEmpty"]["code"] == "CHECKPOINT_UNAVAILABLE"),
        "pilotEnabled": pilot_enabled,
    }

    evidence = {
        "schema": 1,
        "kind": "w3-stale-edit",
        "takenAt": datetime.now(UTC).isoformat(),
        "runId": run_id,
        "projectId": project_id,
        "leader": leader_id,
        "model": "none (deterministic API + component checks)",
        "durationSeconds": duration,
        "outcomes": outcomes,
        "intentAfterStale": intent,
        "intentAfterSteering": steered,
        "intentAfterTakeProposed": after_take.get("intent"),
        "openConflicts": [
            {"id": c.get("id"), "baseVersion": c.get("baseVersion"), "stale": c.get("stale"),
             "fields": [f.get("field") for f in c.get("fields") or []],
             "conflicting": [f.get("field") for f in c.get("fields") or []
                             if f.get("conflicting")]}
            for c in open_conflicts
        ],
        "latestPlanVersion": latest_version,
        "latestPlanGoal": latest_content.get("goal"),
        "checkpointProbe": rewind_probe,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [k for k, v in checks.items() if not v],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w3-stale-edit-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"],
                      "failed": evidence["failedChecks"],
                      "runId": run_id, "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
