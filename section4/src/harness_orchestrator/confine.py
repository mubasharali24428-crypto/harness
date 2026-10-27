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

import glob
import logging
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

SEATBELT_BIN = "/usr/bin/sandbox-exec"
CONFINEMENT_ENV = "HARNESS_ENGINE_CONFINEMENT"
logger = logging.getLogger(__name__)

# Everything the worker + dsh runtime legitimately need; nothing else leaks in
# (no database URLs, master keys, cloud credentials, tokens of other services).
ENV_ALLOW_EXACT = frozenset(
    {"PATH", "LANG", "LC_ALL", "TERM", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "PYTHONPATH"}
)
ENV_ALLOW_PREFIX = ("DSH_",)
_CRED_DIRS = (".ssh", ".aws", ".gnupg", ".config", ".kube", "Library/Keychains")

#: Routing variables the worker is launched with by value (see wrap_command).
ROUTING_ENV = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"})
#: The run network's only way out, sandbox purpose (docs use 3129).
EGRESS_SANDBOX_URL_ENV = "HARNESS_SANDBOX_PROXY_URL"
EGRESS_SANDBOX_URL_DEFAULT = "http://harness-egress:3128"
INSTALLS_ENV = "DSH_ALLOW_INSTALLS"


def install_env(mode: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Env additions when the operator permits package installs (W4 slice 4).

    Only the container boundary qualifies: the allow-list is enforced at the
    egress proxy, and a seatbelt worker has no egress boundary at all. Returns
    nothing otherwise, so the default stays "installs are blocked".
    """
    env = os.environ if environ is None else environ
    if env.get(INSTALLS_ENV, "").strip() != "1" or mode != "container":
        return {}
    url = env.get(EGRESS_SANDBOX_URL_ENV, "").strip() or EGRESS_SANDBOX_URL_DEFAULT
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "NO_PROXY": "localhost,127.0.0.1,model,harness-egress",
        INSTALLS_ENV: "1",
    }


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


MAX_FORBIDDEN_TARGETS = 64


def forbidden_targets(workspace: str, patterns: list[str] | None) -> list[str]:
    """Existing workspace paths the plan's ``forbiddenPaths`` name, as real paths (S-01).

    The command policy only reads command strings, so ``python3 -c "open('pay'+'roll/x', 'w')"``
    slips past it. These targets are made read-only by the boundary itself. Patterns are
    workspace-relative (globs allowed); only paths that exist at launch can be bound, and a
    link that resolves outside the workspace is skipped (the policy's symlink check owns it).
    """
    root = os.path.realpath(workspace)
    found: dict[str, None] = {}
    for pattern in patterns or []:
        rel = pattern.strip().lstrip("/").rstrip("/")
        if not rel or ".." in Path(rel).parts:
            continue
        for match in glob.glob(os.path.join(root, rel), recursive=True):
            real = os.path.realpath(match)
            if real != root and real.startswith(root + os.sep):
                found[real] = None
    if len(found) > MAX_FORBIDDEN_TARGETS:
        raise ConfinementUnavailableError(
            f"forbiddenPaths match {len(found)} paths (limit {MAX_FORBIDDEN_TARGETS}); "
            "narrow the patterns rather than run with some of them unprotected"
        )
    return sorted(found)


def scope_targets(
    workspace: str, scope_paths: list[str] | None, forbidden: list[str]
) -> list[str] | None:
    """Real paths the run intent's ``scopePaths`` name, when narrower than the workspace (M03).

    ``None`` means the whole workspace is in scope. Otherwise the container mounts the workspace
    read-only and binds each target back read-write, so the approval holds for every writer in the
    worker - the shell, ``python3 -c``, and the native file tools alike (M01 measured that the
    string check holds for none of them). Fail-closed by omission: a path that does not exist at
    launch cannot be bound and stays read-only, a link that resolves outside the workspace is never
    bound, and a scope at or beneath a forbidden target is dropped (forbidden paths are hard).
    """
    if not scope_paths:
        return None
    root = os.path.realpath(workspace)
    found: dict[str, None] = {}
    for raw in scope_paths:
        rel = str(raw).strip().strip("/")
        real = os.path.realpath(os.path.join(root, rel))
        if real == root:
            return None
        inside = real.startswith(root + os.sep) and ".." not in Path(rel).parts
        if not inside or not os.path.exists(real):
            continue
        if any(real == f or real.startswith(f + os.sep) for f in forbidden):
            continue
        found[real] = None
    return sorted(found)


def forbidden_snapshot(workspace: str, patterns: list[str] | None) -> dict[str, str]:
    """File -> sha256 for every file under a forbidden pattern (detective check for paths
    the mounts cannot cover: created after launch, or no mount mode)."""
    import hashlib

    root = os.path.realpath(workspace)
    snap: dict[str, str] = {}
    for pattern in patterns or []:
        for hit in glob.glob(os.path.join(root, pattern.strip("/")), recursive=True):
            walk = (
                [os.path.join(d, f) for d, _, fs in os.walk(hit) for f in fs]
                if os.path.isdir(hit)
                else [hit]
            )
            for path in walk[:5000]:
                try:
                    with open(path, "rb") as fh:
                        snap[path] = hashlib.sha256(fh.read()).hexdigest()
                except OSError:
                    snap[path] = "?"
    return snap


def seatbelt_profile(
    roots: list[str],
    workspace: str,
    session_dir: str,
    tmp_dir: str,
    forbidden: list[str] | None = None,
) -> str:
    """Last matching rule wins in SBPL: broad read, deny the runs root, re-allow own dirs."""
    own = (workspace, session_dir, tmp_dir)
    home = str(Path.home())
    cred = " ".join(f'(subpath "{home}/{d}")' for d in _CRED_DIRS)
    own_read = " ".join(f'(subpath "{p}")' for p in own)
    deny_roots = " ".join(f'(subpath "{r}")' for r in roots)
    frozen = " ".join(f'(subpath "{p}")' for p in forbidden or [])
    frozen_rule = f"(deny file-write* {frozen})\n" if frozen else ""
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
        f"{frozen_rule}"
        "(allow mach-lookup)\n"
        "(allow sysctl-read)\n"
        "(allow ipc-posix-shm)\n"
        "(allow iokit-get-properties)\n"
        "(allow network*)\n"  # the model provider must be reachable
        "(allow system-socket)\n"
    )


RUNS_VOLUME_ENV = "HARNESS_RUNS_VOLUME"
RUNS_ROOT_ENV = "HARNESS_RUNS_ROOT"
#: Opt out of binding the volume's engine-host directory (see `_mount`).
RUNS_BIND_ENV = "HARNESS_RUNS_BIND"
_MOUNTPOINT_CACHE: dict[str, str | None] = {}


def volume_mountpoint(volume: str) -> str | None:
    """The engine-host directory behind a named volume, or None when unresolvable.

    Cached: it is a Docker round trip, and the answer cannot change for a
    running process. `HARNESS_RUNS_BIND=0` forces the volume-subpath form on a
    deployment where the engine's own filesystem is not reachable this way.
    """
    if volume in _MOUNTPOINT_CACHE:
        return _MOUNTPOINT_CACHE[volume]
    resolved: str | None = None
    if os.environ.get(RUNS_BIND_ENV, "1").strip() != "0":
        try:
            out = subprocess.run(
                ["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", volume],
                capture_output=True,
                text=True,
                timeout=15,
            )
            candidate = out.stdout.strip()
            if out.returncode == 0 and candidate.startswith("/") and "," not in candidate:
                resolved = candidate
        except (OSError, subprocess.SubprocessError):
            resolved = None
    _MOUNTPOINT_CACHE[volume] = resolved
    return resolved


def _mount(path: str, readonly: str) -> str:
    """``--mount`` value for ``path``; ``readonly`` is the literal suffix (``readonly=false`` …).

    A launcher on the host binds the path as is. The worker daemon (M07) runs in a container,
    and the Docker engine resolves a bind source on ITS host, where the daemon's paths do not
    exist; so paths under ``HARNESS_RUNS_ROOT`` are re-expressed against the shared volume —
    as a **bind of the volume's own engine-host directory** when that is resolvable, and only
    as a `volume-subpath` when it is not.

    The order is not cosmetic. Measured 2026-10-27: Docker re-creates a
    ``volume-subpath`` directory at mount time as root:root 0755, *undoing the ownership
    handover the launcher just performed* — a pre-set 0o2777 mode was reset to 0755 too. The
    worker then runs as 10001 inside a directory it cannot write, every `write` returns EACCES,
    and no container-mode run can produce a change at all. A bind of the same underlying
    directory preserves both ownership and mode (verified: the agent wrote its file), which is
    why this path is tried first. A missing subpath is not a problem here: the launcher creates
    and hands over the directory before `docker run`.
    """
    volume = os.environ.get(RUNS_VOLUME_ENV, "").strip()
    root = os.path.realpath(os.environ.get(RUNS_ROOT_ENV, "") or "/nonexistent")
    if volume and path.startswith(root + os.sep):
        sub = os.path.relpath(path, root)
        mount = volume_mountpoint(volume)
        if mount:
            return f"type=bind,src={os.path.join(mount, sub)},dst={path},{readonly}"
        return f"type=volume,src={volume},dst={path},volume-subpath={sub},{readonly}"
    return f"type=bind,src={path},dst={path},{readonly}"


def _worker_user(workspace: str) -> str:
    """Never root. A root launcher (the worker daemon) runs the worker as the workspace's
    owner, so the run can write what the orchestrator provisioned; anyone else, as itself."""
    if os.getuid() == 0:
        st = os.stat(workspace)
        return f"{st.st_uid}:{st.st_gid}" if st.st_uid else "10001:10001"
    return f"{os.getuid()}:{os.getgid() or 10001}"


#: The uid/gid the engine worker image is built with, and the identity
#: `_worker_user` falls back to for a root-owned run dir.
WORKER_UID, WORKER_GID = 10001, 10001


def _owner_uid(path: str) -> int:
    """Owner of a path, in one place so the ownership handover is testable as root."""
    return os.stat(path).st_uid


def claim_run_dirs_for_worker(paths: Iterable[str]) -> list[str]:
    """Take a ROOT-owned run directory away from root; leave every other owner alone.

    Two halves have to agree, or the run cannot do its job: `_worker_user` refuses to
    run the worker as root, so it launches the container as the directory's owner (or,
    for a root-owned directory, as the image's own user 10001). A directory owned by
    root therefore produces a mismatch — 10001 inside a root:root 0755 directory, every
    `write` answered `EACCES … mkdir`, the run settling `blocked` with no changes at all
    (measured 2026-10-27 on the compose stack, and the reason no container-mode run had
    ever produced a reviewable diff).

    But taking *every* directory is just as wrong, in the other direction: normally the
    ORCHESTRATOR provisions the run dirs as its own uid, and it is also the process that
    stages, checkpoints and diffs the files afterwards. Re-owning them here moved them
    out from under that process, and the next run failed in `staging` with
    `Permission denied` on the very file the agent had just written.

    So: only a root-owned directory is claimed, and only then is its tree handed to
    `WORKER_UID`. A non-root launcher has nothing to do. Returns the paths actually
    changed, so the caller can record the handover.
    """
    if os.getuid() != 0:
        return []
    changed: list[str] = []
    for top in paths:
        if not os.path.isdir(top):
            # Loud, because the common cause is a directory the launcher believes
            # it created and did not: ownership then comes from whoever creates it
            # later (Docker's volume-subpath creates it as root), and the worker's
            # write fails with EACCES again.
            logger.warning("run dir %s does not exist; nothing handed to the worker", top)
            continue
        try:
            if _owner_uid(top) != 0:
                continue  # provisioned by another uid: that uid is the one that must keep it
            targets = [top]
            for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
                for name in (*dirnames, *filenames):
                    targets.append(os.path.join(dirpath, name))
        except OSError:
            continue  # missing/unreadable tree: nothing to claim
        for target in targets:
            try:
                os.lchown(target, WORKER_UID, WORKER_GID)
                changed.append(target)
            except OSError:
                break  # one unlinkable entry: leave the rest, the worker reports its own EACCES
    if changed:
        logger.info("handed %d run path(s) to uid %d before launching the worker",
                    len(changed), WORKER_UID)
    return changed


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
    forbidden_paths: list[str] | None = None,
    scope_paths: list[str] | None = None,
) -> list[str]:
    """Build the worker launch command for the selected boundary."""
    forbidden = forbidden_targets(workspace, forbidden_paths) if mode != "none" else []
    if mode == "container":
        if not container_name or not network:
            raise ConfinementUnavailableError("container requires a private network and name")
        for path in (workspace, session_dir):
            if Path(path).is_symlink() or not Path(path).is_dir() or "," in path:
                raise ValueError("container mounts must be real directories without commas")
        scope = scope_targets(workspace, scope_paths, forbidden)
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
            # 128 was enough for python + the runtime's node process, and not
            # enough once a browser is mounted: measured on the stack, the worker
            # sits at 115/128 tasks and Chromium needs ~40 more, so the first
            # render of a run succeeded and every later one hung in
            # pthread_create until the gate's timeout (W10). The cap still bounds
            # a fork bomb; it is a resource limit, not the security boundary.
            "512",
            "--memory",
            "1g",
            "--cpus",
            "1",
            "--user",
            _worker_user(workspace),
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=256m,mode=1777",
            "--workdir",
            workspace,
        ]
        # M03: a narrower intent freezes the workspace and binds only the scope back writable.
        frozen = "true" if scope is not None else "false"
        result += ["--mount", _mount(workspace, f"readonly={frozen}")]
        result += ["--mount", _mount(session_dir, "readonly=false")]
        for path in scope or []:
            if "," in path:
                raise ConfinementUnavailableError("scope paths must not contain commas")
            result += ["--mount", _mount(path, "readonly=false")]
        for path in forbidden:  # S-01: nested read-only bind; Docker mounts deeper paths later
            if "," in path:
                raise ConfinementUnavailableError("forbidden paths must not contain commas")
            result += ["--mount", _mount(path, "readonly")]
        # Values are inherited by Docker from its scrubbed client environment, never argv.
        # One documented exception: the egress routing variables. They are non-secret
        # (an internal alias URL), and `--env KEY` would resolve to the DAEMON's own
        # environment — which does not, and must not, carry the run's proxy config.
        # Without them nothing inside the worker is bounded by the allow-list, so
        # installs (W4 slice 4) would mean an unbounded fetch.
        for key in sorted(env or {}):
            if key in ROUTING_ENV:
                result += ["--env", f"{key}={env[key]}"]
            elif key not in {"PATH", "PYTHONPATH", "TMPDIR", "HOME"}:
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
    profile.write_text(seatbelt_profile(roots, workspace, session_dir, tmp_dir, forbidden))
    return [SEATBELT_BIN, "-f", str(profile), *argv]
