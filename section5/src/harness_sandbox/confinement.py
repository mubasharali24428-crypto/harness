"""F6r — OS-level confinement for sandboxed command execution.

macOS: Seatbelt profiles via /usr/bin/sandbox-exec.
Linux: Landlock via ctypes (explicit requests fail closed when unsupported —
see S1-R03d; `auto` picks the best available mode).

Layering contract: policy.py decides WHAT may run; this module decides WHERE it
may touch the machine. Reads stay broad (toolchains need system SDKs), writes are
confined to the task workspace + its private tmp, network is configurable.
"""

import logging
import os
import struct
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SEATBELT_BIN = "/usr/bin/sandbox-exec"


def platform_release() -> str:
    import platform

    return platform.release()


def seatbelt_available() -> bool:
    return sys.platform == "darwin" and Path(SEATBELT_BIN).exists()


# Cache for the runtime probe: 'usable' is a property of THIS process's
# environment (kernel + container runtime policy), constant for its lifetime.
_LANDLOCK_PROBE: bool | None = None


def _landlock_kernel_ok() -> bool:
    try:
        parts = platform_release().split(".")
        major, minor = int(parts[0]), int(parts[1])
        return major > 5 or (major == 5 and minor >= 13)
    except Exception:  # noqa: BLE001 — unknown kernels: assume unsupported
        return False


def _probe_landlock_syscalls(timeout: float = 10.0) -> bool:
    """Fork one child through the SAME preexec hook the runner uses and see
    whether landlock_create_ruleset + landlock_add_rule are permitted."""
    import subprocess

    with tempfile.TemporaryDirectory(prefix="harness-ll-probe-") as ws:
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "pass"],
                preexec_fn=lambda: apply_landlock_in_child(Path(ws), False),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
        except Exception:  # noqa: BLE001 — any probe failure = unsupported
            return False
    return proc.returncode == 0


def landlock_supported() -> bool:
    """True only when Landlock is actually USABLE here — kernel AND policy.

    Version alone over-promises: container runtimes with the default seccomp
    profile (Docker included) report a 6.x kernel but block the landlock
    syscalls with EPERM/ENOSYS. The old version-only check made 'auto'
    resolve to 'landlock' inside the shipped compose image, and every sandbox
    exec died in preexec_fn (found by the OPS-20 E2E, 2026-10-17). The probe
    result is cached for the process lifetime.
    """
    global _LANDLOCK_PROBE
    if sys.platform != "linux":
        return False
    if _LANDLOCK_PROBE is None:
        _LANDLOCK_PROBE = _landlock_kernel_ok() and _probe_landlock_syscalls()
    return _LANDLOCK_PROBE


# --------------------------------------------------------------------------
# Seatbelt (macOS)
# --------------------------------------------------------------------------


def seatbelt_profile(
    workspace: Path,
    allow_network: bool,
    task_dir: Path | None = None,
    tmpdir: Path | None = None,
) -> str:
    """deny-default profile; writes confined to the task workspace + its private tmp.

    T7b: the shared system temp dir is NOT writable, and file CONTENTS under the
    storage root (every task's workspace) are unreadable except this task's own
    directory. Metadata (stat) stays readable so cwd resolution keeps working.
    """
    ws = str(Path(workspace).resolve())
    writable = [f'(subpath "{ws}")']
    if tmpdir is not None:
        writable.append(f'(subpath "{Path(tmpdir).resolve()}")')
    writable.append('(subpath "/dev/null")')
    isolation = ""
    if task_dir is not None:
        task = Path(task_dir).resolve()
        # Later rules win in SBPL: deny the whole store, then re-allow our own task.
        isolation = (
            f'(deny file-read-data (subpath "{task.parent}"))\n'
            f'(allow file-read-data (subpath "{task}"))\n'
        )
    network = "(allow network*)\n(allow system-socket)" if allow_network else "(deny network*)"
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow file-read*)\n"
        f"{isolation}"
        f"(allow file-write* {' '.join(writable)})\n"
        "(allow mach-lookup)\n"
        "(allow sysctl-read)\n"
        "(allow ipc-posix-shm)\n"
        "(allow iokit-get-properties)\n"
        f"{network}\n"
    )


def write_seatbelt_profile(
    workspace: Path, allow_network: bool, target_dir: Path, tmpdir: Path | None = None
) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "sb.sb"
    path.write_text(seatbelt_profile(workspace, allow_network, target_dir, tmpdir))
    return path


def seatbelt_command(command: str, profile_path: Path) -> list[str]:
    return [SEATBELT_BIN, "-f", str(profile_path), "/bin/sh", "-c", command]


# --------------------------------------------------------------------------
# Linux Landlock (best-effort). Applied inside the forked child via preexec_fn.
# We only HANDLE write/remove/make rights, so reads+execute stay unrestricted
# by construction; writes get explicit allow rules for workspace + the task's private tmp.
# --------------------------------------------------------------------------

_LL_CREATE_RULESET = 444
_LL_ADD_RULE = 445
_LL_RESTRICT_SELF = 446
_LL_RULE_PATH_BENEATH = 1

# Access bits per landlock ABI v1.
LL_EXECUTE = 1 << 0
LL_WRITE_FILE = 1 << 1
LL_READ_FILE = 1 << 2
LL_READ_DIR = 1 << 3
LL_REMOVE_DIR = 1 << 4
LL_REMOVE_FILE = 1 << 5
LL_MAKE_CHAR = 1 << 6
LL_MAKE_DIR = 1 << 7
LL_MAKE_REG = 1 << 8
LL_MAKE_SOCK = 1 << 9
LL_MAKE_FIFO = 1 << 10
LL_MAKE_BLOCK = 1 << 11
LL_MAKE_SYM = 1 << 12

_HANDLED = (
    LL_WRITE_FILE | LL_REMOVE_DIR | LL_REMOVE_FILE | LL_MAKE_CHAR | LL_MAKE_DIR
    | LL_MAKE_REG | LL_MAKE_SOCK | LL_MAKE_FIFO | LL_MAKE_BLOCK | LL_MAKE_SYM
)
_READ_EXEC_EVERYWHERE = LL_EXECUTE | LL_READ_FILE | LL_READ_DIR


def apply_landlock_in_child(
    workspace: Path, allow_network: bool, tmpdir: Path | None = None
) -> bool:
    """Runs INSIDE the forked child (preexec_fn). Returns True when enforced.

    S1-R03d: failure RAISES — subprocess propagates preexec_fn exceptions to
    the parent's ``await create_subprocess_shell``, so a requested-but-failing
    landlock aborts the spawn instead of silently running unconfined.
    """
    import ctypes
    import ctypes.util

    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    attr = struct.pack("Q", _HANDLED)
    ruleset_fd = libc.syscall(_LL_CREATE_RULESET, attr, len(attr), 0)
    if ruleset_fd < 0:
        raise OSError("landlock_create_ruleset failed")

    def add_beneath(parent_fd: int, allowed: int) -> None:
        rule = struct.pack("iQi", _LL_RULE_PATH_BENEATH, allowed, parent_fd)
        if libc.syscall(_LL_ADD_RULE, ruleset_fd, _LL_RULE_PATH_BENEATH, rule) != 0:
            raise OSError("landlock_add_rule failed")

    O_PATH = os.O_PATH | os.O_CLOEXEC

    def fs_fd(path: str) -> int:
        return os.open(path, O_PATH)

    add_beneath(fs_fd("/"), _READ_EXEC_EVERYWHERE)
    # T7b: writes only to the workspace and this task's private tmp (never the shared temp).
    writable = {str(Path(workspace).resolve())}
    if tmpdir is not None:
        writable.add(str(Path(tmpdir).resolve()))
    for p in writable:
        try:
            add_beneath(fs_fd(p), _HANDLED)
        except OSError:  # unreadable temp dir variants etc.
            continue

    if libc.syscall(_LL_RESTRICT_SELF, ruleset_fd, 0) != 0:
        raise OSError("landlock_restrict_self failed")
    os.close(ruleset_fd)
    _ = allow_network  # ABI v1 has no network hooks; egress stays policy-layer
    return True


# --------------------------------------------------------------------------
# Confined argv for processes that have no sandbox of their own (extension
# servers). The caller gets a wrapped argv or an error, never an unconfined run.
# --------------------------------------------------------------------------


class ConfinementUnavailableError(RuntimeError):
    """No OS confinement can be applied on this host."""


# Linux child bootstrap: load this module by path (no package import), apply
# Landlock, then replace the process with the target. Landlock survives exec.
_LANDLOCK_EXEC = (
    "import importlib.util, os, sys\n"
    "from pathlib import Path\n"
    "spec = importlib.util.spec_from_file_location('confinement', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "module.apply_landlock_in_child(Path(sys.argv[2]), sys.argv[3] == '1', Path(sys.argv[4]))\n"
    "os.execvp(sys.argv[5], sys.argv[5:])\n"
)


def confine_argv(
    argv: list[str], *, workspace: Path, tmpdir: Path, allow_network: bool = False
) -> list[str]:
    """Wrap ``argv`` so the process writes only under ``workspace`` and ``tmpdir``.

    macOS runs the command under a Seatbelt profile written into ``tmpdir``; Linux
    applies Landlock in a bootstrap before exec. Raises ConfinementUnavailableError
    when neither is usable: callers must refuse to launch, never run unconfined.
    """
    if seatbelt_available():
        tmpdir.mkdir(parents=True, exist_ok=True)
        profile = tmpdir / "confine.sb"
        profile.write_text(seatbelt_profile(workspace, allow_network, None, tmpdir))
        return [SEATBELT_BIN, "-f", str(profile), *argv]
    if landlock_supported():
        return [
            sys.executable,
            "-I",
            "-c",
            _LANDLOCK_EXEC,
            str(Path(__file__).resolve()),
            str(workspace),
            "1" if allow_network else "0",
            str(tmpdir),
            *argv,
        ]
    raise ConfinementUnavailableError("no OS confinement is available on this host")
