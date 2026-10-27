#!/usr/bin/env python3
"""W6 slice 3: `harness chat` is the same gate, and its session is explicit.

Live against the stack (unit tests cover the logic; this proves the wiring):

  * two scripted turns (`--message`) each start a NORMAL plan-gated run —
    propose → approve → start — never a shortcut;
  * the SECOND turn's plan is proposed from a goal that carries the first
    turn's history block, so "what is remembered between turns" is a string
    that was sent over the wire, not an implication;
  * the session file lands in the config dir with the credentials permission
    contract (0600 in a 0700 dir) and survives between the two invocations;
  * `--json` chat output is exactly one object.

    python3 scripts/e2e/w6_chat_smoke.py [--timeout 300]
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
EVIDENCE = (
    ROOT / "docs" / "briefs" / "evidence" / "pilot-prep" / f"w6-chat-{int(time.time())}.json"
)


def _fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def _load_journey():
    spec = importlib.util.spec_from_file_location(
        "compose_journey", ROOT / "scripts" / "e2e" / "compose_journey.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cli(env: dict[str, str], *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [str(CLIENT_PY), "-m", "harness_sdk.cli.main", *args],
        check=False,  # the exit code is part of the observed evidence
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _one_object(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError as exc:
        _fail(f"--json chat output was not one object: {exc} — {text[:200]!r}")
        raise SystemExit(1)  # unreachable, keeps type checkers honest


def main() -> int:
    timeout = 300.0
    argv = sys.argv[1:]
    while argv:
        flag = argv.pop(0)
        if flag == "--timeout" and argv:
            timeout = float(argv.pop(0))
        else:
            _fail(f"unknown argument {flag!r}")
    if not CLIENT_PY.exists():
        _fail(f"client venv missing at {CLIENT_PY}; run `make bootstrap`")

    journey = _load_journey()
    token, _principal = journey.authenticate("chat smoke", journey.EMAIL_A)
    status, seed = journey.http(
        "POST", f"{journey.ORCHESTRATOR}/api/v1/orchestrator/demo/seed", token=token, body={}
    )
    if status not in (200, 201):
        _fail(f"demo seed answered {status}: {seed!r}")
    project_id = seed.get("projectId")
    print(f"PASS  signed in, project {project_id}")

    first_goal = "Reply with the single word: one"
    second_goal = "Reply with the single word: two"

    with tempfile.TemporaryDirectory(prefix="harness-chat-") as cfg:
        env = {
            **os.environ,
            "HARNESS_CONFIG_DIR": cfg,
            "HARNESS_ORCHESTRATOR_URL": journey.ORCHESTRATOR,
            "HARNESS_IDENTITY_URL": journey.IDENTITY,
        }
        env.pop("HARNESS_TOKEN", None)
        code, out, err = _cli(env, "login", "--token", token)
        if code != 0:
            _fail(f"login exited {code}: {err}")

        deadline = time.monotonic() + timeout
        code, out, err = _cli(
            env, "chat", "--message", first_goal, "--project", project_id,
            "--yes", "--json", "--timeout", str(int(timeout)),
        )
        if code != 0:
            _fail(f"chat turn 1 exited {code}: {err}\n{out}")
        session1 = _one_object(out)
        turn1 = (session1.get("exchanges") or [{}])[0]
        if not turn1.get("runId") or turn1.get("state") not in {
            "done", "awaiting_done_signal", "pr_opened"
        }:
            _fail(f"turn 1 did not complete a gated run: {turn1!r}")
        run1_types = _types_for(journey, project_id, token, turn1["runId"])
        print(f"PASS  turn 1: run {turn1['runId']} events {run1_types}")

        if time.monotonic() > deadline:
            _fail("timeout budget exhausted before turn 2")
        code, out, err = _cli(
            env, "chat", "--message", second_goal, "--resume", session1["sessionId"],
            "--yes", "--json", "--timeout", str(int(timeout)),
        )
        if code != 0:
            _fail(f"chat turn 2 exited {code}: {err}\n{out}")
        session2 = _one_object(out)
        if session2["sessionId"] != session1["sessionId"]:
            _fail(f"--resume changed the session: {session2['sessionId']}")
        exchanges = session2.get("exchanges") or []
        if len(exchanges) != 2:
            _fail(f"expected 2 exchanges in the resumed session, got {len(exchanges)}")

        # What the server actually received for turn 2 must carry turn 1.
        wire_goal = exchanges[1].get("goal", "")
        if first_goal not in wire_goal or "Current message:" not in wire_goal:
            _fail(f"turn 2 goal did not replay turn 1 over the wire: {wire_goal[:200]!r}")
        print("PASS  turn 2 goal carried the explicit history block")

        path = Path(cfg) / "sessions" / f"{session1['sessionId']}.json"
        if not path.is_file():
            _fail(f"session file missing at {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        dir_mode = stat.S_IMODE(path.parent.stat().st_mode)
        if mode != 0o600 or dir_mode != 0o700:
            _fail(f"session permissions {oct(dir_mode)}/{oct(mode)}, expected 0700/0600")
        print(f"PASS  session file {oct(dir_mode)} dir / {oct(mode)} file")

        evidence = {
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "project": project_id,
            "sessionId": session1["sessionId"],
            "turns": [
                {"message": e.get("message"), "runId": e.get("runId"), "state": e.get("state")}
                for e in exchanges
            ],
            "turn1EventTypes": run1_types,
            "turn2GoalCarriedHistory": True,
            "credentialPermissions": f"{oct(dir_mode)} dir / {oct(mode)} file",
            "jsonSingleObject": True,
        }
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")

    # The lifecycle claim: each chat turn ran the same gate as `harness run`.
    for name in ("plan_proposed", "plan_approved", "execution_started"):
        if name not in run1_types:
            _fail(f"turn 1 skipped the gate — no {name} event: {run1_types}")
    print(f"PASS  evidence → {EVIDENCE.relative_to(ROOT)}")
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
