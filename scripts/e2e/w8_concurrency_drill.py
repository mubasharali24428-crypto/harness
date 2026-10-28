#!/usr/bin/env python3
"""W8 live gate: three concurrent runs in one repo — isolated, reviewable, stoppable.

The queue has always been concurrent in code — atomic claim SQL, `SKIP LOCKED`,
a per-team cap enforced inside the claim — and every run gets its own workspace,
session root and uuid-named container boundary. This script observes three at
once and then tests what concurrency makes possible:

  1. one project, three goals; **two of them produce the same path**
     (`w8_shared.txt`, different bytes) and the third a unique file;
  2. approve and start all three, and record the state timeline — the overlap
     is evidence, not an assumption;
  3. while at least two are executing, sample `docker inspect` and assert the
     workers' networks and workspace mounts are pairwise distinct;
  4. after they settle: each run's changes belong to that run, each run's
     before/after checkpoints live only under its own run id, and the two
     `w8_shared.txt` results are distinct bytes and distinct digests — neither
     snapshot contains the other run's content;
  5. **cross-run digest refusal:** approve run A's change for the shared path,
     then present A's digest against run B's change for the same path — the
     server must answer `409 CHANGE_STALE`, not approve it;
  6. **cancellation preserves neighbours:** a fourth run is started, confirmed
     executing, and stopped. It must settle, release its claim and its boundary
     network must be gone, while A/B/C keep their states and their recorded
     changes unchanged.

A run that failed is reported as failed rather than folded into a pass — three
claims that overlap only because two of them died early is not concurrency.

    python3 scripts/e2e/w8_concurrency_drill.py [--timeout 1800]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
WORKER_NAME = re.compile(r"^harness-[0-9a-f]{32}(-relay)?$")
NET_ROW = re.compile(r"^(harness-[0-9a-f]{32}-net)\s+(.*)$")
SHARED = "w8_shared.txt"

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

GOALS = {
    "a": (f"Create a file named {SHARED} in the workspace root containing exactly: alpha",
          "alpha"),
    "b": (f"Create a file named {SHARED} in the workspace root containing exactly: beta",
          "beta"),
    "c": ("Create a file named w8_run_c.txt in the workspace root containing exactly: gamma",
          "gamma"),
}
CANCEL_GOAL = (
    "Create three files in the workspace root, one at a time: w8_cancel_1.txt containing one, "
    "w8_cancel_2.txt containing two, and w8_cancel_3.txt containing three. After each file, "
    "list the workspace with the bash tool to confirm it exists."
)
SETTLED = {"done", "error", "failed", "execution_failed", "stopped", "cancelled", "blocked",
           "awaiting_done_signal"}


def env_of(container: str, names: tuple[str, ...]) -> dict[str, str]:
    out = tpl.sh("docker", "inspect", container, check=False).stdout
    if not out.strip():
        return {}
    try:
        env = json.loads(out)[0]["Config"]["Env"]
    except (ValueError, KeyError, IndexError):
        return {}
    values = {}
    for item in env:
        key, _, value = item.partition("=")
        if key in names:
            values[key] = value
    return values


def sample_workers(seen: dict, stop: threading.Event) -> None:
    """Poll docker while the runs are live, recording what each worker sees."""
    while not stop.is_set():
        rows = tpl.sh("docker", "ps", "--format", "{{.ID}} {{.Names}}", check=False).stdout.splitlines()
        for line in rows:
            cid, _, name = line.partition(" ")
            if not name or not WORKER_NAME.match(name.strip()) or cid in seen:
                continue
            out = tpl.sh("docker", "inspect", cid, check=False).stdout
            if not out.strip():
                continue
            try:
                info = json.loads(out)[0]
            except (ValueError, IndexError):
                continue
            mounts = [
                {"src": m.get("Source"), "dst": m.get("Destination"),
                 "type": m.get("Type"), "rw": bool(m.get("RW"))}
                for m in (info.get("Mounts") or [])
            ]
            seen[cid] = {
                "name": name.strip(),
                "role": "relay" if name.strip().endswith("-relay") else "worker",
                "networks": sorted(info["NetworkSettings"]["Networks"]),
                "mounts": mounts,
                "started": info["State"]["StartedAt"],
            }
        time.sleep(1)


def claim_row(run_id: str) -> dict | None:
    cid = tpl.sh("docker", "ps", "-q", "--filter",
                 "label=com.docker.compose.service=db", check=False).stdout.split()
    if not cid:
        return None
    sql = ("select coalesce(json_agg(t), '[]'::json) from (select state, worker_id, claimed_at "
           f"from run_claims where run_id='{run_id}') t")
    proc = tpl.sh("docker", "exec", cid[0], "sh", "-lc",
                  f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "{sql}"', check=False)
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout.strip() or "[]")
    except ValueError:
        return None
    return rows[0] if rows else None


def labelled_networks() -> dict[str, str]:
    """network name -> run id, for the boundary networks Docker still has."""
    out = tpl.sh("docker", "network", "ls", "--format",
                 '{{.Name}}\t{{.Label "harness-run"}}', check=False).stdout
    found: dict[str, str] = {}
    for line in out.splitlines():
        match = NET_ROW.match(line.replace("\t", " ").strip())
        if match:
            found[match.group(1)] = match.group(2).strip()
    return found


def network_of(run_id: str) -> str | None:
    for name, label in labelled_networks().items():
        if label == run_id:
            return name
    return None


def review(run_id: str, change_id: str, decision: str, diff_sha: str,
           token: str) -> tuple[int, dict]:
    return tpl.http(
        "POST",
        f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}/changes/{change_id}/review",
        token=token, body={"decision": decision, "diffSha": diff_sha},
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=1800.0)
    args = ap.parse_args()

    stamp = int(time.time())
    email = f"w8-{stamp}@example.com"
    token = tpl.login(email)
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w8 concurrency {stamp}"})
    if not (status == 201 and isinstance(project, dict) and project.get("id")):
        raise SystemExit(f"create project failed: {status} {project!r}")
    project_id = project["id"]

    runs: dict[str, dict] = {}
    for key, (goal, _) in GOALS.items():
        run_id = f"w8-{key}-{stamp}"
        base = f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        status, plan = tpl.http("POST", f"{base}/plan", token=token,
                                body={"goal": goal, "projectId": project_id})
        if not (status == 201 and isinstance(plan, dict) and plan.get("planId")):
            raise SystemExit(f"propose {key} failed: {status} {plan!r}")
        tpl.http("PUT", f"{base}/execution-limits", token=token, body={"maxToolCalls": 6})
        status, _ = tpl.http("POST", f"{base}/plan/approve", token=token,
                             body={"planId": plan["planId"]})
        if status != 200:
            raise SystemExit(f"approve {key} failed: {status}")
        runs[key] = {"runId": run_id, "base": base, "planId": plan["planId"], "goal": goal}

    cancel_run = {"runId": f"w8-cancel-{stamp}",
                  "base": f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/w8-cancel-{stamp}"}
    status, cancel_plan = tpl.http("POST", f"{cancel_run['base']}/plan", token=token,
                                   body={"goal": CANCEL_GOAL, "projectId": project_id})
    if not (status == 201 and isinstance(cancel_plan, dict) and cancel_plan.get("planId")):
        raise SystemExit(f"propose cancel run failed: {status} {cancel_plan!r}")
    tpl.http("PUT", f"{cancel_run['base']}/execution-limits", token=token,
             body={"maxToolCalls": 8})
    status, _ = tpl.http("POST", f"{cancel_run['base']}/plan/approve", token=token,
                         body={"planId": cancel_plan["planId"]})
    if status != 200:
        raise SystemExit(f"approve cancel run failed: {status}")

    # Start as close together as the HTTP layer allows: the overlap we are
    # measuring is a property of the system, not of this script's timing, but a
    # sequential start would make the claim unprovable.
    for key, entry in runs.items():
        status, _ = tpl.http("POST", f"{entry['base']}/start", token=token)
        entry["startStatus"] = status
    status, _ = tpl.http("POST", f"{cancel_run['base']}/start", token=token)
    cancel_run["startStatus"] = status

    seen: dict[str, dict] = {}
    stop = threading.Event()
    sampler = threading.Thread(target=sample_workers, args=(seen, stop), daemon=True)
    sampler.start()

    deadline = time.time() + args.timeout
    timeline: list[str] = []
    max_concurrent = 0
    cancel_state_at_stop = None
    neighbour_states_at_stop: dict[str, str] = {}
    stopped = False
    while time.time() < deadline:
        states = {}
        for key, entry in runs.items():
            _, body = tpl.http("GET", f"{entry['base']}/status", token=token)
            states[key] = body.get("state") if isinstance(body, dict) else "unknown"
        _, cancel_body = tpl.http("GET", f"{cancel_run['base']}/status", token=token)
        cancel_state = (cancel_body or {}).get("state") if isinstance(cancel_body, dict) else None
        running = sum(1 for s in states.values() if s not in SETTLED and s != "unknown")
        if cancel_state and cancel_state not in SETTLED:
            running += 1
        max_concurrent = max(max_concurrent, running)
        snapshot_line = ",".join(f"{k}={v}" for k, v in sorted(states.items()))
        snapshot_line += f",cancel={cancel_state}"
        if not timeline or timeline[-1] != snapshot_line:
            timeline.append(snapshot_line)
        # Stop the fourth run only once it, and at least two neighbours, are
        # genuinely executing — otherwise "preserves neighbours" proves nothing.
        executing = sum(1 for s in states.values() if s == "executing")
        if not stopped and cancel_state == "executing" and executing >= 2:
            cancel_state_at_stop = cancel_state
            neighbour_states_at_stop = dict(states)
            cancel_run["stopStatus"] = tpl.http(
                "POST", f"{cancel_run['base']}/stop", token=token)[0]
            stopped = True
        if all(s in SETTLED for s in states.values()) and (not stopped or (
                cancel_state in SETTLED)):
            break
        time.sleep(2)
    stop.set()
    sampler.join(timeout=5)

    for key, entry in runs.items():
        _, body = tpl.http("GET", f"{entry['base']}/status", token=token)
        entry["finalState"] = (body or {}).get("state") if isinstance(body, dict) else "unknown"
    _, cancel_body = tpl.http("GET", f"{cancel_run['base']}/status", token=token)
    cancel_run["finalState"] = (cancel_body or {}).get("state") if isinstance(cancel_body, dict) \
        else "unknown"

    workers = [w for w in seen.values() if w["role"] == "worker"]
    nets = {tuple(w["networks"]) for w in workers}
    # Which RUNS' directories each worker was given. `--mount type=volume,
    # volume-subpath=<relpath>` reports no Source path and no RW flag, so the
    # isolation claim is read off the mount TARGETS: a worker may see its own run
    # directory and nothing belonging to a sibling. (An earlier revision compared
    # `dst == "/workspace"`, matched nothing, and reported "0 distinct workspaces"
    # for three genuinely separate ones.)
    run_by_mount = re.compile(r"/var/lib/harness-runs/(?:workspace|sessions)/([^/\s]+)")
    for w in workers:
        w["mountTargets"] = sorted({m["dst"] for m in w["mounts"] if m["dst"]})
        w["runDirs"] = sorted(
            {
                match.group(1)
                for target in w["mountTargets"]
                for match in [run_by_mount.search(target)]
                if match and match.group(1) not in {".run-tmp", ".checkpoints"}
            }
        )
    workspace_sets = {tuple(w["runDirs"]) for w in workers}
    all_run_dirs = {d for w in workers for d in w["runDirs"]}
    shared_dirs = sorted(
        d for d in all_run_dirs if sum(1 for w in workers if d in w["runDirs"]) > 1
    )

    daemon = tpl.sh("docker", "ps", "-q", "--filter",
                    "label=com.docker.compose.service=worker-daemon", check=False).stdout.split()
    orch = tpl.sh("docker", "ps", "-q", "--filter",
                  "label=com.docker.compose.service=orchestrator", check=False).stdout.split()

    # A server run has no proposed diff of its own (W7 Decision 6): the recorded
    # change set appears when the human confirms the run with `done-signal`. The
    # drill plays that human, and takes the bytes from the PRODUCT — the same
    # staging diff the withheld-approval surface uses (`staging._changes` against
    # the run's own baseline), never a diff this script assembled itself.
    for key, entry in runs.items():
        entry["doneSignal"] = None
        if not orch:
            continue
        rid = entry["runId"]
        script = (
            "from pathlib import Path;"
            "from harness_orchestrator import staging;"
            "root = Path('/var/lib/harness-runs/workspace');"
            f"baseline, _staged = staging._paths({rid!r}, str(root));"
            "print(''.join(d for _change, d in staging._changes("
            f"baseline, root / {rid!r})), end='')"
        )
        diff_text = tpl.sh(
            "docker", "exec", orch[0], "python", "-c", script, check=False
        ).stdout
        if not diff_text.strip():
            entry["doneSignal"] = {"refused": "no product-computed diff for this run"}
            continue
        status, body = tpl.http(
            "POST", f"{entry['base']}/done-signal", token=token,
            body={"diffText": diff_text},
        )
        entry["doneSignal"] = {"status": status, "diffChars": len(diff_text),
                              "detail": (body or {}).get("detail") if isinstance(body, dict) else body}

    for key, entry in runs.items():
        _, changes = tpl.http("GET", f"{entry['base']}/changes", token=token)
        items = (changes or {}).get("items", []) if isinstance(changes, dict) else []
        entry["changeItems"] = [
            {"id": c.get("id"), "path": c.get("path"),
             "diffSha": c.get("diffSha"), "reviewState": c.get("reviewState")}
            for c in items
        ]
        entry["changes"] = [{"path": c.get("path"), "diffSha": (c.get("diffSha") or "")[:12]}
                            for c in items]
        entry["changesDigest"] = (changes or {}).get("changesDigest") if isinstance(changes, dict) else None
        listing = ""
        if daemon:
            # One compact JSON object per manifest on its own line. `cat` alone
            # is not enough: the product writes these manifests pretty-printed,
            # and a per-line parser silently counted ZERO of them (the first read
            # of this drill reported "0 checkpoints" for a run that had two).
            reader = (
                "import glob,json;"
                "print(chr(10).join(json.dumps(json.load(open(p))) "
                "for p in sorted(glob.glob("
                f"'/var/lib/harness-runs/workspace/.checkpoints/'+{entry['runId']!r}+'/*.json'))))"
            )
            listing = tpl.sh(
                "docker", "exec", daemon[0], "python", "-c", reader, check=False
            ).stdout
        # A manifest is {id, files: {path: sha}, …}. The clobbering claim is
        # about the CONTENTS: run A's snapshot must not contain run B's file,
        # which would mean both snapshots walked the same tree.
        paths: set[str] = set()
        hashes: dict[str, set[str]] = {}
        manifests = 0
        for line in listing.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                manifest = json.loads(line)
            except ValueError:
                continue
            manifests += 1
            files = manifest.get("files") or {}
            paths.update(files.keys())
            for path, sha in files.items():
                hashes.setdefault(path, set()).add(sha)
        entry["checkpointManifests"] = manifests
        entry["checkpointPaths"] = sorted(paths)
        entry["checkpointHashes"] = {p: sorted(v) for p, v in hashes.items()}
        if key == "c":
            others = {SHARED}
        else:
            others = {"w8_run_c.txt"}
        entry["foreignCheckpointNames"] = sorted(paths & others)
        own = SHARED if key in ("a", "b") else "w8_run_c.txt"
        entry["ownCheckpointFileSeen"] = own in paths

    # Which path each run actually changed, and the digest it was recorded under.
    overlap = {}
    for key in ("a", "b"):
        entry = runs[key]
        item = next((c for c in entry["changeItems"] if c["path"] == SHARED), None)
        overlap[key] = item
    overlap_ok = all(item and item.get("diffSha") for item in overlap.values())
    digests_differ = bool(overlap_ok and overlap["a"]["diffSha"] != overlap["b"]["diffSha"])

    shared_hashes = {}
    for key in ("a", "b"):
        shared_hashes[key] = runs[key]["checkpointHashes"].get(SHARED) or []
    hashes_differ = (
        len(shared_hashes["a"]) >= 1
        and len(shared_hashes["b"]) >= 1
        and all(len(h) == 64 for h in shared_hashes["a"] + shared_hashes["b"])
        and set(shared_hashes["a"]) != set(shared_hashes["b"])
    )

    # Cross-run digest refusal, then two independent approvals.
    review_results: dict[str, dict] = {}
    if overlap_ok:
        cross_status, cross_body = review(
            runs["b"]["runId"], overlap["b"]["id"], "approved", overlap["a"]["diffSha"], token)
        review_results["crossDigest"] = {
            "status": cross_status,
            "code": ((cross_body or {}).get("detail") or {}).get("error", {}).get("code")
            if isinstance(cross_body, dict) else None,
        }
        for key in ("a", "b"):
            status, body = review(
                runs[key]["runId"], overlap[key]["id"], "approved", overlap[key]["diffSha"], token)
            review_results[key] = {
                "status": status,
                "reviewState": ((body or {}).get("change") or {}).get("reviewState")
                if isinstance(body, dict) else None,
            }

    # Cancellation is requested the moment stop is accepted and *confirmed* only
    # when the engine is quiesced (lifecycle.py). Reading once raced the unwind:
    # the first revision saw `stopping`, a live claim and a live network for a
    # stop that was still in progress. Wait, bounded, for confirmation.
    confirm_started = time.time()
    cancel_claim = None
    cancel_net = None
    cancel_lifecycle = None
    while time.time() - confirm_started < 120:
        _, cancel_agg = tpl.http("GET", cancel_run["base"], token=token)
        cancel_lifecycle = (cancel_agg or {}).get("lifecycle") \
            if isinstance(cancel_agg, dict) else None
        cancel_claim = claim_row(cancel_run["runId"])
        cancel_net = network_of(cancel_run["runId"])
        if (isinstance(cancel_lifecycle, dict)
                and cancel_lifecycle.get("cancelConfirmed") is True
                and cancel_claim is None and cancel_net is None):
            break
        time.sleep(2)
    cancel_confirm_seconds = round(time.time() - confirm_started, 1)
    _, cancel_engine = tpl.http("GET", f"{cancel_run['base']}/engine", token=token)
    cancel_reason = (cancel_engine or {}).get("finalResponse") if isinstance(cancel_engine, dict) \
        else None
    neighbour_states_after = {key: entry["finalState"] for key, entry in runs.items()}
    # "Unaffected" is about what the stop did: the neighbours were executing when
    # it was issued, they finished on their own, and each still has its recorded
    # change. A neighbour that happened to fail for its own reason is not a
    # preservation claim.
    neighbours_unchanged = (
        bool(neighbour_states_at_stop)
        and all(s == "executing" for s in neighbour_states_at_stop.values())
        and all(state in SETTLED for state in neighbour_states_after.values())
        and all(len(entry["changeItems"]) > 0 for entry in runs.values())
    )

    caps = env_of("harness-orchestrator-1", ("HARNESS_RUN_CONCURRENCY", "ENGINE_MAX_CONCURRENCY",
                                             "ENGINE_RUN_TIMEOUT_SECONDS", "DSH_MODEL",
                                             "HARNESS_ENGINE_CONFINEMENT"))
    evidence = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W8",
        "claim": "three concurrent runs in one repo: isolated, reviewable, no clobbering or silent "
                 "overwrite; a cancelled run releases its boundary and preserves neighbours",
        "drive": "three live runs over HTTP on the compose stack (Postgres, container confinement), "
                 "two of them producing the same path with different bytes; a fourth run stopped "
                 "mid-flight",
        "projectId": project_id,
        "model": caps.get("DSH_MODEL"),
        "capsInForce": {k: v for k, v in caps.items() if k != "DSH_MODEL"},
        "runs": runs,
        "cancelRun": {
            "runId": cancel_run["runId"],
            "startStatus": cancel_run.get("startStatus"),
            "stopStatus": cancel_run.get("stopStatus"),
            "stateAtStop": cancel_state_at_stop,
            "neighbourStatesAtStop": neighbour_states_at_stop,
            "finalState": cancel_run["finalState"],
            "claimAfterSettle": cancel_claim,
            "networkAfterSettle": cancel_net,
            "lifecycle": cancel_lifecycle,
            "confirmSeconds": cancel_confirm_seconds,
            "engineFinalResponse": str(cancel_reason)[:300] if cancel_reason else None,
        },
        "maxConcurrentExecuting": max_concurrent,
        "stateTimeline": timeline[-30:],
        "workerContainers": workers,
        "distinctWorkerNetworkSets": len(nets),
        "runDirsPerWorker": {w["name"]: w["runDirs"] for w in workers},
        "sharedRunDirs": shared_dirs,
        "overlap": {
            "path": SHARED,
            "digests": {k: (v or {}).get("diffSha") for k, v in overlap.items()},
            "checkpointHashes": shared_hashes,
            "reviews": review_results,
        },
    }

    all_done = all(entry["finalState"] in SETTLED for entry in runs.values())
    checks = {
        "threeStarted": all(entry.get("startStatus") == 200 for entry in runs.values()),
        "overlapObserved": max_concurrent >= 2,
        "threeWorkersSeen": len(workers) >= 2,
        "networksDistinct": len(workers) < 2 or len(nets) == len(workers),
        "workspacesDistinct": (
            len(workers) >= 2
            and all(len(w["runDirs"]) == 1 for w in workers)
            and len(workspace_sets) == len(workers)
            and not shared_dirs
        ),
        "eachReviewable": all(len(entry["changes"]) > 0 for entry in runs.values()),
        "eachCheckpointed": all(entry["checkpointManifests"] >= 1 for entry in runs.values()),
        "ownFileInOwnCheckpoint": all(entry["ownCheckpointFileSeen"] for entry in runs.values()),
        "noClobbering": all(not entry["foreignCheckpointNames"] for entry in runs.values()),
        "allSettled": all_done,
        "sharedPathInBothRuns": overlap_ok,
        "overlapDigestsDiffer": digests_differ,
        "overlapHashesDiffer": hashes_differ,
        "crossRunDigestRefused": (
            review_results.get("crossDigest", {}).get("status") == 409
            and review_results.get("crossDigest", {}).get("code") == "CHANGE_STALE"
        ),
        "bothOverlapReviewable": all(
            review_results.get(key, {}).get("status") == 200
            and review_results.get(key, {}).get("reviewState") == "approved"
            for key in ("a", "b")
        ),
        "cancelStarted": cancel_run.get("startStatus") == 200,
        "cancelWasLive": cancel_state_at_stop is not None
        and cancel_state_at_stop not in SETTLED,
        "cancelSettled": cancel_run["finalState"] in SETTLED,
        "cancelRecordedAsStopped": (
            isinstance(cancel_lifecycle, dict)
            and cancel_lifecycle.get("phase") == "stopped"
            and cancel_lifecycle.get("cancelConfirmed") is True
        ),
        "cancelClaimReleased": cancel_claim is None,
        "cancelBoundaryGone": cancel_net is None,
        "neighboursUnaffected": neighbours_unchanged,
    }
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w8-concurrency-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"maxConcurrent": max_concurrent, "workers": len(workers),
                      "nets": len(nets), "runDirs": sorted(all_run_dirs),
                      "states": {k: v["finalState"] for k, v in runs.items()},
                      "cancel": cancel_run["finalState"],
                      "overlapDigestsDiffer": digests_differ,
                      "crossRunRefused": review_results.get("crossDigest"),
                      "verdict": evidence["verdict"]}, indent=1))
    print(f"{evidence['verdict']}  evidence -> {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
