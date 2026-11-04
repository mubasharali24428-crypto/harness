#!/usr/bin/env python3
"""W8 gate (deterministic): a second overlapping result refuses, it does not overwrite.

The live concurrency drill proves runs cannot see each other. This one proves
the other half of the W8 claim — **no silent overwrite** — at the moment two
results meet: the apply step. Two runs start from the same bytes, both produce
output for the same path, and the CLI must land the first and refuse the
second with the digest it expected and the digest it found.

The apply is real code (`harness_sdk.cli.local.apply_back`), run by an
interpreter that can import it; the drill itself stays stdlib-only so the
evaluation runner can execute it. No model, no docker, no network.

Checks:
  * the first result applies, byte-for-byte;
  * the second is refused with ``expected`` = the base digest and ``actual`` =
    the first result's digest — and the file still holds the first result;
  * the refusal is all-or-nothing: a second path that *did* match is not written;
  * a re-apply of the first result is refused too (not a double write);
  * a new-file collision is refused (expected ``None``, file exists);
  * a stale deletion is refused and the file survives.

    python3 scripts/e2e/w8_overlap_drill.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
PROBE = REPO / "scripts" / "e2e" / "w8_overlap_probe.py"
CLIENTS = REPO / "clients" / "python"
CONTRACTS = REPO / "contracts" / "src"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def probe_python() -> str:
    """An interpreter that can import the CLI package under test."""
    env_path = os.environ.get("PYTHONPATH", "")
    path = os.pathsep.join(p for p in (str(CLIENTS), str(CONTRACTS), env_path) if p)
    candidates = [
        os.environ.get("HARNESS_LOCAL_PYTHON", ""),
        str(REPO / "section5" / ".venv" / "bin" / "python"),
        str(REPO / "section4" / ".venv" / "bin" / "python"),
        sys.executable,
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        check = subprocess.run(
            [candidate, "-c", "import harness_sdk.cli.local"],
            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": path},
            timeout=60, check=False,
        )
        if check.returncode == 0:
            return candidate
    raise SystemExit("no interpreter can import harness_sdk.cli.local")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()
    python = probe_python()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            p for p in (str(CLIENTS), str(CONTRACTS), os.environ.get("PYTHONPATH", "")) if p),
    }
    started = time.monotonic()
    proc = subprocess.run([python, str(PROBE)], capture_output=True, text=True,
                          env=env, timeout=args.timeout, check=False)
    duration = round(time.monotonic() - started, 2)
    try:
        observed = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        observed = {"fatal": "probe produced no JSON",
                    "stdout": (proc.stdout or "")[-1000:],
                    "stderr": (proc.stderr or "")[-1000:]}

    shared = observed.get("sharedFile") or {}
    created = observed.get("newFile") or {}
    deleted = observed.get("staleDeletion") or {}
    second = shared.get("second") or {}
    reapply = shared.get("reapply") or {}
    second_conflicts = second.get("conflicts") or []
    first_conflicts = (second_conflicts[0] if second_conflicts else {})

    checks = {
        "probeCompleted": "fatal" not in observed,
        "firstResultApplied": (shared.get("first") or {}).get("applied") == [
            "shared.txt", "keep.txt"],
        "firstBytesOnDisk": shared.get("finalShared") == "from a\n",
        "secondRefused": second.get("refused") is True,
        "conflictNamesThePath": first_conflicts.get("path") == "shared.txt",
        "conflictNamesExpectedBase": first_conflicts.get("expected") == sha("base\n"),
        "conflictNamesActualFirst": first_conflicts.get("actual") == sha("from a\n"),
        "allOrNothing": shared.get("finalKeep") == "keep a\n",
        "reapplyRefused": reapply.get("refused") is True,
        "newFileCollisionRefused": (created.get("second") or {}).get("refused") is True,
        "newFileKeptFirst": created.get("finalCreated") == "from a\n",
        "newFileNeighbourUnwritten": created.get("finalKeep") == "keep a\n",
        "staleDeletionRefused": (deleted.get("result") or {}).get("refused") is True,
        "staleDeletionKeptFile": deleted.get("stillThere") is True
        and deleted.get("text") == "edited since\n",
    }
    evidence = {
        "schema": 1,
        "kind": "w8-overlap-apply",
        "takenAt": datetime.now(UTC).isoformat(),
        "claim": "two results for one path cannot silently overwrite each other",
        "drive": "harness_sdk.cli.local.apply_back on real files; no model, no docker",
        "interpreter": python,
        "durationSeconds": duration,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [k for k, v in checks.items() if not v],
        "observations": observed,
        "probeStderr": (proc.stderr or "")[-1500:],
    }
    stamp = int(time.time())
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w8-overlap-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"],
                      "failed": evidence["failedChecks"],
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
