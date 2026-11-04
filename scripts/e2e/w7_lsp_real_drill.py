#!/usr/bin/env python3
"""W7 gate: one real language server works inside the sandbox, read-only.

W4 proved the client's *limits* with a fixture server. W7 proves the *use*: a
pinned python-lsp-server answers diagnostics, document symbols, definitions and
references for a workspace mounted **read-only** on a ``--network none``
container, as uid 10001, with the server's own ``/proc/<pid>/environ`` showing
no credential-shaped key.

Every check is an observation of that container:

* the image's baked client is byte-identical to this worktree (otherwise the
  result is not about the code under review);
* diagnostics arrive after ``didOpen`` for the planted undefined name and the
  unused import, and a clean file publishes an empty list;
* symbols, definition and references resolve to the planted locations;
* the server cannot write the workspace, cannot reach the network, and a 1 ms
  deadline kills it;
* the workspace tree is byte-identical before and after, and the container has
  no route.

    python3 scripts/e2e/w7_lsp_real_drill.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
IMAGE = "harness-engine-worker-lsp:vdev-local"
CLIENT_SOURCE = REPO / "section5" / "src" / "harness_sandbox" / "lsp_client.py"
PROBE = REPO / "scripts" / "e2e" / "w7_lsp_probe.py"

RETRY_PY = '''import os

RETRY_BACKOFF_SECONDS = 5


def compute_backoff(attempt):
    return RETRY_BACKOFF_SECONDS * attempt


def broken():
    return undefined_name
'''

JOBS_PY = '''from pkg.retry import compute_backoff

VALUE = compute_backoff(2)
'''

CLEAN_PY = '''VALUE = 1


def double(value):
    return value * 2
'''


def run(argv: list[str], *, timeout: float = 120, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          check=check)


def tree_hashes(root: Path) -> dict[str, str]:
    rows = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rows[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return rows


def build_fixture(base: Path) -> tuple[Path, Path]:
    work = base / "work"
    (work / "pkg").mkdir(parents=True)
    (work / "pkg" / "retry.py").write_text(RETRY_PY, encoding="utf-8")
    (work / "pkg" / "jobs.py").write_text(JOBS_PY, encoding="utf-8")
    (work / "pkg" / "clean.py").write_text(CLEAN_PY, encoding="utf-8")
    work.chmod(0o777)
    (work / "pkg").chmod(0o777)
    return work, PROBE


def image_file_hash(image: str, path: str) -> str | None:
    proc = run(["docker", "run", "--rm", "--entrypoint", "sha256sum", image, path],
               timeout=180)
    parts = (proc.stdout or "").strip().split()
    return parts[0] if parts else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()
    stamp = int(time.time())
    base = Path(tempfile.mkdtemp(prefix="w7-lsp-"))
    cid = ""
    try:
        work, probe = build_fixture(base)
        before = tree_hashes(work)
        host_client = hashlib.sha256(CLIENT_SOURCE.read_bytes()).hexdigest()
        deployed_client = image_file_hash(args.image, "/opt/harness-lsp/lsp_client.py")
        image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}",
                        args.image], timeout=30).stdout.strip() or None

        cid = run([
            "docker", "run", "-d", "--network", "none", "--name", f"w7-lsp-{stamp}",
            "--user", "10001:10001",
            "-e", "DEEPSEEK_API_KEY=w7-drill-should-not-leak",
            "-e", "HARNESS_WORKER_TOKEN=w7-drill-should-not-leak",
            "-v", f"{work}:/work:ro",
            "-v", f"{probe}:/probe/probe.py:ro",
            "--entrypoint", "sleep", args.image, "900",
        ], timeout=120).stdout.strip()
        if not cid:
            raise SystemExit("could not start the drill container")

        routes = run(["docker", "exec", cid, "sh", "-lc",
                      "ip route 2>/dev/null || true"], timeout=30)
        probe_proc = run(["docker", "exec", cid, "python3", "-I", "/probe/probe.py"],
                         timeout=args.timeout)
        try:
            observed = json.loads((probe_proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            observed = {"fatal": "probe produced no JSON",
                        "stdout": (probe_proc.stdout or "")[-1000:],
                        "stderr": (probe_proc.stderr or "")[-1000:]}

        after = tree_hashes(work)
        diagnostics = observed.get("diagnostics") or []
        symbols = {item.get("name") for item in (observed.get("symbols") or [])
                   if isinstance(item, dict)}
        definition = observed.get("definition") or []
        definition = definition if isinstance(definition, list) else [definition]
        references = observed.get("references") or []
        references = references if isinstance(references, list) else [references]
        env_keys = set(observed.get("serverEnvironmentKeys") or [])
        caps = set(observed.get("capabilities") or [])
        write = observed.get("workspaceWrite") or {}
        network = observed.get("network") or {}
        deadline = observed.get("deadline") or {}

        def pairs(locations: list) -> set[tuple[str, int]]:
            out = set()
            for item in locations:
                if not isinstance(item, dict):
                    continue
                uri = str(item.get("uri") or "")
                line = ((item.get("range") or {}).get("start") or {}).get("line")
                out.add((Path(uri).name, line))
            return out

        checks = {
            "imageClientMatchesWorktree": bool(deployed_client and deployed_client == host_client),
            "pylspPinned": (observed.get("pylspVersion") == "1.12.2"
                            and observed.get("pyflakesVersion") == "3.2.0"),
            "capabilitiesAdvertised": {
                "definitionProvider", "referencesProvider", "documentSymbolProvider",
            } <= caps,
            "diagnosticsReported": any(
                d.get("severity") == 1
                and (d.get("range") or {}).get("start", {}).get("line") == 10
                and "undefined_name" in str(d.get("message"))
                for d in diagnostics),
            "unusedImportWarned": any(
                d.get("severity") == 2 and "os" in str(d.get("message"))
                for d in diagnostics),
            "cleanFilePublishesNothing": observed.get("cleanDiagnostics") == [],
            "symbolsListDefinitions": {
                "RETRY_BACKOFF_SECONDS", "compute_backoff", "broken",
            } <= symbols,
            "definitionResolvesToDeclaration": bool(definition)
                and pairs(definition) == {("retry.py", 5)},
            "referencesSpanFiles": {("jobs.py", 2), ("retry.py", 5)} <= pairs(references),
            "serverEnvironmentScrubbed": bool(env_keys)
                and not [k for k in env_keys
                         if any(word in k.upper() for word in
                                ("DEEPSEEK", "HARNESS", "TOKEN", "SECRET", "PASSWORD"))]
                and str(observed.get("serverHome") or "").startswith("/tmp/")
                and str(observed.get("serverTmpdir") or "").startswith("/tmp/"),
            "workspaceReadOnly": (write.get("wrote") is False
                                  and "Read-only" in str(write.get("error"))
                                  and not (work / "escape.txt").exists()),
            "networkBlocked": network.get("connected") is False,
            "deadlineKillsRealServer": (deadline.get("timedOut") is True
                                        and observed.get("diedAfterDeadline") is True
                                        and float(observed.get("deadlineSeconds") or 99) < 10),
            "workspaceTreeUnchanged": before == after,
            "containerHasNoRoute": not (routes.stdout or "").strip(),
            "probeCompleted": "fatal" not in observed,
        }
        evidence = {
            "schema": 1,
            "kind": "w7-lsp-real-server",
            "takenAt": datetime.now(UTC).isoformat(),
            "image": args.image,
            "imageId": image_id,
            "clientSha256": {"worktree": host_client, "image": deployed_client},
            "network": "none",
            "user": "10001:10001",
            "workspaceMount": "read-only",
            "server": {"name": "pylsp",
                       "version": observed.get("pylspVersion"),
                       "pyflakes": observed.get("pyflakesVersion")},
            "checks": checks,
            "verdict": "PASS" if all(checks.values()) else "FAIL",
            "failedChecks": [k for k, v in checks.items() if not v],
            "observations": {
                "routes": (routes.stdout or "").strip(),
                "capabilities": sorted(caps),
                "diagnostics": diagnostics,
                "cleanDiagnostics": observed.get("cleanDiagnostics"),
                "symbols": sorted(symbols),
                "definition": definition,
                "references": references,
                "serverEnvironmentKeys": sorted(env_keys),
                "serverHome": observed.get("serverHome"),
                "serverTmpdir": observed.get("serverTmpdir"),
                "workspaceWrite": write,
                "network": network,
                "deadline": deadline,
                "deadlineSeconds": observed.get("deadlineSeconds"),
                "timings": {key: observed.get(key) for key in (
                    "initializeSeconds", "diagnosticsSeconds", "symbolsSeconds",
                    "definitionSeconds", "referencesSeconds")},
                "probeStderr": (probe_proc.stderr or "")[-1500:],
                "fatal": observed.get("fatal"),
            },
        }
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"w7-lsp-real-{stamp}.json"
        path.write_text(json.dumps(evidence, indent=2, default=str) + "\n",
                        encoding="utf-8")
        print(json.dumps({"verdict": evidence["verdict"],
                          "failed": evidence["failedChecks"],
                          "evidence": str(path)}, indent=2))
        return 0 if evidence["verdict"] == "PASS" else 1
    finally:
        if cid:
            run(["docker", "rm", "-f", cid], timeout=60)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
