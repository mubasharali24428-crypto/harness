#!/usr/bin/env python3
"""W22: cut the model response partway through, then check the run and a fresh run.

The run's model traffic goes through its per-run relay. The drill asks for one
long write, waits until the relay has started sending the response back to the
worker (bytes on the run-network interface still growing), then `docker kill`s
the relay: the worker holds a response cut off mid-stream and cannot reach the
provider again.

Pass: the run settles as blocked with an engine error, the half-streamed file
was never written, no worker or relay of the run is still running, and a
fresh run afterwards completes its task (recovery). A true upstream provider
5xx outage cannot be produced on demand and is not claimed here.
"""
import json
import time
import uuid
from pathlib import Path

from w3_stop_spike import IDENTITY, ORCHESTRATOR, http, login, run_containers, sh

WORKSPACE_ROOT = "/var/lib/harness-runs/workspace"
LINES = 400
CUT_AFTER_BYTES = 4000
SETTLED = {"blocked", "done", "awaiting_done_signal", "failed"}


def start(run_id, token, goal):
    status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                           body={"name": run_id})
    assert status == 201
    base = f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}"
    status, plan = http("POST", f"{base}/plan", token=token,
                        body={"goal": goal, "projectId": project["id"]})
    assert status == 201
    assert http("POST", f"{base}/plan/approve", token=token,
                body={"planId": plan["planId"]})[0] == 200
    assert http("POST", f"{base}/start", token=token)[0] == 200
    return base


def wait_settled(base, token, seconds):
    deadline = time.monotonic() + seconds
    state = None
    while time.monotonic() < deadline:
        _, body = http("GET", f"{base}/status", token=token)
        state = body.get("state") if isinstance(body, dict) else None
        if state in SETTLED:
            break
        time.sleep(2)
    return state


def file_lines(run_id, name):
    code = (f"import os; p = {WORKSPACE_ROOT!r} + '/' + {run_id!r} + '/' + {name!r}\n"
            "print(len(open(p).read().splitlines()) if os.path.exists(p) else -1)")
    out = sh("docker", "exec", "harness-orchestrator-1", "python3", "-c", code,
             check=False).stdout.strip()
    return int(out) if out.lstrip("-").isdigit() else None


def run_net_tx(relay, mac):
    """Bytes the relay has sent on the interface whose MAC is on the run network."""
    code = ("import os\n"
            "for i in os.listdir('/sys/class/net'):\n"
            "    if os.path.isdir(f'/sys/class/net/{i}') and open(f'/sys/class/net/{i}/address').read().strip() == " + repr(mac) + ":\n"
            "        print(open(f'/sys/class/net/{i}/statistics/tx_bytes').read().strip())")
    out = sh("docker", "exec", relay, "python3", "-c", code, check=False).stdout.strip()
    return int(out) if out.isdigit() else None


def main():
    tag = uuid.uuid4().hex[:12]
    run_id = f"w22-cut-{tag}"
    record = {"runId": run_id, "verdict": "FAIL", "lines": LINES}
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"{run_id}.json"
    token = None
    base = None
    try:
        token = login(f"{run_id}@example.com")
        goal = (f"Create a file named big.txt containing exactly {LINES} lines, "
                "`line 1` through `line " + str(LINES) + "`, written with ONE call of your "
                "write tool holding the whole content. Do not use bash.")
        base = start(run_id, token, goal)
        deadline = time.monotonic() + 60
        relay = None
        while time.monotonic() < deadline and not relay:
            relay = run_containers(run_id).get("relay")
            time.sleep(0.2)
        assert relay, "relay absent"
        record["relay"] = relay
        nets = json.loads(sh("docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}",
                             relay).stdout)
        run_net = next(n for n in nets if n.endswith("-net"))
        mac = nets[run_net]["MacAddress"]
        samples = []
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            tx = run_net_tx(relay, mac)
            samples.append(tx)
            if tx and tx > CUT_AFTER_BYTES and len(samples) > 1 and samples[-2] \
                    and samples[-2] > 0 and tx > samples[-2]:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("response never started streaming")
        record["txAtCut"] = samples[-1]
        record["txGrowingAtCut"] = [samples[-2], samples[-1]]
        record["killExit"] = sh("docker", "kill", relay, check=False).returncode
        record["cutAt"] = time.strftime("%H:%M:%S")
        state = record["state"] = wait_settled(base, token, 240)
        _, engine = http("GET", f"{base}/engine", token=token)
        record["engineStatus"] = engine.get("status") if isinstance(engine, dict) else None
        record["events"] = sh(
            "docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness", "-Atc",
            "SELECT string_agg(type || coalesce(':' || (payload->>'reason'), ''), ',' "
            f"ORDER BY seq) FROM agent_events WHERE run_id='{run_id}'").stdout.strip()
        record["bigTxtLines"] = file_lines(run_id, "big.txt")
        # W17 asks for outage AND recovery *without a downgrade*: a provider failure
        # must fail the run, never re-route it to another model behind the user's back.
        record["routedModels"] = sh(
            "docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness", "-Atc",
            "SELECT DISTINCT coalesce(payload->>'model', '') FROM agent_events "
            f"WHERE run_id='{run_id}' AND type='model_routed'").stdout.split()
        time.sleep(10)
        record["leftContainers"] = run_containers(run_id)

        # Recovery: a fresh run on the same stack completes its task.
        rec_id = f"w22-cut-rec-{tag}"
        rec_base = start(rec_id, token, "Create a file named ok.txt containing the text hello.")
        record["recovery"] = {"runId": rec_id, "state": wait_settled(rec_base, token, 240),
                              "okTxtLines": file_lines(rec_id, "ok.txt")}
        checks = record["checks"] = {
            "cutMidStream": record["txGrowingAtCut"][1] > record["txGrowingAtCut"][0],
            "runBlocked": state == "blocked",
            "engineError": record["engineStatus"] == "error",
            "noPartialFile": record["bigTxtLines"] == -1,
            "noModelDowngrade": len(set(record["routedModels"])) <= 1,
            "noLiveWorkerOrRelay": not record["leftContainers"],
            "recoveryCompleted": record["recovery"]["state"] in {"done", "awaiting_done_signal"}
            and record["recovery"]["okTxtLines"] == 1,
        }
        record["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    except Exception as exc:  # noqa: BLE001 — save failed drills
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        if token and base and record.get("state") not in SETTLED:
            record["cleanupStop"] = http("POST", f"{base}/stop", token=token)[0]
        out.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"runId": run_id, "verdict": record["verdict"],
                          "checks": record.get("checks"), "evidence": str(out)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
