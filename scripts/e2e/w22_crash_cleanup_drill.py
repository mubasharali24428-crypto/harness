#!/usr/bin/env python3
"""W8 live gate, claim 2: a crashed run's boundary is reclaimed, its neighbours are not.

The boundary sweep is a deletion path over shared infrastructure, so a unit test
of its logic is not enough — it has to be shown against a real orphan and a real
live run beside it. This script makes the crash it is designed for, on the
running stack, and then checks what it does and does not touch:

  1. two live runs in one project, each with its own labelled boundary network
     (`harness-run=<run id>`, which is what makes an orphan traceable at all);
  2. the victim's `docker run` CLIENT is SIGKILLed inside the worker daemon. The
     container it launched keeps running — that is the W4 failure mode: 17-20
     orphaned networks on one dev stack, ending in "all predefined address pools
     have been fully subnetted" and no run able to start;
  3. the run is observed through the product's own crash handling: it settles,
     its claim is gone, and the audit says why;
  4. the sweep runs INSIDE the daemon (the only process with the Docker socket)
     against the real orphans, and is asserted to (a) refuse a live run it is
     told to hold even when also told it is settled, and (b) remove the victim's
     network + stopped container while leaving the neighbour's untouched.

A refusal that cannot be demonstrated is not a safety property, so the held-run
check is performed on the neighbour while it is genuinely executing.

    python3 scripts/e2e/w8_crash_cleanup_drill.py [--timeout 900]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "w22"
ROOT = "/var/lib/harness-runs/workspace"
SETTLED = {"done", "error", "failed", "execution_failed", "stopped", "cancelled", "blocked",
           "awaiting_done_signal"}
NET_ROW = re.compile(r"^(harness-[0-9a-f]{32}-net)\s+(.*)$")

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

GOALS = {
    "victim": "Create a file named w8_crash_victim.txt in the workspace root containing exactly: victim",
    # Deliberately longer than the victim's: the sweep must run while this one is
    # still mid-flight, or "the neighbour was untouched" is untested.
    "neighbour": (
        "Use the bash tool to run exactly `python3 -c 'import time; time.sleep(180)'` "
        "and wait for it to finish. Do not do any other work before that command."
    ),
}


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


def containers_on(network: str) -> list[dict[str, str]]:
    out = tpl.sh("docker", "network", "inspect", network, check=False).stdout
    if not out.strip():
        return []
    try:
        attached = json.loads(out)[0].get("Containers") or {}
    except (ValueError, IndexError, KeyError):
        return []
    rows = []
    for cid in attached:
        listed = tpl.sh("docker", "ps", "--no-trunc", "--format",
                        "{{.ID}}|{{.Names}}|{{.State}}", check=False).stdout
        for line in listed.splitlines():
            cid_seen, _, rest = line.partition("|")
            if cid_seen == cid:
                name, _, state = rest.rpartition("|")
                rows.append({"id": cid, "name": name, "state": state.strip()})
    return rows


def daemon() -> str:
    cid = tpl.sh("docker", "ps", "-q", "--filter",
                 "label=com.docker.compose.service=worker-daemon", check=False).stdout.split()
    if not cid:
        raise SystemExit("worker-daemon container not found (compose profile 'worker'?)")
    return cid[0]


def make_orphan(docker_id: str, run_id: str) -> dict:
    """Leave exactly the wreckage a SIGKILLed launcher leaves, with no cleanup path.

    Killing a run's `docker run` client is not enough on its own: the launcher that
    spawned it is still alive and runs its `close()` in a `finally`, so the network
    goes away by itself (that half is what the victim run below proves, through the
    product's own crash handling). The failure this sweep exists for is the LAUNCHER
    dying: its container keeps running, and the network, relay and proxy alias stay
    attached with nobody left to remove them.

    So this builds the boundary with the product's own classes inside the daemon and
    then SIGKILLs the process that made it. The container is started on a sleep so it
    outlives its parent — exactly the "running corpse" the weak rule refuses to judge
    and only the durable proof may stop.
    """
    script = (
        "import json, os, signal, subprocess, time\n"
        "from harness_orchestrator import confine, container_runtime\n"
        "from harness_orchestrator.engine import DeepSeekHarnessWrapper\n"
        f"rid = {run_id!r}\n"
        "w = DeepSeekHarnessWrapper(api_key='x')\n"
        "ws, ss, tmp = w.run_workspace(rid), w.run_session_dir(rid), w.run_tmp_dir(rid)\n"
        "for d in (ws, ss, tmp):\n"
        "    os.makedirs(d, exist_ok=True)\n"
        "b = container_runtime.ContainerBoundary('https://api.deepseek.com', run_id=rid)\n"
        "b.start()\n"
        "argv = confine.wrap_command(\n"
        "    ['python', '-m', 'harness_orchestrator.engine_worker'], mode='container',\n"
        "    roots=[ws, ss], workspace=ws, session_dir=ss, tmp_dir=tmp,\n"
        "    container_name=b.name, network=b.network, image=b.image, env={})[:-2]\n"
        "argv += ['-c', 'import time; time.sleep(1800)']\n"
        "subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "                 stderr=subprocess.DEVNULL)\n"
        "time.sleep(3)\n"
        "print(json.dumps({'network': b.network, 'container': b.name}), flush=True)\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n"
    )
    proc = tpl.sh("docker", "exec", docker_id, "python", "-c", script, check=False)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise SystemExit(f"orphan setup printed nothing usable: {proc.stdout[:200]} {proc.stderr[:300]}")


def sweep(docker_id: str, held: list[str], settled: list[str] | None) -> dict:
    """Run the product's own sweep inside the daemon — the only holder of the socket."""
    script = (
        "import json;"
        "from harness_orchestrator.container_runtime import sweep_orphan_boundaries;"
        f"held = {json.dumps(sorted(held))};"
        f"settled = {json.dumps(None if settled is None else sorted(settled))};"
        "print(json.dumps(sweep_orphan_boundaries(set(held),"
        " None if settled is None else set(settled))))"
    )
    proc = tpl.sh("docker", "exec", docker_id, "python", "-c", script, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"sweep failed in the daemon: {proc.stderr.strip()[:400]}")
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise SystemExit(f"sweep printed nothing usable: {proc.stdout[:300]!r}")


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
    body = proc.stdout.strip()
    try:
        rows = json.loads(body or "[]")
    except ValueError:
        return None
    return rows[0] if rows else None


def kill_client(docker_id: str, container_name: str) -> str:
    """SIGKILL the `docker run` client for one container, from inside the daemon.

    The daemon launched it as a subprocess; killing the client is what an
    orchestrator/daemon crash looks like to Docker. The container it was
    supervising keeps running, and nothing is left to remove its network.
    """
    script = (
        "import glob,os,signal;"
        f"needle={container_name!r}.encode();"
        "victims=[int(p.split('/')[2]) for p in glob.glob('/proc/[0-9]*/cmdline')"
        " if needle in open(p,'rb').read() and b'worker_daemon' not in open(p,'rb').read()];"
        "[os.kill(pid, signal.SIGKILL) for pid in victims];"
        "print(len(victims))"
    )
    proc = tpl.sh("docker", "exec", docker_id, "python", "-c", script, check=False)
    return proc.stdout.strip() or proc.stderr.strip()[:200]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()
    docker_id = daemon()

    stamp = int(time.time())
    token = tpl.login(f"w8-crash-{stamp}@example.com")
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w8 crash {stamp}"})
    if not (status == 201 and isinstance(project, dict) and project.get("id")):
        raise SystemExit(f"create project failed: {status} {project!r}")

    runs: dict[str, dict] = {}
    for key, goal in GOALS.items():
        run_id = f"w8crash-{key}-{stamp}"
        base = f"{tpl.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
        status, plan = tpl.http("POST", f"{base}/plan", token=token,
                                body={"goal": goal, "projectId": project["id"]})
        if not (status == 201 and isinstance(plan, dict) and plan.get("planId")):
            raise SystemExit(f"propose {key} failed: {status} {plan!r}")
        tpl.http("PUT", f"{base}/execution-limits", token=token, body={"maxToolCalls": 6})
        status, _ = tpl.http("POST", f"{base}/plan/approve", token=token,
                             body={"planId": plan["planId"]})
        if status != 200:
            raise SystemExit(f"approve {key} failed: {status}")
        runs[key] = {"runId": run_id, "base": base}
        status, _ = tpl.http("POST", f"{runs[key]['base']}/start", token=token)
        runs[key]["startStatus"] = status

    # Wait for both to be executing AND for both boundary networks to exist: the
    # crash has to land on a live boundary, not on a run that has not started one.
    deadline = time.time() + args.timeout / 2
    while time.time() < deadline:
        states = {
            key: (tpl.http("GET", f"{entry['base']}/status", token=token)[1] or {}).get("state")
            for key, entry in runs.items()
        }
        nets = {key: network_of(entry["runId"]) for key, entry in runs.items()}
        if all(states.get(key) == "executing" for key in runs) and all(nets.values()):
            break
        time.sleep(2)
    else:
        raise SystemExit(f"both runs never reached executing with networks: {states} {nets}")
    for key, entry in runs.items():
        entry["network"] = nets[key]

    # Refusal proof, on the live neighbour: told to HOLD it, the sweep must not
    # remove it even when also told it is SETTLED. Only the neighbour is named —
    # naming the victim here would destroy the very orphan the next steps need.
    held_refusal = sweep(docker_id, [runs["neighbour"]["runId"]], [runs["neighbour"]["runId"]])
    neighbour_network_before = network_of(runs["neighbour"]["runId"])

    victim_name = runs["victim"]["network"][: -len("-net")]
    killed = kill_client(docker_id, victim_name)

    # The product notices the crash on its own: the engine call fails, the run
    # settles, the claim goes, the audit says why. (Its own `close()` also cleans
    # the boundary up, which is why the leak below is built separately.)
    victim_state = None
    victim_claim = None
    deadline2 = time.time() + args.timeout / 2
    while time.time() < deadline2:
        _, body = tpl.http("GET", f"{runs['victim']['base']}/status", token=token)
        victim_state = (body or {}).get("state") if isinstance(body, dict) else None
        victim_claim = claim_row(runs["victim"]["runId"])
        if victim_state in SETTLED and victim_claim is None:
            break
        time.sleep(2)
    _, audit = tpl.http("GET", f"{runs['victim']['base']}/engine", token=token)
    reason = None
    if isinstance(audit, dict):
        reason = audit.get("finalResponse")
    # The wreckage a dead launcher leaves, labelled with the now-settled run: a
    # network, a relay and a still-running worker container that no process owns.
    make_orphan(docker_id, runs["victim"]["runId"])
    time.sleep(2)
    orphan_network = network_of(runs["victim"]["runId"])
    orphan_containers = containers_on(orphan_network) if orphan_network else []

    # Snapshot the neighbour AT THE MOMENT of the sweep. What the claim is about
    # is what the sweep does — a neighbour that finishes its own run a second
    # later and removes its own network through `close()` has not been touched by
    # anything here, and must not be reported as if it had been.
    neighbour_before = network_of(runs["neighbour"]["runId"])
    neighbour_containers_before = containers_on(neighbour_before) if neighbour_before else []
    neighbour_state_at_sweep = (
        tpl.http("GET", f"{runs['neighbour']['base']}/status", token=token)[1] or {}
    ).get("state")

    # Now the real sweep, with the durable proof for the victim only.
    report = sweep(docker_id, [runs["neighbour"]["runId"]], [runs["victim"]["runId"]])
    removed = [entry["network"] for entry in report.get("removed", [])]
    kept = list(report.get("kept", []))
    stopped_ids = {cid for entry in report.get("removed", []) for cid in entry.get("stopped", [])}
    after = labelled_networks()
    neighbour_network_after = network_of(runs["neighbour"]["runId"])
    neighbour_still_up = bool(neighbour_network_after) and any(
        c["state"] == "running" for c in containers_on(neighbour_network_after)
    )

    # Leave the neighbour to its own lifecycle: cancel, and its own close() cleans up.
    tpl.http("POST", f"{runs['neighbour']['base']}/stop", token=token)

    evidence = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W8",
        "claim": "a crashed run's boundary network and containers are reclaimed, while a live neighbour's are not",
        "drive": (
            "two live runs on the compose stack; the victim's docker-run client SIGKILLed "
            "inside the worker daemon (the product must settle the run and release the claim), "
            "then a launcher-shaped orphan built with the product's own ContainerBoundary and "
            "SIGKILLed mid-flight (network + relay + running container, no owner); sweep executed "
            "inside the daemon, the only holder of the Docker socket"
        ),
        "projectId": project["id"],
        "runs": {
            key: {"runId": entry["runId"], "network": entry["network"]}
            for key, entry in runs.items()
        },
        "victim": {
            "settledState": victim_state,
            "claimRowAfterSettle": victim_claim,
            "engineFinalResponse": (str(reason)[:300] if reason else None),
            "orphanNetworkSurvivedCrash": bool(orphan_network),
            "orphanContainersRunning": [
                c for c in orphan_containers if c["state"] == "running"
            ],
            "killedClients": killed,
        },
        "sweeps": {"heldRefusal": held_refusal, "real": report},
        "neighbour": {
            "stateAtSweep": neighbour_state_at_sweep,
            "networkBefore": neighbour_network_before,
            "containersBefore": neighbour_containers_before,
            "networkAfter": neighbour_network_after,
            "stillRunningAfterSweep": neighbour_still_up,
            "networkKeptBySweep": neighbour_before in kept,
            "stoppedBySweep": sorted(stopped_ids),
        },
        "networksAfterSweep": after,
    }

    checks = {
        "bothStarted": all(entry.get("startStatus") == 200 for entry in runs.values()),
        "crashObserved": str(killed).isdigit() and int(killed) >= 1,
        "orphanExisted": bool(orphan_network),
        "orphanContainerWasRunning": any(
            c["state"] == "running" for c in orphan_containers
        ),
        "victimSettled": victim_state in SETTLED,
        "claimReleased": victim_claim is None,
        "heldLiveRunRefused": runs["neighbour"]["network"] in held_refusal.get("kept", [])
        and not any(e["network"] == runs["neighbour"]["network"]
                     for e in held_refusal.get("removed", [])),
        "orphanNetworkRemoved": orphan_network in removed,
        "orphanContainersStopped": report.get("containersStopped", 0) >= 1,
        # "Untouched" is about the sweep, at the moment it ran: the neighbour still
        # had a boundary, the sweep put it in `kept`, and none of its containers
        # were stopped. A neighbour that finishes afterwards and closes its own
        # network is its own lifecycle, not the sweeper's doing.
        "neighbourUntouched": (
            bool(neighbour_before)
            and neighbour_before in kept
            and neighbour_before not in removed
            and not ({c["id"] for c in neighbour_containers_before} & stopped_ids)
        ),
        "victimNetworkGone": network_of(runs["victim"]["runId"]) is None,
    }
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w8-crash-cleanup-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "removed": removed,
                      "victimState": victim_state,
                      "neighbourAfter": neighbour_network_after,
                      "verdict": evidence["verdict"]}, indent=1))
    print(f"{evidence['verdict']}  evidence -> {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
