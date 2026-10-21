"""Section 5 shell gate for the engine's ``bash`` tool (part 52, path-to-9 B1).

The DeepSeek runtime's own shell needs a PTY module the Linux wheels do not ship
(``docs/briefs/evidence/s12/part52.md``). Section 4 replaces that tool with a small
runtime plugin which hands every command to this script:

    python -I shell_gate.py < {"command", "cwd", "forbiddenPaths", "scopePaths", "timeoutSeconds"}

It applies the same rules as Section 5's own exec path (``validate_command``: allow-list,
no shell operators, secret files, forbidden paths), refuses paths inside the workspace
that fall outside the run intent's ``scopePaths``, then runs the command under OS
confinement (Seatbelt or Landlock; none available = refused) with a sanitized
environment. It prints one JSON line: ``{"denied": reason}`` or ``{"exitCode", "output"}``.

Stdlib only, and the sibling modules are loaded by file path, so the worker image needs
these three files and not the ``harness_sandbox`` package (whose ``__init__`` pulls in
pydantic and docker).
"""

import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_OUTPUT_CHARS = 30_000


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = _load("policy")
confinement = _load("confinement")


def scope_violation(command: str, cwd: str, scope_paths: list[str]) -> str | None:
    """First workspace path named by the command that lies outside every scope path."""
    if not scope_paths:
        return None
    root = os.path.realpath(cwd)
    scopes = [os.path.realpath(os.path.join(root, s)) for s in scope_paths]
    for token in shlex.split(command)[1:]:
        path = os.path.join(root, token)
        if token.startswith("-") or not (
            os.path.lexists(path) or ("/" in token and os.path.isdir(os.path.dirname(path)))
        ):
            # ponytail: a path is a token that exists or whose parent does; flags, words and
            # code strings (python -c "...") are skipped, and --flag=path is not parsed.
            continue
        real = os.path.realpath(path)
        if real != root and not real.startswith(root + os.sep):
            continue  # outside the workspace: confinement already stops writes there
        if not any(real == s or real.startswith(s + os.sep) for s in scopes):
            return f"path '{token}' is outside the run intent's scope paths"
    return None


def run(req: dict) -> dict:
    command, cwd = req["command"], req["cwd"]
    try:
        policy.validate_command(command, req.get("forbiddenPaths") or [], cwd=cwd)
    except policy.SecurityPolicyViolationError as exc:
        return {"denied": str(exc)}
    reason = scope_violation(command, cwd, req.get("scopePaths") or [])
    if reason:
        return {"denied": reason}
    with tempfile.TemporaryDirectory(prefix="harness-shell-") as tmp:
        try:
            argv = confinement.confine_argv(
                ["/bin/sh", "-c", command], workspace=Path(cwd), tmpdir=Path(tmp)
            )
        except confinement.ConfinementUnavailableError as exc:
            return {"denied": f"{exc}; refusing to run unconfined"}
        env = policy.sanitize_environment(os.environ)
        env.update(TMPDIR=tmp, TMP=tmp, TEMP=tmp)
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True,
        )
        # The plugin sends SIGTERM when the tool call is aborted: take the command's group down too.
        signal.signal(
            signal.SIGTERM, lambda *_: (os.killpg(proc.pid, signal.SIGKILL), sys.exit(143))
        )
        try:
            out, _ = proc.communicate(timeout=req.get("timeoutSeconds") or 300)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            return {"exitCode": None, "output": out[-MAX_OUTPUT_CHARS:] + "\n[timed out]"}
    return {"exitCode": proc.returncode, "output": out[-MAX_OUTPUT_CHARS:]}


if __name__ == "__main__":
    sys.stdout.write(json.dumps(run(json.loads(sys.stdin.read()))) + "\n")
