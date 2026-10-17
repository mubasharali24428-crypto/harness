"""F023: process-isolated engine runs.

The in-process thread path mutates process-global ``os.environ`` for the
provider key (cross-run credential race), shares one workspace, and cannot be
killed. This runs each live call in its own child process: explicit minimal
environment (only that run's credential), per-run workspace, own process
group so timeout/cancel/stop SIGKILLs the child *and* its grandchildren.

Fail-closed: a workspace that cannot be proven inside the confinement root
refuses the run before anything is spawned.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

RESULT_MARKER = "@@RESULT@@"
ALLOW_UNCONFINED_ENV = "ENGINE_ALLOW_UNCONFINED_LIVE"
CONFINEMENT_UNPROVEN_CODE = "ENGINE_CONFINEMENT_UNPROVEN"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_OUTPUT = 4 * 1024 * 1024
# The only parent variables a child may inherit; credentials are passed explicitly.
_INHERIT = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


class ConfinementError(RuntimeError):
    """Workspace/run-id failed the confinement gate; nothing was spawned."""


def per_run_workspace(root: str | os.PathLike[str], run_id: str) -> Path:
    """Resolve ``root/run_id`` or raise — never a path outside ``root``."""
    if not _RUN_ID_RE.match(run_id or "") or run_id in {".", ".."}:
        raise ConfinementError(f"run id {run_id!r} is not a safe workspace name")
    root_p = Path(root)
    if not root_p.is_absolute() or root_p.parent == root_p:
        raise ConfinementError(f"confinement root {str(root)!r} must be an absolute non-root dir")
    root_real = root_p.resolve()
    target = (root_real / run_id).resolve()
    if root_real not in target.parents:
        raise ConfinementError(f"workspace for {run_id!r} escapes the confinement root")
    return target


def build_child_env(
    *,
    api_key: str,
    base_url: str,
    model: str,
    workspace: Path,
    session_root: Path,
    extra_pythonpath: Sequence[str] = (),
) -> dict[str, str]:
    """Explicit child environment: no inherited secrets, one run's credential."""
    env = {k: os.environ[k] for k in _INHERIT if k in os.environ}
    env.update(
        {
            "DEEPSEEK_API_KEY": api_key,
            "DSH_MODEL": model,
            "DSH_WORKSPACE": str(workspace),
            "DSH_SESSION_ROOT": str(session_root),
            "PYTHONPATH": os.pathsep.join(extra_pythonpath),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if base_url:
        env["DEEPSEEK_BASE_URL"] = base_url
    return env


def kill_process_group(pid: int) -> None:
    """SIGKILL the whole group (child + grandchildren); already-dead is fine."""
    try:
        os.killpg(pid, signal.SIGKILL)  # child is its own group leader
    except (ProcessLookupError, PermissionError):
        pass


class ProcessEngine:
    """Run one ``DeepSeekHarnessWrapper`` call in a killable child process."""

    isolated = True

    def __init__(
        self,
        wrapper: Any,
        *,
        confinement_root: str | os.PathLike[str] | None = None,
        argv: Sequence[str] | None = None,
        cleanup: bool = True,
        enforce_confinement: bool = True,
    ) -> None:
        self.wrapper = wrapper
        self.confinement_root = confinement_root or wrapper.workspace
        self.argv = list(argv) if argv else [sys.executable, "-m", "harness_orchestrator.sdk_child"]
        self.cleanup = cleanup
        self.enforce_confinement = enforce_confinement
        self.last_pgid: int | None = None  # exposed for kill-verification tests

    def __getattr__(self, name: str) -> Any:  # model/api_key/mode/... pass through
        return getattr(self.wrapper, name)

    async def run_isolated(self, goal: str, session_id: str) -> dict[str, Any]:
        if self.enforce_confinement and os.environ.get(ALLOW_UNCONFINED_ENV) != "1":
            # Fail closed: the SDK's tool/network/edit hooks are not proven
            # routed through S5 authority (NEEDS INTEGRATION), so live runs are
            # refused unless an operator explicitly accepts that risk.
            return {
                "sessionId": session_id,
                "engine": "confinement",
                "status": "error",
                "errorCode": CONFINEMENT_UNPROVEN_CODE,
                "finalResponse": (
                    "live run refused: SDK tool confinement is unproven; set "
                    f"{ALLOW_UNCONFINED_ENV}=1 to accept an unconfined run."
                ),
            }
        workspace = per_run_workspace(self.confinement_root, session_id)
        session_root = workspace / ".sessions"
        workspace.mkdir(parents=True, exist_ok=True)
        pkg_root = str(Path(__file__).resolve().parent.parent)
        env = build_child_env(
            api_key=self.wrapper.api_key,
            base_url=self.wrapper.base_url,
            model=self.wrapper.model,
            workspace=workspace,
            session_root=session_root,
            extra_pythonpath=[pkg_root, *[p for p in sys.path if p and os.path.isdir(p)]],
        )
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=str(workspace),
            start_new_session=True,
        )
        self.last_pgid = proc.pid
        try:
            payload = json.dumps({"goal": goal, "sessionId": session_id}).encode()
            out, _ = await proc.communicate(payload)
        finally:
            # Cancel/timeout/stop land here: the group dies before we return.
            kill_process_group(proc.pid)
            if proc.returncode is None:
                await proc.wait()
            if self.cleanup:
                shutil.rmtree(workspace, ignore_errors=True)
        return self._parse(out[:_MAX_OUTPUT], proc.returncode, session_id)

    def _parse(self, out: bytes, returncode: int | None, session_id: str) -> dict[str, Any]:
        for line in reversed(out.decode("utf-8", "replace").splitlines()):
            if line.startswith(RESULT_MARKER):
                try:
                    data = json.loads(line[len(RESULT_MARKER) :])
                except ValueError:
                    break
                if isinstance(data, dict):
                    return data
        from harness_orchestrator.redact import scrub_secrets

        return {
            "sessionId": session_id,
            "engine": self.wrapper._engine_name(),
            "status": "error",
            "finalResponse": scrub_secrets(
                f"engine child exited ({returncode}) without a result", self.wrapper.api_key
            ),
        }
