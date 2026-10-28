#!/usr/bin/env python3
"""W6 gate: the same goal from the CLI and over HTTP produces the same audit trail.

What "parity" means here, precisely — because a loose version of this check
would pass on nothing:

  * both paths go through the SAME gate (propose → approve → start), never a
    shortcut, and neither path approves implicitly;
  * the identity event log for each run contains the same ORDERED sequence of
    event types (content differs — a model is nondeterministic; the lifecycle
    must not);
  * both end in the same run state;
  * the CLI's token store is owner-only (0600 in a 0700 directory) and its
    `--json` is exactly one object on stdout, so a pipe stays parseable.

Exit 0 = parity proven. Any divergence prints both sequences side by side and
exits 1. Stdlib-only, like compose_journey.py, so it cannot fail for reasons
other than the stack or the CLI.

    python3 scripts/e2e/cli_parity.py [--goal "..."] [--timeout 300]
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLIENT_PY = ROOT / "clients" / "python" / ".venv" / "bin" / "python"
# Every run writes its own file (`w6-cli-parity-<epoch>.json`), like the W4
# drills: a later run must never overwrite the evidence a claim was made from.
EVIDENCE = (
    ROOT / "docs" / "briefs" / "evidence" / "pilot-prep" / f"w6-cli-parity-{int(time.time())}.json"
)

SETTLED = {
    "done",
    "error",
    "failed",
    "execution_failed",
    "stopped",
    "cancelled",
    "blocked",
    "awaiting_done_signal",
    "pr_opened",
}


def _load_journey():
    spec = importlib.util.spec_from_file_location(
        "compose_journey", ROOT / "scripts" / "e2e" / "compose_journey.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def _cli(env: dict[str, str], *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [str(CLIENT_PY), "-m", "harness_sdk.cli.main", *args],
        check=False,  # the return code is part of the compared evidence
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _types_for(journey, project_id: str, token: str, run_id: str) -> list[str]:
    status, replay = journey.http(
        "GET",
        f"{journey.IDENTITY}/api/v1/projects/{project_id}/events/replay?limit=1000",
        token=token,
    )
    if status != 200:
        _fail(f"event replay answered {status}: {replay!r}")
    items = (replay or {}).get("items") or []
    return [ev.get("type") for ev in items if ev.get("runId") == run_id]


def _http_run(journey, token: str, project_id: str, goal: str, deadline: float) -> dict:
    """The web's own path: propose → approve → start → poll."""
    status, plan = journey.http(
        "POST",
        f"{journey.ORCHESTRATOR}/api/v1/orchestrator/plans",
        token=token,
        body={"goal": goal, "projectId": project_id},
    )
    if status not in (200, 201):
        _fail(f"propose answered {status}: {plan!r}")
    plan_id = plan.get("planId")
    if not plan_id:
        _fail(f"propose returned no planId: {plan!r}")
    status, body = journey.http(
        "POST", f"{journey.ORCHESTRATOR}/api/v1/orchestrator/plans/{plan_id}/approve", token=token
    )
    if status != 200:
        _fail(f"approve answered {status}: {body!r}")
    run_id = (body or {}).get("runId")
    if not run_id:
        _fail(f"approve returned no runId: {body!r}")
    status, body = journey.http(
        "POST", f"{journey.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}/start", token=token
    )
    if status != 200:
        _fail(f"start answered {status}: {body!r}")
    state = "unknown"
    while time.monotonic() < deadline:
        status, run = journey.http(
            "GET", f"{journey.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}", token=token
        )
        if status == 200 and isinstance(run, dict):
            state = str(run.get("state") or "unknown")
            if state in SETTLED:
                break
        time.sleep(2.0)
    return {"runId": run_id, "planId": plan_id, "state": state}


def main() -> int:
    goal = "Reply with the single word: present"
    timeout = 300.0
    argv = sys.argv[1:]
    while argv:
        flag = argv.pop(0)
        if flag == "--goal" and argv:
            goal = argv.pop(0)
        elif flag == "--timeout" and argv:
            timeout = float(argv.pop(0))
        else:
            _fail(f"unknown argument {flag!r}")
    if not CLIENT_PY.exists():
        _fail(f"client venv missing at {CLIENT_PY}; run `make bootstrap`")

    journey = _load_journey()
    token, _principal = journey.authenticate("parity login", journey.EMAIL_A)
    status, seed = journey.http(
        "POST", f"{journey.ORCHESTRATOR}/api/v1/orchestrator/demo/seed", token=token, body={}
    )
    if status not in (200, 201):
        _fail(f"demo seed answered {status}: {seed!r}")
    project_id = seed.get("projectId")
    print(f"PASS  signed in, project {project_id}")

    deadline = time.monotonic() + timeout

    # --- CLI path, using the offline token store the plan requires -----------
    with tempfile.TemporaryDirectory(prefix="harness-parity-") as cfg:
        env = {
            **os.environ,
            "HARNESS_CONFIG_DIR": cfg,
            "HARNESS_ORCHESTRATOR_URL": journey.ORCHESTRATOR,
            "HARNESS_IDENTITY_URL": journey.IDENTITY,
            "HARNESS_TOKEN": "",
        }
        env.pop("HARNESS_TOKEN")
        code, out, err = _cli(env, "login", "--token", token)
        if code != 0:
            _fail(f"login --token failed ({code}): {err.strip()[:300]}")
        # HARNESS_CONFIG_DIR is used verbatim (no extra "harness" level).
        cred = Path(cfg) / "credentials.json"
        if not cred.exists():
            _fail(f"no credentials file at {cred}")
        mode = stat.S_IMODE(cred.stat().st_mode)
        if mode != 0o600:
            _fail(f"credentials mode is {oct(mode)}, expected 0o600")
        dir_mode = stat.S_IMODE(cred.parent.stat().st_mode)
        if dir_mode != 0o700:
            _fail(f"config dir mode is {oct(dir_mode)}, expected 0o700")
        print(f"PASS  token stored 0600 in 0700 ({cred})")

        code, out, err = _cli(
            env, "run", goal, "--project", project_id, "--yes", "--json",
            "--timeout", str(timeout),
        )
        if code != 0:
            _fail(f"cli run failed ({code}). stdout={out[:600]!r} stderr={err.strip()[-2000:]}")
        try:
            payload = json.loads(out)
        except ValueError:
            _fail(f"cli --json emitted something other than one JSON object: {out[:300]!r}")
        if not isinstance(payload, dict) or not payload.get("runId"):
            _fail(f"cli --json payload has no runId: {payload!r}")
        # "one object per invocation": decode the first document and require
        # that NOTHING but whitespace follows it.
        decoder = json.JSONDecoder()
        _, end = decoder.raw_decode(out.lstrip())
        if out.lstrip()[end:].strip():
            _fail(f"cli --json printed trailing output after the object: {out[end:][:200]!r}")
        cli_run = payload
        print(f"PASS  cli run {cli_run['runId']} → {cli_run.get('state')}")

        # Claim 2: `harness diff` shows the run's real diff — the server's own
        # payload, not a client-side reconstruction. Compared for equality. A
        # run with no done-signal diff has none to show; in that case both
        # paths must agree on the absence with the same machine code, which is
        # a parity claim too (and the honest one for a goal that edits nothing).
        code, out, err = _cli(env, "diff", cli_run["runId"], "--json")
        status, server_diff = journey.http(
            "GET",
            f"{journey.ORCHESTRATOR}/api/v1/orchestrator/runs/{cli_run['runId']}/diff",
            token=token,
        )
        if status == 404:
            try:
                payload = json.loads(out)
            except ValueError:
                _fail(f"cli diff printed no JSON object on absence: {out[:300]!r}")
            error = payload.get("error") or {}
            if code != 1 or error.get("code") != "NOT_FOUND":
                _fail(f"cli diff absence mismatch: exit {code}, error {error!r}")
            print("PASS  cli diff reports the server's absence (404 NOT_FOUND)")
        else:
            if code != 0:
                _fail(f"cli diff failed ({code}): {err.strip()[-400:]}")
            if status != 200:
                _fail(f"diff endpoint answered {status}: {server_diff!r}")
            if json.loads(out) != server_diff:
                _fail("cli diff differs from the server payload")
            count = len(server_diff.get("files") or [])
            print(f"PASS  cli diff == server payload ({count} file(s))")

    http_run = _http_run(journey, token, project_id, goal, deadline)
    print(f"PASS  http run {http_run['runId']} → {http_run['state']}")

    cli_types = _types_for(journey, project_id, token, cli_run["runId"])
    http_types = _types_for(journey, project_id, token, http_run["runId"])
    if not cli_types:
        _fail("no events recorded for the CLI run — parity cannot be claimed")

    same_sequence = cli_types == http_types
    same_state = cli_run.get("state") == http_run["state"]
    evidence = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "goal": goal,
        "cli": {"runId": cli_run["runId"], "state": cli_run.get("state"), "events": cli_types},
        "http": {"runId": http_run["runId"], "state": http_run["state"], "events": http_types},
        "identicalSequence": same_sequence,
        "identicalState": same_state,
        "credentialPermissions": "0600 file in 0700 dir",
        "jsonSingleObject": True,
    }
    EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")

    if not same_sequence:
        print(f"  cli : {cli_types}")
        print(f"  http: {http_types}")
        _fail("event sequences diverge")
    if not same_state:
        _fail(f"final states diverge: cli={cli_run.get('state')} http={http_run['state']}")

    print(f"PASS  identical audit trail: {len(cli_types)} events, {sorted(set(cli_types))}")
    print(f"PASS  evidence → {EVIDENCE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
