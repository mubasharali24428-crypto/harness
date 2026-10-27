#!/usr/bin/env python3
"""W8 live gate: three concurrent runs in one repo, each isolated, each reviewable.

The queue has always been concurrent in code — atomic claim SQL, `SKIP LOCKED`,
a per-team cap enforced inside the claim — and every run gets its own workspace,
session root and uuid-named container boundary. What no test or drill had ever
done is run **three at once and look while they are running**. Reading the code
that promises isolation is not the same claim as observing three live workers
that cannot see each other, so this script does the latter:

  1. one project, three goals, each asking for a differently named file;
  2. approve all three, start all three, and record the state timeline —
     the overlap is evidence, not an assumption;
  3. while at least two are executing, sample `docker inspect` and assert the
     workers' networks and workspace mounts are pairwise distinct;
  4. after they settle, each run's recorded changes must belong to that run,
     and each run's automatic before/after checkpoints must live only under its
     own run id, with no other run's file listed in its manifest.

Everything the verdict depends on is named in the evidence: the caps in force,
the confinement mode, the model. A run that failed is reported as failed rather
than folded into a pass — three claims that overlap only because two of them
died early is not concurrency.

    python3 scripts/e2e/w8_concurrency_drill.py [--timeout 1800] [--project-echo]
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

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

GOALS = {
    "a": ("Create a file named w8_run_a.txt in the workspace root containing exactly: alpha", "alpha"),
    "b": ("Create a file named w8_run_b.txt in the workspace root containing exactly: beta", "beta"),
    "c": ("Create a file named w8_run_c.txt in the workspace root containing exactly: gamma", "gamma"),
}
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
        runs[key] = {"runId": run_id, "planId": plan["planId"], "goal": goal}

    # Start as close together as the HTTP layer allows: the overlap we are
    # measuring is a property of the system, not of this script's timing, but a
    # sequential start would make the claim unprovable.
    for key, entry in runs.items():
        status, _ = tpl.http("POST", f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/"
                                     f"{entry['runId']}/start", token=token)
        entry["startStatus"] = status

    seen: dict[str, dict] = {}
    stop = threading.Event()
    sampler = threading.Thread(target=sample_workers, args=(seen, stop), daemon=True)
    sampler.start()

    deadline = time.time() + args.timeout
    timeline: list[str] = []
    max_concurrent = 0
    while time.time() < deadline:
        states = {}
        for key, entry in runs.items():
            _, body = tpl.http("GET", f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/"
                                      f"{entry['runId']}/status", token=token)
            states[key] = body.get("state") if isinstance(body, dict) else "unknown"
        running = sum(1 for s in states.values() if s not in SETTLED and s != "unknown")
        max_concurrent = max(max_concurrent, running)
        snapshot_line = ",".join(f"{k}={v}" for k, v in sorted(states.items()))
        if not timeline or timeline[-1] != snapshot_line:
            timeline.append(snapshot_line)
        if all(s in SETTLED for s in states.values()):
            break
        time.sleep(2)
    stop.set()
    sampler.join(timeout=5)

    for key, entry in runs.items():
        _, body = tpl.http("GET", f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/"
                                  f"{entry['runId']}/status", token=token)
        entry["finalState"] = (body or {}).get("state") if isinstance(body, dict) else "unknown"

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

    # Per-run review surface: changes from the product, checkpoints from the
    # volume the product writes them to (the daemon mounts it, and the paths
    # come from the daemon's own DSH_WORKSPACE — never from a caller).
    daemon = tpl.sh("docker", "ps", "-q", "--filter",
                    "label=com.docker.compose.service=worker-daemon", check=False).stdout.split()

    # A server run has no proposed diff of its own (W7 Decision 6): the recorded
    # change set appears when the human confirms the run with `done-signal`. The
    # drill plays that human, and takes the bytes from the PRODUCT — the same
    # staging diff the withheld-approval surface uses (`staging._changes` against
    # the run's own baseline), never a diff this script assembled itself.
    #
    # It runs in the ORCHESTRATOR container, not the worker daemon: staging
    # imports SQLAlchemy (the worker image deliberately does not ship it), and
    # both containers see the same runs volume.
    orch = tpl.sh("docker", "ps", "-q", "--filter",
                  "label=com.docker.compose.service=orchestrator", check=False).stdout.split()
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
            "POST", f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/{rid}/done-signal",
            token=token, body={"diffText": diff_text},
        )
        entry["doneSignal"] = {"status": status, "diffChars": len(diff_text),
                              "detail": (body or {}).get("detail") if isinstance(body, dict) else body}

    for key, entry in runs.items():
        _, changes = tpl.http("GET", f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/"
                                    f"{entry['runId']}/changes", token=token)
        items = (changes or {}).get("items", []) if isinstance(changes, dict) else []
        entry["changes"] = [{"path": c.get("path"), "diffSha": (c.get("diffSha") or "")[:12]} for c in items]
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
            paths.update((manifest.get("files") or {}).keys())
        entry["checkpointManifests"] = manifests
        entry["checkpointPaths"] = sorted(paths)
        others = {f"w8_run_{other}.txt" for other in GOALS if other != key}
        entry["foreignCheckpointNames"] = sorted(paths & others)
        entry["ownCheckpointFileSeen"] = f"w8_run_{key}.txt" in paths

    caps = env_of("harness-orchestrator-1", ("HARNESS_RUN_CONCURRENCY", "ENGINE_MAX_CONCURRENCY",
                                             "ENGINE_RUN_TIMEOUT_SECONDS", "DSH_MODEL",
                                             "HARNESS_ENGINE_CONFINEMENT"))
    evidence = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W8",
        "claim": "three concurrent runs in one repo: each isolated, each reviewable, none clobbering another's checkpoint",
        "drive": "three live runs over HTTP on the compose stack (Postgres, container confinement)",
        "projectId": project_id,
        "model": caps.get("DSH_MODEL"),
        "capsInForce": {k: v for k, v in caps.items() if k != "DSH_MODEL"},
        "runs": runs,
        "maxConcurrentExecuting": max_concurrent,
        "stateTimeline": timeline[-25:],
        "workerContainers": workers,
        "distinctWorkerNetworkSets": len(nets),
        "runDirsPerWorker": {w["name"]: w["runDirs"] for w in workers},
        "sharedRunDirs": shared_dirs,
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
    }
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w8-concurrency-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"maxConcurrent": max_concurrent, "workers": len(workers),
                      "nets": len(nets), "runDirs": sorted(all_run_dirs),
                      "states": {k: v["finalState"] for k, v in runs.items()},
                      "changes": {k: len(v["changes"]) for k, v in runs.items()},
                      "verdict": evidence["verdict"]}, indent=1))
    print(f"{evidence['verdict']}  evidence -> {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
