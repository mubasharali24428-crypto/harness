"""Section 5 shell gate for the engine's ``bash`` tool (part 52, path-to-9 B1).

The DeepSeek runtime's own shell needs a PTY module the Linux wheels do not ship
(``docs/briefs/evidence/s12/part52.md``). Section 4 replaces that tool with a small
runtime plugin which hands every command to this script:

    python -I shell_gate.py < {"command", "cwd", "forbiddenPaths", "scopePaths", "timeoutSeconds"}

It applies the same rules as Section 5's own exec path (``validate_command``: allow-list,
no shell operators, secret files, forbidden paths), refuses arguments that resolve outside the
run intent's ``scopePaths`` (including out-of-workspace spellings: ``../..``, ``~/x``,
``$HOME/x``, ``--flag=path``, ``-o/path``), then runs the command under OS
confinement (Seatbelt or Landlock; none available = refused) whose only writable part of
the workspace is that scope (M03), with a sanitized environment. It prints one JSON line:
``{"denied": reason}`` or ``{"exitCode", "output"}``.

Stdlib only, and the sibling modules are loaded by file path, so the worker image needs
these three files and not the ``harness_sandbox`` package (whose ``__init__`` pulls in
pydantic and docker).
"""

import glob
import importlib.util
import json
import os
import re
import shlex
import signal
import string
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


def deny(reason: str, code: str, alternative: str, request_scope: str | None = None) -> dict:
    """W27: a refusal says why (`denied`, unchanged), what to do instead, and what to ask for.

    `requestScope` names the one path whose addition to the run's scope would unblock the
    command. Nothing here retries, switches model or widens scope: that stays the user's call.
    """
    out = {"denied": reason, "code": code, "alternative": alternative}
    if request_scope:
        out["requestScope"] = request_scope
        # a first-class request the relay can surface: one path, nothing granted by asking
        out["scopeRequest"] = {"path": request_scope, "grants": False}
    return out


def scope_violation(command: str, cwd: str, scope_paths: list[str]) -> str | None:
    """First workspace path named by the command that lies outside every scope path."""
    token = _scope_hit(command, cwd, scope_paths)
    return f"path '{token}' is outside the run intent's scope paths" if token else None


def _scope_hit(command: str, cwd: str, scope_paths: list[str]) -> str | None:
    """First argument whose path resolves outside every scope path.

    Part 52 left items: a path is checked the way ``/bin/sh`` would act on it, so
    ``$VAR`` (against the child's sanitized env), a leading ``~``, ``..`` (via
    realpath) and a flag's path value (``--flag=path``, ``-o/path``) are resolved
    before the scope test. A token that resolves outside the workspace is denied
    here too: the OS boundary stops writes out there, but reads have no boundary.
    Symlinks are followed on purpose: a link inside scope that points out is
    denied (stricter than "no symlink follow"). Bare words and code strings
    (``python -c "..."``) are still skipped: code strings stay the write
    boundary's business (M03).
    """
    if not scope_paths:
        return None
    root = os.path.realpath(cwd)
    scopes = [os.path.realpath(os.path.join(root, s)) for s in scope_paths]
    env = policy.sanitize_environment(os.environ)
    for token in shlex.split(command)[1:]:
        spelling = token
        if token.startswith("-"):
            if "=" in token:
                spelling = token.split("=", 1)[1]  # --flag=path: the value carries the path
            elif not token.startswith("--") and token[2:3] in ("/", ".", "~", "$"):
                spelling = token[2:]  # -o/tmp/x: a short flag with its path stuck on
            else:
                continue  # a flag with no path value (-la, --verbose)
            if not spelling:
                continue
        expanded = string.Template(spelling).safe_substitute(env)
        if _UNRESOLVED_VAR.search(expanded):
            # ponytail: sh would expand it (unset var, ${X:-...}); the gate can't know
            # where it points, so it refuses. Ceiling: a quoted '$foo' grep pattern is
            # refused too.
            return spelling
        if expanded.startswith("~"):
            path = os.path.expanduser(expanded)
        else:
            path = os.path.join(root, expanded)
        if not _is_path_candidate(path, expanded, root):
            continue
        real = os.path.realpath(path)
        if not any(real == s or real.startswith(s + os.sep) for s in scopes):
            return spelling
    return None


_UNRESOLVED_VAR = re.compile(r"\$\{?[A-Za-z_]")


def _is_path_candidate(path: str, spelling: str, root: str) -> bool:
    """Does sh treat this token as a path? Words, regexes and code strings are not."""
    if spelling.startswith("~") or os.path.lexists(path):
        return True
    parts = spelling.split("/")
    if ".." in parts:
        # `../../x` reaches the target even when it does not exist; `a/../b` (a regex,
        # a sed script) only resolves if `a` exists, and then the kernel walks it.
        prefix = "/".join(parts[: parts.index("..")])
        return os.path.isdir(os.path.join(root, prefix))
    # ponytail: a path is a token that exists or whose parent does.
    return "/" in spelling and os.path.isdir(os.path.dirname(path))


def scope_roots(cwd: str, scope_paths: list[str]) -> list[Path] | None:
    """M03: the only writable roots for a narrower intent; ``None`` = the whole workspace.

    ``scope_violation`` reads command strings, so ``python3 -c "open('do'+'cs/x','w')"`` passed
    it (M01). Handing these to the OS boundary as its write set is what actually refuses that.
    A scope path that does not exist yet, or resolves outside the workspace, is not writable.
    """
    if not scope_paths:
        return None
    root = os.path.realpath(cwd)
    roots: list[Path] = []
    for scope in scope_paths:
        real = os.path.realpath(os.path.join(root, str(scope).strip().strip("/")))
        if real == root:
            return None
        if real.startswith(root + os.sep) and os.path.exists(real):
            roots.append(Path(real))
    return roots


def unprotected_forbidden(cwd: str, patterns: list[str]) -> str | None:
    """First existing forbidden path that is still writable (not a read-only mount)."""
    root = os.path.realpath(cwd)
    for pattern in patterns:
        for hit in glob.glob(os.path.join(root, pattern.strip("/")), recursive=True):
            real = os.path.realpath(hit)
            if (real == root or real.startswith(root + os.sep)) and not (
                os.statvfs(real).f_flag & os.ST_RDONLY
            ):
                # ponytail: only the container's read-only bind mounts count; Landlock/Seatbelt
                # here cannot deny a subtree under an allowed workspace.
                return pattern
    return None


def run(req: dict) -> dict:
    command, cwd = req["command"], req["cwd"]
    install_ok, install_reason = policy.install_permission()
    try:
        policy.validate_command(command, req.get("forbiddenPaths") or [], cwd=cwd,
                                allow_installs=install_ok)
    except policy.SecurityPolicyViolationError as exc:
        if not install_ok and any(p.search(command) for p in policy.INSTALL_PATTERNS):
            # say WHY: an agent (and the human reading the transcript) should see
            # that installs are gated by the boundary, not merely forbidden
            return deny(f"{exc} — installs: {install_reason}", "INSTALL_BLOCKED",
                        "Use only what is already in the workspace, or stop and tell the user "
                        "which package you need so they can decide.")
        return deny(str(exc), "COMMAND_REFUSED",
                    "Do this with the file tools (read, write, edit) instead of the shell, or "
                    "stop and tell the user what you were trying to do.")
    open_path = unprotected_forbidden(cwd, req.get("forbiddenPaths") or [])
    if open_path:
        return deny(f"forbidden path '{open_path}' is not read-only here; refusing to run",
                    "FORBIDDEN_PATH_EXPOSED",
                    "Do not touch that path. Stop and tell the user; this is an environment "
                    "problem, not something a retry fixes.")
    scopes = req.get("scopePaths") or []
    hit = _scope_hit(command, cwd, scopes)
    if hit:
        return deny(f"path '{hit}' is outside the run intent's scope paths", "OUT_OF_SCOPE",
                    f"Stay inside: {', '.join(scopes)}. If '{hit}' is needed, stop and tell the "
                    f"user to add it to this run's scope (an intent edit, which asks for "
                    f"approval again); do not look for another way around.", hit)
    with tempfile.TemporaryDirectory(prefix="harness-shell-") as tmp:
        try:
            argv = confinement.confine_argv(
                ["/bin/sh", "-c", command], workspace=Path(cwd), tmpdir=Path(tmp),
                write_roots=scope_roots(cwd, req.get("scopePaths") or []),
            )
        except confinement.ConfinementUnavailableError as exc:
            return deny(f"{exc}; refusing to run unconfined", "CONFINEMENT_UNAVAILABLE",
                        "No safe alternative exists in this worker. Stop and tell the user so "
                        "the operator can fix the worker; commands are never run unconfined.")
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
            return {"exitCode": None, "timedOut": True,
                    "output": out[-MAX_OUTPUT_CHARS:] + "\n[timed out]"}
    return {"exitCode": proc.returncode, "output": out[-MAX_OUTPUT_CHARS:]}


def _sanitize_own_environment() -> None:
    """G2 root fix: re-exec self with a sanitized environment ONCE.

    On Linux, /proc/<gate-pid>/environ is readable by the gate's confined
    child (the read allow-list grants /proc). Whatever the CALLER does, a gate
    process holding the runner token or DB DSN in its environment leaks them
    to every command it confines — demonstrated live in the worker image
    (probe 2026-11-21, "BYPASS-LEAKED"). Re-exec strips the sensitive keys at
    the source: after this, the environ a confined child reads is clean, and
    the command child gets the sanitized env it always got.
    """
    module = _load("policy")
    sanitized = module.sanitize_environment(os.environ)
    if sanitized == dict(os.environ):
        return  # nothing sensitive present; no re-exec needed
    os.environ.clear()
    os.environ.update(sanitized)
    os.execve(sys.executable, [sys.executable, "-I", str(Path(__file__).resolve())],
              sanitized)


if __name__ == "__main__":
    _sanitize_own_environment()
    sys.stdout.write(json.dumps(run(json.loads(sys.stdin.read()))) + "\n")
