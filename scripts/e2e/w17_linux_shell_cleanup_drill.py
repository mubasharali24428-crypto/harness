#!/usr/bin/env python3
"""W17 leg: Linux-confined SHELL cleanup — the runner's process-group kill reaps a
detached grandchild under Landlock. Previously unproven because the dev host is
macOS (Seatbelt path); this must run on a Linux host/container with Landlock.

Drives the REAL product path, not an imitation:
  LocalSandboxRunner(confinement="landlock").create_task(...) -> exec(command)
The command forks a detached `sleep` grandchild into the same new session/group,
records its pid, then hangs so the exec budget expires. On timeout the runner calls
its `_kill`, which `os.killpg(pgid, SIGKILL)` — the whole group, grandchild included.
Verdict = the grandchild is dead after the run, plus confinement really was Landlock.

Run inside a Linux sandbox container, e.g.:
    docker cp scripts/e2e/w17_linux_shell_cleanup_drill.py harness-sandbox-1:/tmp/drill.py
    docker exec harness-sandbox-1 python /tmp/drill.py
"""
import asyncio
import json
import os
import platform
import signal
import sys
import time
import uuid
from pathlib import Path

EXIT = {"verdict": "SKIP", "reason": "", "checks": {}}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # No `ps` in a slim container: read /proc. State Z (zombie) is dead for us.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            state = fh.read().rsplit(") ", 1)[1].split()[0]
        return state != "Z"
    except (FileNotFoundError, IndexError):
        return False


def _wait_dead(pid: int, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


async def run() -> dict:
    from harness_sandbox import config, confinement
    from harness_sandbox.local_runner import (
        LocalSandboxRunner,
        SandboxCommandTimeoutError,
    )

    checks: dict[str, bool] = {}
    checks["isLinux"] = platform.system() == "Linux"
    checks["landlockSupported"] = bool(confinement.landlock_supported())
    if not (checks["isLinux"] and checks["landlockSupported"]):
        return {"verdict": "SKIP", "reason": "needs Linux + Landlock", "checks": checks}

    config.default_timeout_seconds = 2  # bound the run so the timeout kill fires

    root = Path("/tmp") / f"w17-shell-cleanup-{uuid.uuid4().hex[:8]}"
    runner = LocalSandboxRunner(storage_root=root, confinement="landlock", allow_network=False)
    task_id = "w17-s-cleanup"
    handle = runner.create_task(task_id, repo_ref=str(root), branch="main",
                                forbidden_paths=["secrets/"], project_id="p", team_id="t")
    resolved = runner._resolve_confinement()
    checks["resolvedConfinementIsLandlock"] = resolved == "landlock"

    gc_file = str(runner.active_tasks[task_id]["tmpdir"] + "/gc.pid")
    # Fork a detached grandchild into the group, record its pid, then hang so the
    # exec budget expires and the runner's timeout path kills the whole group.
    command = (
        "python3 -c 'import subprocess,sys,time;"
        f"p=subprocess.Popen([\"sleep\",\"300\"]);"
        f"open(\"{gc_file}\",\"w\").write(str(p.pid));"
        "sys.stdout.flush();time.sleep(300)'"
    )
    leader_pgids: list[int] = []
    timed_out = False
    try:
        async for _line in runner.exec(handle, command):
            pass
    except SandboxCommandTimeoutError:
        timed_out = True
    # the proc objects recorded their group; capture leader pgid for the report
    for proc in runner.active_tasks[task_id]["procs"]:
        try:
            leader_pgids.append(os.getpgid(proc.pid))
        except ProcessLookupError:
            pass

    checks["execTimedOut"] = bool(timed_out)
    grandchild = int(Path(gc_file).read_text()) if Path(gc_file).exists() else 0
    checks["grandchildWasSpawned"] = grandchild > 0
    # Liveness positive control: a detached sleep in the DRILL's own group is
    # alive; this proves _alive is not blind (a missing ps must not read as dead).
    import subprocess

    control = subprocess.Popen(["sleep", "30"])  # noqa: ASYNC220 — 2-line drill control
    checks["_aliveDetectsLiveProcess"] = _alive(control.pid)
    control.kill()
    control.wait()
    # The grandchild is NOT the leader; only the group-kill reaches it. The leader
    # already died with the killpg; assert the group member is gone too.
    checks["grandchildReapedByGroupKill"] = grandchild > 0 and _wait_dead(grandchild)
    # cleanup the whole task tree + any stragglers (drill hygiene, not the claim)
    for pid in [grandchild, *leader_pgids]:
        if pid and _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for pgid in leader_pgids:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    import shutil

    shutil.rmtree(root, ignore_errors=True)
    checks["taskStorageRemoved"] = not root.exists()
    verdict = "PASS" if all(checks.values()) else "FAIL"
    return {"verdict": verdict, "platform": f"{platform.system()} {os.uname().release}",
            "resolvedConfinement": resolved, "checks": checks, "grandchildPid": grandchild}


if __name__ == "__main__":
    try:
        EXIT = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 — keep failed evidence, no secret text
        EXIT = {"verdict": "FAIL", "error": str(exc)[:300]}
    print(json.dumps(EXIT))
    sys.exit(0 if EXIT.get("verdict") == "PASS" else (0 if EXIT.get("verdict") == "SKIP" else 1))
