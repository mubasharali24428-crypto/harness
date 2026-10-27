#!/usr/bin/env python3
"""W4 live drill: is the egress sidecar actually the sandbox network's only way out?

Two layers, because "deployed" said nothing:

1. **Listener + policy** — exec inside ``sandbox-egress`` and send CONNECT lines
   to 127.0.0.1:3128. Allow-listed docs/endpoints must answer 200; a host
   outside the list, a non-443 port and a non-CONNECT method must answer 403.
2. **Network path** — start a throwaway container on the ``harness_sandbox_egress``
   internal network (no gateway) and connect to ``sandbox-egress:3128``. A
   denial there proves the proxy is reachable *as a proxy* from a sandbox-like
   peer; an allow proves the same hop can also tunnel out.

The container keeps running the image it was built from, so re-run this after a
rebuild. One evidence file per run.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
NETWORK = "harness_sandbox_egress"
PROBE_IMAGE = "python:3.12.12-slim-bookworm"

# (name, request bytes, expected status token)
LOCAL_CASES = [
    ("allowed_endpoint", b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n", b" 200 "),
    ("allowed_git", b"CONNECT github.com:443 HTTP/1.1\r\n\r\n", b" 200 "),
    # Docs are a *web* purpose: the sandbox proxy must refuse them even though
    # web_fetch may read them. The two purposes are deliberately not the same.
    ("denied_docs", b"CONNECT api-docs.deepseek.com:443 HTTP/1.1\r\n\r\n", b" 403 "),
    ("denied_host", b"CONNECT example.com:443 HTTP/1.1\r\n\r\n", b" 403 "),
    ("denied_port", b"CONNECT api.anthropic.com:8443 HTTP/1.1\r\n\r\n", b" 403 "),
    ("denied_method", b"GET http://api.anthropic.com/ HTTP/1.1\r\n\r\n", b" 405 "),
]

PROBE = r"""
import json, socket, sys
cases = json.loads(sys.argv[1])
out = {}
for name, raw, token in cases:
    try:
        s = socket.create_connection(("sandbox-egress", 3128), 5)
        s.sendall(raw.encode())
        reply = s.recv(64)
        s.close()
        out[name] = {"reply": reply.decode("latin1").strip(), "ok": token.encode() in reply}
    except Exception as exc:
        out[name] = {"error": repr(exc), "ok": False}
print(json.dumps(out))
"""


def container_id(service: str) -> str:
    proc = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.service={service}"],
        capture_output=True, text=True, check=True,
    )
    cid = proc.stdout.strip().splitlines()
    if not cid:
        raise SystemExit(f"service {service!r} is not running")
    return cid[0]


def local_cases(cid: str) -> dict:
    script = (
        "import socket,sys,json\n"
        "cases=json.loads(sys.argv[1])\n"
        "out={}\n"
        "for name,raw,token in cases:\n"
        "    try:\n"
        "        s=socket.create_connection(('127.0.0.1',3128),3); s.sendall(raw.encode())\n"
        "        r=s.recv(64); s.close(); out[name]={'reply':r.decode('latin1').strip(),"
        "'ok':token.encode() in r}\n"
        "    except Exception as exc: out[name]={'error':repr(exc),'ok':False}\n"
        "print(json.dumps(out))\n"
    )
    payload = json.dumps([[n, r.decode(), t.decode()] for n, r, t in LOCAL_CASES])
    proc = subprocess.run(
        ["docker", "exec", cid, "python", "-c", script, payload],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"error": f"probe produced no JSON (exit {proc.returncode}): {proc.stderr[-400:]}"}


def network_cases() -> dict:
    payload = json.dumps([[n, r.decode(), t.decode()] for n, r, t in LOCAL_CASES])
    proc = subprocess.run(
        ["docker", "run", "--rm", "--network", NETWORK, PROBE_IMAGE,
         "python", "-c", PROBE, payload],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"error": f"probe produced no JSON (exit {proc.returncode}): {proc.stderr[-400:]}"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cid = container_id("sandbox-egress")
    inside = local_cases(cid)
    from_network = network_cases()
    evidence = {
        "drill": "w4-egress-network",
        "takenAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "container": cid[:12],
        "network": NETWORK,
        "insideTheProxy": inside,
        "fromTheSandboxNetwork": from_network,
    }
    ok_inside = all(v.get("ok") for v in inside.values()) if "error" not in inside else False
    ok_network = (
        all(v.get("ok") for v in from_network.values()) if "error" not in from_network else False
    )
    evidence["verdict"] = "pass" if ok_inside and ok_network else "fail"
    path = OUT / f"w4-egress-network-{int(time.time())}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    print(f"\nwrote {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
