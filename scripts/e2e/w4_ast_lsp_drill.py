#!/usr/bin/env python3
"""W4 gate: a read-only AST search and a spawned language server stay inside policy.

Runs both spike tools in the real worker image on a `--network none` container,
as an unprivileged user, with the workspace mounted **read-only**. Every check is
an observation of that container, not of a claim in a doc:

* AST search finds a planted definition and never reads a symlink that points
  out of the workspace;
* the LSP client spawns a server whose environment carries no credential-shaped
  key, whose cwd is the workspace, whose outbound connect fails (no network),
  and whose write to the workspace fails (read-only mount) — and no file appears;
* a server that never answers is killed at the deadline (the CLI returns within
  the cap with a timeout error);
* the source tree is byte-identical before and after every call (the tools do
  not write), and the container has no route table.

    python3 scripts/e2e/w4_ast_lsp_drill.py
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shlex
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
WORKER_IMAGE = "harness-engine-worker:vdev-local"
TOOL_FILES = [
    REPO / "section5" / "src" / "harness_sandbox" / "ast_search.py",
    REPO / "section5" / "src" / "harness_sandbox" / "lsp_client.py",
    REPO / "section5" / "tests" / "fixtures" / "lsp_fixture_server.py",
]

spec = importlib.util.spec_from_file_location(
    "tpl", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py")
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

RETRY_PY = '''"""Retry policy."""

RETRY_BACKOFF_SECONDS = 5


class RetryPolicy:
    def compute_backoff(self, attempt):
        return RETRY_BACKOFF_SECONDS * attempt


def flush_backoff_queue():
    pass
'''


def build_fixture(base: Path) -> tuple[Path, Path]:
    work = base / "work"
    tools = base / "tools"
    (work / "pkg").mkdir(parents=True)
    tools.mkdir()
    (work / "pkg" / "retry.py").write_text(RETRY_PY, encoding="utf-8")
    (work / "pkg" / "notes.txt").write_text("compute_backoff\n", encoding="utf-8")
    (base / "outside.py").write_text("def compute_backoff_secret():\n    pass\n",
                                     encoding="utf-8")
    (work / "pkg" / "escape.py").symlink_to(base / "outside.py")
    for path in TOOL_FILES:
        shutil.copy2(path, tools / path.name)
    work.chmod(0o777)
    tools.chmod(0o755)
    return work, tools


def tree_hashes(root: Path) -> dict[str, str]:
    rows = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rows[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return rows


def exec_json(cid: str, script: str, *args: str, timeout: int = 60) -> dict:
    command = " ".join(shlex.quote(part) for part in (script, *args))
    proc = tpl.sh("docker", "exec", cid, "sh", "-lc", command, check=False)
    text = (proc.stdout or "").strip()
    try:
        return {"exitCode": proc.returncode, **json.loads(text)}
    except ValueError:
        return {"exitCode": proc.returncode,
                "stdout": text[-500:], "stderr": (proc.stderr or "")[-500:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=WORKER_IMAGE)
    args = ap.parse_args()
    stamp = int(time.time())
    base = Path(tempfile.mkdtemp(prefix="w4-astlsp-"))
    cid = ""
    try:
        work, tools = build_fixture(base)
        before = tree_hashes(work)
        cid = tpl.sh(
            "docker", "run", "-d", "--network", "none", "--name",
            f"w4-astlsp-{stamp}", "--user", "10001:10001",
            "-v", f"{work}:/work:ro", "-v", f"{tools}:/tools:ro",
            "--entrypoint", "sleep", args.image, "600",
        ).stdout.strip()
        if not cid:
            raise SystemExit("could not start the drill container")

        routes = tpl.sh("docker", "exec", cid, "sh", "-lc",
                        "ip route 2>/dev/null || true", check=False)
        ast = exec_json(cid, "python3", "-I", "/tools/ast_search.py",
                        "--root", "/work", "--query", "backoff")
        ast_secret = exec_json(cid, "python3", "-I", "/tools/ast_search.py",
                               "--root", "/work", "--query", "compute_backoff_secret")
        definition = exec_json(
            cid, "python3", "-I", "/tools/lsp_client.py", "--cwd", "/work",
            "--command", json.dumps(["python3", "-I", "/tools/lsp_fixture_server.py"]),
            "--method", "textDocument/definition",
            "--params", json.dumps({
                "textDocument": {"uri": "file:///work/pkg/retry.py"},
                "position": {"line": 0, "character": 0},
            }))
        env = exec_json(
            cid, "python3", "-I", "/tools/lsp_client.py", "--cwd", "/work",
            "--command", json.dumps(["python3", "-I", "/tools/lsp_fixture_server.py"]),
            "--method", "test/env")
        cwd = exec_json(
            cid, "python3", "-I", "/tools/lsp_client.py", "--cwd", "/work",
            "--command", json.dumps(["python3", "-I", "/tools/lsp_fixture_server.py"]),
            "--method", "test/cwd")
        write = exec_json(
            cid, "python3", "-I", "/tools/lsp_client.py", "--cwd", "/work",
            "--command", json.dumps(["python3", "-I", "/tools/lsp_fixture_server.py"]),
            "--method", "test/write")
        network = exec_json(
            cid, "python3", "-I", "/tools/lsp_client.py", "--cwd", "/work",
            "--command", json.dumps(["python3", "-I", "/tools/lsp_fixture_server.py"]),
            "--method", "test/network")
        slow_started = time.monotonic()
        slow = exec_json(
            cid, "python3", "-I", "/tools/lsp_client.py", "--cwd", "/work",
            "--command", json.dumps(["python3", "-I", "/tools/lsp_fixture_server.py"]),
            "--method", "test/slow", "--timeout", "1.0")
        slow_seconds = round(time.monotonic() - slow_started, 2)

        after = tree_hashes(work)
        names = {r["qualname"] for r in ast.get("results") or []}
        env_keys = set(((env.get("result") or {}).get("keys")) or [])
        checks = {
            "astFindsDefinitions": (ast.get("exitCode") == 0 and names == {
                "RETRY_BACKOFF_SECONDS", "RetryPolicy.compute_backoff",
                "flush_backoff_queue"}),
            "astRefusesSymlinkEscape": (ast_secret.get("exitCode") == 0
                                        and ast_secret.get("results") == []
                                        and "pkg/escape.py" in (ast_secret.get("skipped") or [])),
            "astReadOnly": before == after,
            "lspDefinition": ((definition.get("result") or {}).get("uri")
                              == "file:///work/pkg/retry.py"),
            "lspEnvScrubbed": (bool(env_keys)
                               and not [k for k in env_keys
                                        if "DEEPSEEK" in k or "HARNESS" in k]),
            "lspCwdConfined": ((cwd.get("result") or {}).get("cwd") == "/work"),
            "lspCannotWriteWorkspace": ((write.get("result") or {}).get("wrote") is False
                                        and not (work / "escape.txt").exists()),
            "lspCannotReachNetwork": ((network.get("result") or {}).get("connected")
                                      is False),
            "lspDeadlineKillsServer": (slow.get("exitCode") == 2
                                       and (slow.get("error") or {}).get("code")
                                       == "LspTimeout"
                                       and slow_seconds < 10),
            "containerHasNoRoute": not (routes.stdout or "").strip(),
        }
        evidence = {
            "schema": 1,
            "kind": "w4-ast-lsp-confinement",
            "takenAt": datetime.now(UTC).isoformat(),
            "image": args.image,
            "network": "none",
            "user": "10001:10001",
            "workspaceMount": "read-only",
            "checks": checks,
            "verdict": "PASS" if all(checks.values()) else "FAIL",
            "failedChecks": [k for k, v in checks.items() if not v],
            "observations": {
                "routes": (routes.stdout or "").strip(),
                "ast": {k: ast.get(k) for k in
                        ("exitCode", "scannedFiles", "skipped", "truncated", "elapsedSeconds")},
                "astResults": sorted(names),
                "lspDefinition": definition.get("result"),
                "lspEnvKeyCount": len(env_keys),
                "lspCwd": (cwd.get("result") or {}).get("cwd"),
                "lspWrite": write.get("result"),
                "lspNetwork": network.get("result"),
                "slowExit": slow.get("exitCode"),
                "slowError": slow.get("error"),
                "slowSeconds": slow_seconds,
            },
        }
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"w4-ast-lsp-{stamp}.json"
        path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"verdict": evidence["verdict"],
                          "failed": evidence["failedChecks"],
                          "evidence": str(path)}, indent=2))
        return 0 if evidence["verdict"] == "PASS" else 1
    finally:
        if cid:
            tpl.sh("docker", "rm", "-f", cid, check=False)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
