"""G1 negative drill (p04 review §7.3): Landlock-confined child with
allow_network=False attempts an outbound TLS connection. Gap evidence, not a
pass/fail gate: ABI v1 Landlock has no network hooks (confinement.py:446),
so the connection is expected to succeed today."""
import socket, sys
from pathlib import Path

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "conf", "/s/harness_sandbox/confinement.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
confine_argv = _mod.confine_argv
landlock_supported = _mod.landlock_supported

assert landlock_supported(), "no Landlock on this kernel"
ws = Path("/w/workspace"); ws.mkdir(parents=True, exist_ok=True)
tmp = Path("/w/tmp"); tmp.mkdir(parents=True, exist_ok=True)

argv = ["python3", "-c",
        "import socket; s=socket.create_connection(('example.com',443),5); "
        "print('CONNECTED to example.com:443'); s.close()"]
wrapped = confine_argv(argv, workspace=ws, tmpdir=tmp, allow_network=False)
print("wrapped argv head:", wrapped[0], wrapped[1], wrapped[2][:40], "...")

import subprocess
r = subprocess.run(wrapped, capture_output=True, text=True, timeout=30)
print("exit:", r.returncode)
print("stdout:", r.stdout.strip())
print("stderr tail:", (r.stderr.strip() or "(empty)")[-200:])
print("G1_VERDICT:", "CONNECTED Gap-confirmed" if "CONNECTED" in r.stdout
      else ("BLOCKED network-hook-present" if r.returncode != 0 else "NO-CONNECT"))
