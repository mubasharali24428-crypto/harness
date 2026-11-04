#!/usr/bin/env python3
"""W5 gate: the CLI's diagnostics and capture contract hold, and parity is re-run.

Deterministic half (no model) against the live stack:

* no token → exit 3, one JSON object with ``NOT_LOGGED_IN``;
* an unparsable command line → exit 2 with a ``USAGE`` object;
* an unknown run → the server's 404 code with exit 1;
* ``--capture`` writes exactly the object printed to stdout, owner-only;
* the stored token is 0600 in a 0700 directory, and it appears in no output;
* a real run (created through the same API the web uses) is readable with exit 0.

The live ``cli_parity`` drill still proves CLI/web behavior parity; this one
proves the machine contract that an evaluation can branch on.

    python3 scripts/e2e/w5_cli_diagnostics.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
CLIENT_PY = REPO / "clients" / "python" / ".venv" / "bin" / "python"
IDENTITY = "http://127.0.0.1:8001"
ORCHESTRATOR = "http://127.0.0.1:8004"

spec = importlib.util.spec_from_file_location(
    "tpl", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py")
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)


def cli(args: list[str], *, config_dir: Path, token: str | None = None,
        timeout: float = 60.0) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["HARNESS_CONFIG_DIR"] = str(config_dir)
    env.pop("HARNESS_TOKEN", None)
    if token is not None:
        env["HARNESS_TOKEN"] = token
    proc = subprocess.run(
        # Service URLs are subcommand flags (argparse parents), not global flags.
        [str(CLIENT_PY), "-m", "harness_sdk.cli.main", args[0],
         "--identity-url", IDENTITY, "--orchestrator-url", ORCHESTRATOR, *args[1:]],
        capture_output=True, text=True, env=env, timeout=timeout, check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def one_object(text: str) -> dict:
    """The whole stream must be one object, not JSONL with trailing prose."""
    return json.loads(text)


def main() -> int:
    stamp = int(time.time())
    token = tpl.login(f"w5-cli-{stamp}@example.com")
    base = Path(tempfile.mkdtemp(prefix="w5-cli-"))
    cfg = base / "cfg"
    capture = base / "status.json"
    failures: list[str] = []
    observations: dict = {}

    def check(name: str, ok: bool, detail: object = None) -> None:
        observations[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            failures.append(name)

    # 1. no token → AUTH (3), one object
    code, out, err = cli(["run", "x", "--yes", "--json"], config_dir=cfg)
    body = one_object(out) if out.strip() else {}
    check("notLoggedInExit3", code == 3
          and (body.get("error") or {}).get("code") == "NOT_LOGGED_IN"
          and body.get("exitCode") == 3, {"code": code, "error": body.get("error")})

    # 2. usage error → 2 with a USAGE object (argparse already wrote usage to stderr)
    code, out, _ = cli(["run", "x", "--nope", "--json"], config_dir=cfg)
    body = one_object(out) if out.strip() else {}
    check("usageExit2", code == 2 and (body.get("error") or {}).get("code") == "USAGE",
          {"code": code, "error": body.get("error")})

    # 3. unknown run → the server's 404 code, exit 1
    code, out, err = cli(["status", "w5-does-not-exist", "--json"], config_dir=cfg,
                         token=token)
    body = one_object(out) if out.strip() else {}
    check("unknownRunExit1", code == 1
          and (body.get("error") or {}).get("code") == "NOT_FOUND"
          and (body.get("error") or {}).get("status") == 404,
          {"code": code, "error": body.get("error")})

    # 4. capture is the same object, written 0600
    code, out, _ = cli(["status", "w5-does-not-exist", "--json", "--capture", str(capture)],
                       config_dir=cfg, token=token)
    captured = json.loads(capture.read_text()) if capture.is_file() else {}
    mode = stat.S_IMODE(capture.stat().st_mode) if capture.is_file() else None
    check("captureMatchesStdout", bool(captured) and captured == one_object(out),
          {"captured": captured.get("error"), "mode": oct(mode) if mode else None})
    check("captureIsOwnerOnly", mode == 0o600, {"mode": oct(mode) if mode else None})

    # 5. token storage is owner-only, and logout removes it
    code, _, err = cli(["login", "--token", token], config_dir=cfg)
    stored = cfg / "credentials.json"
    file_mode = stat.S_IMODE(stored.stat().st_mode) if stored.is_file() else None
    dir_mode = stat.S_IMODE(cfg.stat().st_mode) if cfg.is_dir() else None
    check("tokenStoreOwnerOnly", code == 0 and file_mode == 0o600 and dir_mode == 0o700,
          {"file": oct(file_mode) if file_mode else None,
           "dir": oct(dir_mode) if dir_mode else None})
    code, _, _ = cli(["logout"], config_dir=cfg)
    check("logoutRemovesStore", code == 0 and not stored.exists(), {"code": code})

    # 6. the token appears in no output
    combined = "\n".join((out, err)) + capture.read_text() if capture.is_file() else out + err
    check("tokenAbsentFromOutput", token not in combined, {"checkedBytes": len(combined)})

    # 7. a real run created through the web API is readable by the CLI
    status, project = tpl.http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w5 cli {stamp}"})
    run_id = f"w5-cli-{stamp}"
    status_plan, plan = tpl.http(
        "POST", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}/plan", token=token,
        body={"goal": "W5 CLI diagnostics run", "projectId": project["id"]})
    success_capture = base / "success.json"
    code, out, err = cli(["status", run_id, "--json", "--capture", str(success_capture)],
                         config_dir=cfg, token=token)
    body = one_object(out) if out.strip() else {}
    check("realRunReadableExit0",
          status == 201 and status_plan == 201 and code == 0
          and body.get("state") in {"planning", "awaiting_plan_approval"}
          and body.get("ok") is True,
          {"project": status, "plan": status_plan, "code": code,
           "state": body.get("state"), "stderr": err[-200:]})
    check("successCaptureMatches", success_capture.is_file()
          and json.loads(success_capture.read_text()) == body,
          {"exists": success_capture.is_file()})

    evidence = {
        "schema": 1,
        "kind": "w5-cli-diagnostics",
        "takenAt": datetime.now(UTC).isoformat(),
        "runId": run_id,
        "model": "none (CLI contract + API read)",
        "checks": {k: v["ok"] for k, v in observations.items()},
        "observations": observations,
        "verdict": "PASS" if not failures else "FAIL",
        "failedChecks": failures,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w5-cli-diagnostics-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"], "failed": failures,
                      "evidence": str(path)}, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
