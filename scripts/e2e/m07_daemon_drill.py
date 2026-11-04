#!/usr/bin/env python3
"""M07 topology drill: can the DEPLOYED orchestrator run a confined worker?

W1 found it could not: the orchestrator container has no Docker socket (by design), and the
M04/M07 evidence came from host processes with the host's CLI. This drill runs through the
compose stack instead (``--profile worker`` up):

1. the orchestrator container has no Docker socket and no Docker CLI;
2. the engine, running INSIDE that container, gets a worker from ``worker-daemon``;
3. while it runs, the worker container is inspected on the engine: non-root user,
   read-only root, no capabilities, an internal network, run dirs as volume subpaths,
   the forbidden file mounted read-only;
4. the task - fix a bug in a fixture repo and read the file back - lands on disk.

Spend: none (local Ollama through the per-run relay).

    section4/.venv/bin/python scripts/e2e/m07_daemon_drill.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "--env-file", str(REPO / ".env"), "-f",
           str(REPO / "deploy/docker-compose.yml"), "--profile", "worker"]
GOAL = (
    "calc.py has a bug: add() must return the sum of its arguments. Read calc.py, fix it with "
    "the edit or write tool, then read calc.py again to verify the fix, and reply DONE."
)

IN_CONTAINER = r"""
import json, os, sys, time
from harness_orchestrator.engine import DeepSeekHarnessWrapper
sid, model, base, goal = sys.argv[1:5]
e = DeepSeekHarnessWrapper(model=model, base_url=base, api_key="local", live_mode="live")
ws = e.provision_workspace(sid)
open(os.path.join(ws, "calc.py"), "w").write("def add(a, b):\n    return a - b\n")
open(os.path.join(ws, "README.md"), "w").write("calc fixture: add() must return the sum.\n")
e.set_shell_policy(sid, {"scopePaths": [], "forbiddenPaths": ["README.md"], "maxToolCalls": 12})
recs, t0 = [], time.monotonic()
r = e.execute_goal(goal, session_id=sid, on_progress=lambda p: recs.append(
    {"t": round(time.monotonic() - t0, 2), "phase": p.get("phase"),
     "tool": p.get("tool") or p.get("name")}))
print(json.dumps({
    "daemonUrl": e.daemon_url, "status": r.get("status"), "seconds": round(time.monotonic() - t0, 2),
    "calc": open(os.path.join(ws, "calc.py")).read(),
    "readmeUnchanged": open(os.path.join(ws, "README.md")).read().startswith("calc fixture"),
    "records": recs[:30], "finalResponseExcerpt": (r.get("finalResponse") or "")[:300],
}))
"""


def sh(*argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, check=check)


def inspect_workers(image: str, seen: dict, stop: threading.Event) -> None:
    """Snapshot each worker container the daemon starts, while it is still running."""
    while not stop.is_set():
        ids = sh("docker", "ps", "-q", "--filter", f"ancestor={image}", check=False).stdout.split()
        for cid in ids:
            if cid in seen:
                continue
            out = sh("docker", "inspect", cid, check=False).stdout
            if out.strip():
                c = json.loads(out)[0]
                seen[cid] = {
                    "role": "relay" if c["Name"].endswith("-relay") else "worker",
                    "user": c["Config"]["User"],
                    "readonlyRootfs": c["HostConfig"]["ReadonlyRootfs"],
                    "capDrop": c["HostConfig"]["CapDrop"],
                    "networks": sorted(c["NetworkSettings"]["Networks"]),
                    "mounts": [  # as requested; plain "Mounts" drops the volume subpath
                        {"type": m["Type"], "src": m.get("Source"), "dst": m["Target"],
                         "readOnly": bool(m.get("ReadOnly")),
                         "subpath": (m.get("VolumeOptions") or {}).get("Subpath")}
                        for m in c["HostConfig"].get("Mounts") or []
                    ],
                }
        time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="qwen-local:latest")
    ap.add_argument("--base-url", default="http://host.docker.internal:11434/v1")
    ap.add_argument("--image", default="harness-engine-worker:vdev-local")
    ap.add_argument("--evidence", default=str(REPO / "docs/briefs/evidence/pilot-prep"
                                              / "m07-daemon-topology.json"))
    args = ap.parse_args()

    probe = sh(*COMPOSE, "exec", "-T", "orchestrator", "sh", "-c",
               "test -S /var/run/docker.sock && echo SOCKET; command -v docker || echo NO_CLI",
               check=False).stdout.split()
    no_socket, no_cli = "SOCKET" not in probe, "NO_CLI" in probe

    run_id = f"m07d-{int(time.time())}"
    seen: dict = {}
    stop = threading.Event()
    watcher = threading.Thread(target=inspect_workers, args=(args.image, seen, stop), daemon=True)
    watcher.start()
    res = sh(*COMPOSE, "exec", "-T", "orchestrator", "python", "-c", IN_CONTAINER,
             run_id, args.model, args.base_url, GOAL, check=False)
    stop.set()
    watcher.join(timeout=5)
    try:
        run = json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(res.stdout[-2000:], res.stderr[-2000:], sep="\n", file=sys.stderr)
        return 1
    workers = list(seen.values())
    out = {
        "date": time.strftime("%Y-%m-%d"),
        "milestone": "M07 topology",
        "runId": run_id,
        "model": args.model,
        "orchestratorHasNoDockerSocket": no_socket,
        "orchestratorHasNoDockerCli": no_cli,
        "workersObserved": workers,
        **run,
    }
    Path(args.evidence).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "records"}, indent=2))
    fixed = "a + b" in run.get("calc", "")
    w = next((x for x in workers if x["role"] == "worker"), {})
    ok = (no_socket and no_cli and run.get("daemonUrl") and bool(w)
          and not str(w.get("user", "0")).startswith("0") and w.get("readonlyRootfs")
          and run.get("readmeUnchanged") and fixed)
    print(f"\n{'PASS' if ok else 'FAIL'} - workers seen={len(workers)} fixed={fixed} "
          f"status={run.get('status')}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
