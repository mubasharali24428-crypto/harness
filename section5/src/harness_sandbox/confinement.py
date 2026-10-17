"""F6r — OS-level confinement for sandboxed command execution.

macOS: Seatbelt profiles via /usr/bin/sandbox-exec.
Linux: Landlock via ctypes (explicit requests fail closed when unsupported —
see S1-R03d; `auto` picks the best available mode).

Layering contract: policy.py decides WHAT may run; this module decides WHERE it
may touch the machine. Reads stay broad (toolchains need system SDKs), writes are
confined to the task workspace + system temp, network is configurable.
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


def landlock_supported() -> bool:
    if sys.platform != "linux":
        return False
    try:
        parts = platform_release().split(".")
        major, minor = int(parts[0]), int(parts[1])
        return major > 5 or (major == 5 and minor >= 13)
    except Exception:  # noqa: BLE001 — unknown kernels: assume unsupported
        return False


# --------------------------------------------------------------------------
# Seatbelt (macOS)
# --------------------------------------------------------------------------


def seatbelt_profile(workspace: Path, allow_network: bool) -> str:
    """deny-default profile; generous reads, writes confined to ws+/tmp."""
    ws = str(Path(workspace).resolve())
    tmp = tempfile.gettempdir()
    priv_tmp = tmp if tmp.startswith("/private") else "/private" + tmp
    network = "(allow network*)\n(allow system-socket)" if allow_network else "(deny network*)"
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process-exec)\n"
        "(allow process-fork)\n"
        "(allow file-read*)\n"
        f'(allow file-write* (subpath "{ws}") (subpath "{tmp}") (subpath "{priv_tmp}") '
        '(subpath "/dev/null"))\n'
        "(allow mach-lookup)\n"
        "(allow sysctl-read)\n"
        "(allow ipc-posix-shm)\n"
        "(allow iokit-get-properties)\n"
        f"{network}\n"
    )


def write_seatbelt_profile(workspace: Path, allow_network: bool, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "sb.sb"
    path.write_text(seatbelt_profile(workspace, allow_network))
    return path


def seatbelt_command(command: str, profile_path: Path) -> list[str]:
    return [SEATBELT_BIN, "-f", str(profile_path), "/bin/sh", "-c", command]


# --------------------------------------------------------------------------
# Linux Landlock (best-effort). Applied inside the forked child via preexec_fn.
# We only HANDLE write/remove/make rights, so reads+execute stay unrestricted
# by construction; writes get explicit allow rules for workspace + temp.
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


def apply_landlock_in_child(workspace: Path, allow_network: bool) -> bool:
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
    for p in {str(Path(workspace).resolve()), tempfile.gettempdir()}:
        try:
            add_beneath(fs_fd(p), _HANDLED)
        except OSError:  # unreadable temp dir variants etc.
            continue

    if libc.syscall(_LL_RESTRICT_SELF, ruleset_fd, 0) != 0:
        raise OSError("landlock_restrict_self failed")
    os.close(ruleset_fd)
    _ = allow_network  # ABI v1 has no network hooks; egress stays policy-layer
    return True
