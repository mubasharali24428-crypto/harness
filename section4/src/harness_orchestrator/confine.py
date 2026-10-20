"""OS confinement + environment allowlist for the isolated engine worker (B2).

The live engine runs in its own process (``engine_worker``). That process - and
every shell command the DeepSeek runtime spawns beneath it - is launched under a
macOS seatbelt profile that:

* allows writes ONLY to the run's own workspace, session dir and tmp dir;
* denies reads of the shared runs root (other runs' workspaces/sessions) except
  the run's own directories;
* denies reads of common credential locations.

Mirrors section5's ``confinement.seatbelt_profile`` (sections may import only
``harness_contracts``, so the small profile is restated here) but additionally
confines READS between runs, which section5's does not.

Modes (``HARNESS_ENGINE_CONFINEMENT``): ``container`` uses a per-run Docker
boundary and private model relay; ``auto`` uses seatbelt when available,
otherwise none; ``seatbelt`` / ``required`` fail closed when unavailable;
``off`` explicitly accepts an unconfined worker. The real runtime accepts only
container or explicit off (seatbelt breaks its PTY).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SEATBELT_BIN = "/usr/bin/sandbox-exec"
CONFINEMENT_ENV = "HARNESS_ENGINE_CONFINEMENT"

# Everything the worker + dsh runtime legitimately need; nothing else leaks in
# (no database URLs, master keys, cloud credentials, tokens of other services).
ENV_ALLOW_EXACT = frozenset(
    {"PATH", "LANG", "LC_ALL", "TERM", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "PYTHONPATH"}
)
ENV_ALLOW_PREFIX = ("DSH_",)
_CRED_DIRS = (".ssh", ".aws", ".gnupg", ".config", ".kube", "Library/Keychains")


class ConfinementUnavailableError(RuntimeError):
    """Confinement was demanded but this host cannot provide it."""


def seatbelt_available() -> bool:
    return sys.platform == "darwin" and Path(SEATBELT_BIN).exists()


def resolve_mode(requested: str | None = None) -> str:
    """Resolve the requested boundary; raise when a strict mode cannot be met."""
    mode = (requested or os.environ.get(CONFINEMENT_ENV, "auto")).strip().lower()
    if mode == "container":
        return "container"
    if mode == "off":
        return "none"
    if mode in ("seatbelt", "required"):
        if not seatbelt_available():
            raise ConfinementUnavailableError(
                f"{CONFINEMENT_ENV}={mode} but seatbelt is unavailable on this host"
            )
        return "seatbelt"
    if mode == "auto":
        return "seatbelt" if seatbelt_available() else "none"
    raise ValueError(f"unknown {CONFINEMENT_ENV}={mode!r}")


def scrubbed_env(extra: dict[str, str], tmp_dir: str) -> dict[str, str]:
    """Allowlist the parent environment, then add explicit ``extra`` values."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ENV_ALLOW_EXACT or k.startswith(ENV_ALLOW_PREFIX)
    }
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update(extra)
    env["TMPDIR"] = tmp_dir
    env["HOME"] = tmp_dir  # runtimes need a writable home; keep it inside the run's own sandbox
    env["PYTHONUNBUFFERED"] = "1"
    return env


def seatbelt_profile(roots: list[str], workspace: str, session_dir: str, tmp_dir: str) -> str:
    """Last matching rule wins in SBPL: broad read, deny the runs root, re-allow own dirs."""
    own = (workspace, session_dir, tmp_dir)
    home = str(Path.home())
    cred = " ".join(f'(subpath "{home}/{d}")' for d in _CRED_DIRS)
    own_read = " ".join(f'(subpath "{p}")' for p in own)
    deny_roots = " ".join(f'(subpath "{r}")' for r in roots)
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow signal (target same-sandbox))\n"  # kill own children (pty cleanup) only
        "(allow process-info* (target same-sandbox))\n"
        "(allow file-read*)\n"
        f"(deny file-read* {deny_roots})\n"
        # stat/traverse stays allowed (loaders walk up from cwd); contents and
        # directory listings of other runs stay denied.
        f"(allow file-read-metadata {deny_roots})\n"
        f"(allow file-read* {own_read})\n"
        f"(deny file-read* {cred})\n"
        f'(allow file-write* {own_read} (literal "/dev/null") (literal "/dev/tty"))\n'
        "(allow pseudo-tty)\n"  # the agent's persistent shell runs on a pty
        "(allow file-ioctl)\n"
        '(allow file-read* file-write* (literal "/dev/ptmx") (regex #"^/dev/ttys[0-9]+$"))\n'
        "(allow mach-lookup)\n"
        "(allow sysctl-read)\n"
        "(allow ipc-posix-shm)\n"
        "(allow iokit-get-properties)\n"
        "(allow network*)\n"  # the model provider must be reachable
        "(allow system-socket)\n"
    )


def wrap_command(
    argv: list[str],
    *,
    mode: str,
    roots: list[str],
    workspace: str,
    session_dir: str,
    tmp_dir: str,
    container_name: str | None = None,
    network: str | None = None,
    image: str = "harness-engine-worker:p0",
    env: dict[str, str] | None = None,
) -> list[str]:
    """Build the worker launch command for the selected boundary."""
    if mode == "container":
        if not container_name or not network:
            raise ConfinementUnavailableError("container requires a private network and name")
        for path in (workspace, session_dir):
            if Path(path).is_symlink() or not Path(path).is_dir() or "," in path:
                raise ValueError("container mounts must be real directories without commas")
        result = [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--name",
            container_name,
            "--network",
            network,
            "--dns",
            "127.0.0.1",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "1g",
            "--cpus",
            "1",
            "--user",
            f"{os.getuid() or 10001}:{os.getgid() or 10001}",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=256m,mode=1777",
            "--workdir",
            workspace,
        ]
        for path in (workspace, session_dir):
            result += ["--mount", f"type=bind,src={path},dst={path},readonly=false"]
        # Values are inherited by Docker from its scrubbed client environment, never argv.
        for key in sorted(env or {}):
            if key not in {"PATH", "PYTHONPATH", "TMPDIR", "HOME"}:
                result += ["--env", key]
        result += [
            "--env",
            "HOME=/tmp",
            "--env",
            "TMPDIR=/tmp",
            "--entrypoint",
            "python",
            image,
            *argv[1:],
        ]
        return result
    if mode != "seatbelt":
        return argv
    profile = Path(tmp_dir) / "run.sb"
    profile.write_text(seatbelt_profile(roots, workspace, session_dir, tmp_dir))
    return [SEATBELT_BIN, "-f", str(profile), *argv]
