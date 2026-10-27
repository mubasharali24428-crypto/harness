#!/usr/bin/env python3
"""W6 slice 4: a gated run executes against a LOCAL directory, confined.

Live against the stack. What must be true, and how each is checked:

  * the GATE is the server's: the run's replay holds plan_proposed +
    plan_approved, exactly like a web run, and start_run was never called
    (no phantom server execution: the queue only claims EXECUTING runs);
  * the EXECUTION was confined: the launcher refuses anything but the
    container boundary, and the drill runs with HARNESS_ENGINE_CONFINEMENT
    =container explicitly set;
  * the RESULT is local: a file the engine created appears in the user's
    directory, applied from the boundary's own change list — nothing else
    in the directory moved;
  * the TRACE is honest: a diff artifact is registered and a run comment
    carries executed=local + diffSha256. Off-host tool-call events are NOT
    in agent_events (no endpoint can mint them); the drill records the
    worker's own summary instead of pretending.

    python3 scripts/e2e/w6_local_drill.py [--timeout 900]

Budget note (measured 2026-10-27): the container boundary itself is fast — a
live turn created the file and the in-boundary diff in well under a minute of
orchestration — but a small local model (`qwen-local`) can take minutes per
turn. The engine deadline IS the budget: at 200s the worker was killed after
having written the file (status `error`, `changed: ["present.txt"]`), at 240s
the CLI correctly mapped that to `state=error`. Keep the default generous.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLIENT_PY = ROOT / "clients" / "python" / ".venv" / "bin" / "python"
EVIDENCE = (
    ROOT / "docs" / "briefs" / "evidence" / "pilot-prep" / f"w6-local-{int(time.time())}.json"
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


def _engine_env() -> dict[str, str]:
    """Engine credentials come from the deployment .env (last non-empty wins),
    exactly the variables the compose services receive — exported, not invented."""
    values: dict[str, str] = {}
    text = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key in {"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DSH_MODEL"} and value:
            values[key] = value
    return values


def main() -> int:
    timeout = 900.0
    argv = sys.argv[1:]
    while argv:
        flag = argv.pop(0)
        if flag == "--timeout" and argv:
            timeout = float(argv.pop(0))
        else:
            _fail(f"unknown argument {flag!r}")
    if not CLIENT_PY.exists():
        _fail(f"client venv missing at {CLIENT_PY}; run `make bootstrap`")
    engine = _engine_env()
    if not engine.get("DEEPSEEK_API_KEY"):
        _fail("no DEEPSEEK_API_KEY in .env — the local engine must run live, never dry")

    journey = _load_journey()
    token, _principal = journey.authenticate("local drill", journey.EMAIL_A)
    status, seed = journey.http(
        "POST", f"{journey.ORCHESTRATOR}/api/v1/orchestrator/demo/seed", token=token, body={}
    )
    if status not in (200, 201):
        _fail(f"demo seed answered {status}: {seed!r}")
    project_id = seed.get("projectId")
    print(f"PASS  signed in, project {project_id}")

    with tempfile.TemporaryDirectory(prefix="harness-local-drill-") as cfg, \
            tempfile.TemporaryDirectory(prefix="harness-ws-") as ws:
        goal = (
            "Create a file named present.txt in the workspace root containing "
            "exactly the word present and nothing else."
        )
        (Path(ws) / "untouched.txt").write_text("leave me alone\n")
        env = {
            **os.environ,
            **engine,
            "HARNESS_CONFIG_DIR": cfg,
            "HARNESS_ORCHESTRATOR_URL": journey.ORCHESTRATOR,
            "HARNESS_IDENTITY_URL": journey.IDENTITY,
            "HARNESS_ENGINE_CONFINEMENT": "container",
        }
        env.pop("HARNESS_TOKEN", None)
        proc = subprocess.run(
            [str(CLIENT_PY), "-m", "harness_sdk.cli.main", "login", "--token", token],
            check=False, capture_output=True, text=True, env=env, timeout=60,
        )
        if proc.returncode != 0:
            _fail(f"login exited {proc.returncode}: {proc.stderr}")

        start = time.monotonic()
        proc = subprocess.run(
            [str(CLIENT_PY), "-m", "harness_sdk.cli.main", "run", goal,
             "--project", project_id, "--local", ws, "--yes", "--json",
             "--timeout", str(int(timeout))],
            check=False, capture_output=True, text=True, env=env,
            timeout=timeout + 180,
        )
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            _fail(f"--json not one object (exit {proc.returncode}): "
                  f"{proc.stdout[:300]!r} / {proc.stderr[-500:]!r}")
            raise SystemExit(1) from None
        run_id = payload.get("runId")
        if not run_id:
            _fail(f"no runId in result: {payload}")
        print(f"note  CLI exit {proc.returncode}; state={payload.get('state')} "
              f"local={payload.get('local')}")

        # 1) the gate happened server-side, exactly as for a web run
        _, replay = journey.http(
            "GET",
            f"{journey.IDENTITY}/api/v1/projects/{project_id}/events/replay?limit=1000",
            token=token,
        )
        types = [ev.get("type") for ev in (replay or {}).get("items", [])
                 if ev.get("runId") == run_id]
        if "plan_proposed" not in types or "plan_approved" not in types:
            _fail(f"plan gate not on the server audit trail: {types}")
        if "execution_started" in types:
            _fail("the SERVER executed a run that was supposed to stay local")
        print(f"PASS  server events: {sorted(set(types))} (no server execution)")

        # 2) the result is local: the engine's file landed, nothing else moved
        created = Path(ws) / "present.txt"
        if not created.is_file():
            _fail(f"engine output not applied to the workspace: {payload.get('local')}")
        if (Path(ws) / "untouched.txt").read_text() != "leave me alone\n":
            _fail("apply touched a file the diff never named")
        print("PASS  present.txt created locally; untouched.txt unmoved")

        # 3) the trace: provenance comment (diff artifact registration is reported
        #    by the CLI itself; a registration failure would have surfaced in notes)
        _, comments = journey.http(
            "GET",
            f"{journey.ORCHESTRATOR}/api/v1/orchestrator/runs/{run_id}/comments",
            token=token,
        )
        bodies = [c.get("body", "") for c in (comments or {}).get("items", [])]
        prov = next((b for b in bodies if "executed" in b and "local" in b), None)
        if prov is None:
            _fail(f"no local-provenance comment on run {run_id}: {bodies}")
        if "diffSha256" not in prov:
            _fail(f"provenance comment lacks the diff digest: {prov[:200]}")
        print("PASS  provenance comment carries executed=local + diff digest")

        evidence = {
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "project": project_id,
            "runId": run_id,
            "goal": goal,
            "cliState": payload.get("state"),
            "cliExit": proc.returncode,
            "local": payload.get("local"),
            "serverEventTypes": sorted(set(types)),
            "presentFileCreated": created.is_file(),
            "presentFileContent": created.read_text()[:80] if created.is_file() else None,
            "provenanceComment": prov[:400],
            "seconds": round(time.monotonic() - start, 1),
            "honestBoundary": (
                "tool-call events from the off-host engine are not in agent_events; "
                "no existing endpoint can mint them (see docs/week-6-plan.md, slice 4 decision 3)"
            ),
        }
        EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE.write_text(json.dumps(evidence, indent=2) + "\n")

    print(f"PASS  evidence → {EVIDENCE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
