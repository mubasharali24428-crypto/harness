#!/usr/bin/env python3
"""W22: SIGKILL the worker daemon mid-run, bring it back, and check what is left.

Starts a run whose child sleeps, observes the child inside the confined worker,
then `docker kill`s harness-worker-daemon-1 and later starts it again. Snapshots
the claim, lifecycle, events and the run's containers before the kill, while the
daemon is dead, and after it is back.

Default mode: the orchestrator's reaper must stop the orphaned worker and relay
within REAP_WAIT_SECONDS of the daemon coming back, and the daemon must have
logged POST /runs/<id>/reap. With --orchestrator-restart the orchestrator is
restarted while the daemon is dead (its in-memory reaper is lost): the orphan
then lives on, the status must still say mutationPossible, and a user's POST
/stop must remove it. Both modes require every snapshot that sees a live
container on the run's network to report mutationPossible=true (statusTruthful).
The orphan cannot exit on its own inside the window (four 280 s children; the
bash tool caps one call at 300 s). The drill removes anything the product left
behind. The periodic sweep (grace + interval, ~15 min) was proven separately.
"""
import argparse
import json
import time
import uuid
from pathlib import Path

from w3_stop_spike import (
    IDENTITY,
    ORCHESTRATOR,
    http,
    live_pids_in,
    login,
    run_containers,
    sh,
)

DAEMON = "harness-worker-daemon-1"
DEAD_SECONDS = 60
ORCHESTRATOR_NAME = "harness-orchestrator-1"
REAP_WAIT_SECONDS = 30  # reaper polls /reap every 5 s once the daemon answers
BACK_SECONDS = 20
CHILD = "time.sleep(280)"
CHILD_RUNS = 4


def psql(sql):
    return sh("docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness",
              "-Atc", sql).stdout.strip()


def labelled_containers(run_id):
    """Running containers on the run's labelled network, egress proxy excluded.

    `run_containers` finds the relay only through its worker, so a relay that
    outlives its worker is invisible to it; the network label catches both.
    """
    names = []
    for net in sh("docker", "network", "ls", "--filter", f"label=harness-run={run_id}",
                  "--format", "{{.Name}}", check=False).stdout.split():
        names += [n for n in sh("docker", "ps", "--filter", f"network={net}", "--format",
                                "{{.Names}}", check=False).stdout.split()
                  if n != "harness-sandbox-egress-1"]
    return sorted(set(names))


def snapshot(run_id, token):
    _, status = http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}/status",
                     token=token)
    containers = run_containers(run_id)
    worker = containers.get("worker")
    return {
        "at": time.strftime("%H:%M:%S"),
        "daemon": sh("docker", "inspect", "-f", "{{.State.Status}}", DAEMON).stdout.strip(),
        "state": status.get("state") if isinstance(status, dict) else None,
        "lifecycle": status.get("lifecycle") if isinstance(status, dict) else None,
        "claim": psql(f"SELECT state FROM run_claims WHERE run_id='{run_id}'"),
        "events": psql("SELECT string_agg(type || coalesce(':' || (payload->>'reason'), ''), ',' "
                       f"ORDER BY seq) FROM agent_events WHERE run_id='{run_id}'"),
        "containers": containers,
        "onRunNetwork": labelled_containers(run_id),
        "childLive": bool(worker) and any(CHILD in p["cmd"] for p in live_pids_in(worker)),
        "networks": sh("docker", "network", "ls", "--filter", f"label=harness-run={run_id}",
                       "--format", "{{.Name}}").stdout.split(),
    }


def orphan_alive(run_id):
    return bool(run_containers(run_id) or labelled_containers(run_id))


def truthful(snap):
    """A live container on the run's network must never read as unable to mutate."""
    live = bool(snap["containers"] or snap["onRunNetwork"] or snap["childLive"])
    return not live or bool((snap["lifecycle"] or {}).get("mutationPossible"))


def wait_healthy(name, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if sh("docker", "inspect", "-f", "{{.State.Health.Status}}", name,
              check=False).stdout.strip() == "healthy":
            return True
        time.sleep(2)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--orchestrator-restart", action="store_true",
                        help="restart the orchestrator while the daemon is dead")
    restart = parser.parse_args().orchestrator_restart
    run_id = f"w22-dkill-{uuid.uuid4().hex[:12]}"
    record = {"runId": run_id, "mode": "orchestrator-restart" if restart else "reaper",
              "deadSeconds": DEAD_SECONDS, "verdict": "FAIL"}
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"{run_id}.json"
    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
    token = None
    try:
        record["daemonImage"] = sh("docker", "inspect", "-f", "{{.Config.Image}} {{.Image}}",
                                   DAEMON).stdout.strip()
        token = login(f"{run_id}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": run_id})
        assert status == 201
        goal = (f"Run exactly `python3 -c 'import time; {CHILD}'` using bash, {CHILD_RUNS} "
                "times one after another, each as its own bash call. Wait for each to "
                "finish; do nothing else.")
        status, plan = http("POST", f"{base}/plan", token=token,
                            body={"goal": goal, "projectId": project["id"]})
        assert status == 201
        assert http("POST", f"{base}/plan/approve", token=token,
                    body={"planId": plan["planId"]})[0] == 200
        assert http("POST", f"{base}/start", token=token)[0] == 200
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            worker = run_containers(run_id).get("worker")
            if worker and any(CHILD in p["cmd"] for p in live_pids_in(worker)):
                break
            time.sleep(1)
        else:
            raise RuntimeError("child was not observed")
        record["before"] = snapshot(run_id, token)
        record["killedAtUtc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record["killExit"] = sh("docker", "kill", DAEMON, check=False).returncode
        time.sleep(DEAD_SECONDS)
        snaps = [snapshot(run_id, token)]
        record["daemonDead"] = snaps[-1]
        if restart:
            record["orchestratorRestartExit"] = sh("docker", "restart", ORCHESTRATOR_NAME,
                                                   check=False).returncode
            record["orchestratorHealthy"] = wait_healthy(ORCHESTRATOR_NAME)
            snaps.append(snapshot(run_id, token))
            record["afterOrchestratorRestart"] = snaps[-1]
        if record["daemonDead"]["daemon"] != "running":
            record["startExit"] = sh("docker", "start", DAEMON, check=False).returncode
        started = time.monotonic()
        if restart:
            time.sleep(BACK_SECONDS)
            snaps.append(snapshot(run_id, token))
            record["daemonBack"] = snaps[-1]
            record["stop"] = http("POST", f"{base}/stop", token=token)
        deadline = time.monotonic() + REAP_WAIT_SECONDS
        while orphan_alive(run_id) and time.monotonic() < deadline:
            snaps.append(snapshot(run_id, token))
            time.sleep(1)
        record["orphanLivedAfterDaemonStartSeconds"] = round(time.monotonic() - started)
        snaps.append(snapshot(run_id, token))
        after = record["after"] = snaps[-1]
        record["snapshots"] = snaps
        logs = sh("docker", "logs", "--since", record["killedAtUtc"], DAEMON, check=False)
        verb = "/cancel" if restart else "/reap"
        record["daemonCalls"] = [line for line in (logs.stdout + logs.stderr).splitlines()
                                 if f"POST /runs/{run_id}{verb}" in line]
        checks = record["checks"] = {
            "daemonBack": after["daemon"] == "running",
            "runNotRunning": after["state"] not in (None, "running", "executing"),
            "claimReleased": after["claim"] == "",
            "noLiveWorkerOrRelay": not after["containers"] and not after["onRunNetwork"],
            "noLiveChild": not after["childLive"],
            "statusTruthful": all(truthful(snap) for snap in snaps),
            f"daemonLogged{verb.replace('/', ' ').title().strip()}": bool(record["daemonCalls"]),
        }
        if restart:
            checks["orchestratorHealthy"] = record["orchestratorHealthy"]
            checks["orphanLiveAfterRestart"] = record["daemonBack"]["childLive"]
            checks["stopAccepted"] = record["stop"][0] in (200, 202)
            checks["stopConfirmed"] = bool((after["lifecycle"] or {}).get("cancelConfirmed"))
        else:
            checks["childLiveWhileDaemonDead"] = record["daemonDead"]["childLive"]
        record["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    except Exception as exc:  # noqa: BLE001 — save failed drills
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if sh("docker", "inspect", "-f", "{{.State.Status}}", DAEMON,
              check=False).stdout.strip() != "running":
            record["finallyStartExit"] = sh("docker", "start", DAEMON, check=False).returncode
        left = sorted(set(run_containers(run_id).values()) | set(labelled_containers(run_id)))
        if left:
            record["drillRemoved"] = left
            sh("docker", "rm", "-f", *left, check=False)
        for net in sh("docker", "network", "ls", "--filter", f"label=harness-run={run_id}",
                      "--format", "{{.Name}}", check=False).stdout.split():
            record.setdefault("drillRemovedNetworks", []).append(net)
            attached = sh("docker", "network", "inspect", "-f",
                          "{{range .Containers}}{{.Name}} {{end}}", net, check=False).stdout.split()
            for name in attached:  # the shared egress proxy stays up; only its endpoint goes
                sh("docker", "network", "disconnect", "-f", net, name, check=False)
            sh("docker", "network", "rm", net, check=False)
        out.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"runId": run_id, "verdict": record["verdict"],
                          "checks": record.get("checks"), "evidence": str(out)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
