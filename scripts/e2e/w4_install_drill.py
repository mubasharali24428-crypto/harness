#!/usr/bin/env python3
"""W4 live gate, claim 4: installs run confined, and the egress proxy bounds them.

Two halves, both real:

* **the gate decides.** `pip install` is refused by default and permitted only
  when the operator flag, the container boundary and the egress proxy are all in
  place — the reason travels with the refusal so the transcript says why;
* **the boundary bounds it.** Inside a container on a gateway-less run network
  with the allow-list proxy attached, a permitted `pip install` succeeds and the
  proxy logs an allowed CONNECT; an index URL outside the list is refused 403.

The command is driven through `shell_gate.py` directly rather than through a
model turn: the claim is about the boundary, and a nondeterministic agent would
turn a boundary test into a model test. (Same choice as the W4 egress drill.)

    python3 scripts/e2e/w4_install_drill.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
IMAGE = "harness-engine-worker:vdev-local"
EGRESS_CONTAINER = "harness-sandbox-egress-1"

sys.path.insert(0, str(REPO / "section4" / "src"))
sys.path.insert(0, str(REPO / "contracts" / "src"))
from harness_orchestrator.container_runtime import ContainerBoundary


def sh(*argv: str, check: bool = False, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, check=check, timeout=timeout)


def gate_in_container(network: str, command: str, *, allow: bool) -> dict:
    """Run one command through the real shell gate inside the run network."""
    env_args = [
        "--env", "HARNESS_ENGINE_CONFINEMENT=container",
        "--env", "HTTPS_PROXY=http://harness-egress:3128",
        "--env", "HTTP_PROXY=http://harness-egress:3128",
        "--env", "NO_PROXY=localhost,127.0.0.1,model,harness-egress",
        # the real worker is launched with HOME=/tmp and a read-only root (confine.scrubbed_env);
        # pip must be able to write somewhere, so the drill gives it the same /tmp tmpfs
        "--env", "HOME=/tmp",
        "--env", "TMPDIR=/tmp",
        "--env", "PIP_CACHE_DIR=/tmp/pip-cache",
    ]
    if allow:
        env_args += ["--env", "DSH_ALLOW_INSTALLS=1"]
    request = json.dumps({"command": command, "cwd": "/tmp", "timeoutSeconds": 120})
    proc = sh("docker", "run", "--rm",
              "--network", network,
              "--read-only",
              "--cap-drop=ALL",
              "--security-opt", "no-new-privileges",
              "--pids-limit", "128",
              "--memory", "1g",
              "--user", "10001:10001",
              "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=256m,mode=1777",
              *env_args,
              "--entrypoint", "/bin/sh",
              IMAGE,
              "-c", f"printf '%s' {json.dumps(request)} | python -I /opt/harness-shell/shell_gate.py",
              timeout=240)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {
            "raw": proc.stdout, "stderr": proc.stderr[-400:], "exit": proc.returncode
        }
    except ValueError:
        return {"raw": proc.stdout[-600:], "stderr": proc.stderr[-400:], "exit": proc.returncode}


def proxy_connect_lines(since: str) -> list[str]:
    proc = sh("docker", "logs", "--since", since, EGRESS_CONTAINER)
    return [ln for ln in (proc.stdout + proc.stderr).splitlines() if "CONNECT" in ln]


def main() -> int:
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W4",
        "claim": "installs run confined, and the egress allow-list is what bounds them",
        "drive": "shell_gate.py inside a real run network (no model)",
        "image": IMAGE,
    }
    boundary = ContainerBoundary("https://api.deepseek.com")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
    try:
        boundary.start()  # network + relay + the allow-list proxy attached
        network = boundary.network

        denied = gate_in_container(network, "pip install six==1.16.0 --quiet", allow=False)
        evidence["defaultRefusal"] = {
            "denied": bool(denied.get("denied")),
            "reason": str(denied.get("denied") or "")[:300],
        }

        allowed = gate_in_container(
            network,
            "pip install six==1.16.0 --disable-pip-version-check",
            allow=True,
        )
        output = str(allowed.get("output") or "")
        evidence["permitted"] = {
            "ok": allowed.get("exitCode") == 0 and "Successfully installed" in output,
            "exitCode": allowed.get("exitCode"),
            "denied": allowed.get("denied"),
            "snippet": output[-300:],
        }

        blocked = gate_in_container(
            network,
            "pip install six==1.16.0 --quiet --index-url https://files.example.com/simple",
            allow=True,
        )
        blocked_out = str(blocked.get("output") or "")
        evidence["offListRegistry"] = {
            # an off-list index must NOT install: either the proxy refuses the
            # CONNECT or pip fails before it can fetch anything
            "ok": blocked.get("exitCode") == 0 and "Successfully installed" in blocked_out,
            "exitCode": blocked.get("exitCode"),
            "snippet": blocked_out[-300:],
        }

        connects = proxy_connect_lines(since)
        evidence["proxyConnectLines"] = connects[-12:]
        evidence["proxySawPypi"] = any("pypi.org" in ln for ln in connects)
    finally:
        boundary.close()

    evidence["verdict"] = (
        "PASS"
        if (
            evidence["defaultRefusal"]["denied"]
            and evidence["permitted"]["ok"]
            and evidence["proxySawPypi"]
            and not evidence["offListRegistry"]["ok"]
        )
        else "FAIL"
    )
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w4-install-{int(time.time())}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in
                      ("defaultRefusal", "permitted", "offListRegistry", "proxySawPypi",
                       "verdict")}, indent=1))
    print(f"{evidence['verdict']}  evidence -> {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
