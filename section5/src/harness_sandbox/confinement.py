"""F6r — OS-level confinement for sandboxed command execution.

macOS: Seatbelt profiles via /usr/bin/sandbox-exec.
Linux: Landlock via ctypes (explicit requests fail closed when unsupported —
see S1-R03d; `auto` picks the best available mode).

Layering contract: policy.py decides WHAT may run; this module decides WHERE it
may touch the machine. Writes are confined to the task workspace + its private
tmp; reads are confined to an allow-list (see S-01 below); network is
configurable.

S-01 (Linux): Landlock rules GRANT rights, they never deny them, so a
forbidden path cannot be denied by a rule that excludes it — it can only be
denied by never granting READ_FILE on it. That turns "readable except these
paths" into an allow-list, which is what ``landlock_read_allowlist`` builds:
readable roots are enumerated, and every directory that CONTAINS a forbidden
path is granted ``READ_DIR`` only and split one level down (a path-beneath
rule covers every descendant, so the containing directory cannot be granted as
a whole). Everything outside the allow-list is unreadable, which is also what
isolates sibling task workspaces on Linux.
"""

import json
import logging
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SEATBELT_BIN = "/usr/bin/sandbox-exec"


def platform_release() -> str:
    import platform

    return platform.release()


def seatbelt_available() -> bool:
    """Return true only when the host can actually enter a Seatbelt profile."""
    if sys.platform != "darwin" or not Path(SEATBELT_BIN).exists():
        return False
    try:
        probe = subprocess.run(
            [SEATBELT_BIN, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


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
                preexec_fn=lambda: apply_landlock_in_child(
                    Path(ws), False, extra_read_roots=[Path(sys.executable).parent]
                ),
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


def _seatbelt_forbidden_rules(workspace: Path, forbidden_paths: list[str] | None) -> str:
    """Deny reads AND writes for policy forbidden paths inside the workspace.

    S-01: the string policy is UX; this is the enforced boundary. Later SBPL
    rules win, so these denials are emitted AFTER the broad allow rules.
    """
    if not forbidden_paths:
        return ""
    ws = Path(workspace).resolve()
    lines: list[str] = []
    for raw in forbidden_paths:
        p = Path(raw)
        target = (p if p.is_absolute() else ws / p).resolve()
        escaped = str(target).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'(deny file-read* file-write* (subpath "{escaped}"))')
        lines.append(f'(deny file-read* file-write* (literal "{escaped}"))')
    return "".join(line + "\n" for line in lines)


def seatbelt_profile(
    workspace: Path,
    allow_network: bool,
    task_dir: Path | None = None,
    tmpdir: Path | None = None,
    forbidden_paths: list[str] | None = None,
    denied_task_dirs: list[Path] | None = None,
    write_roots: list[Path] | None = None,
) -> str:
    """deny-default profile; writes confined to the task workspace + its private tmp.

    T7b: the shared system temp dir is NOT writable, and file CONTENTS under the
    storage root (every task's workspace) are unreadable except this task's own
    directory. Metadata (stat) stays readable so cwd resolution keeps working.
    ``write_roots`` (M03) narrows the writable part of the workspace to the run
    intent's scope; ``[]`` leaves none of it writable.
    """
    roots = [workspace] if write_roots is None else write_roots
    writable = [f'(subpath "{Path(r).resolve()}")' for r in roots]
    if tmpdir is not None:
        writable.append(f'(subpath "{Path(tmpdir).resolve()}")')
    writable.append('(subpath "/dev/null")')
    # Sibling isolation is expressed as per-task denies, NOT deny-parent +
    # re-allow-own: SBPL's path precedence lets a later deny of a child be
    # defeated by that re-allow pair, which silently re-opened forbidden dirs.
    # G2: credential trees are denied outright, on every profile, whether or
    # not per-task denies exist (same SBPL caveat as below: deny of a subtree
    # under an allow pair — file-read-data deny wins for reads under it).
    cred_denies = [d for d in _credential_deny_paths()]
    isolation = "".join(
        f'(deny file-read-data (subpath "{Path(d).resolve()}"))\n'
        for d in (*cred_denies, *(denied_task_dirs or ()))
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
        f"{_seatbelt_forbidden_rules(workspace, forbidden_paths)}"
        "(allow mach-lookup)\n"
        "(allow sysctl-read)\n"
        "(allow ipc-posix-shm)\n"
        "(allow iokit-get-properties)\n"
        f"{network}\n"
    )


def write_seatbelt_profile(
    workspace: Path,
    allow_network: bool,
    target_dir: Path,
    tmpdir: Path | None = None,
    forbidden_paths: list[str] | None = None,
    denied_task_dirs: list[Path] | None = None,
) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "sb.sb"
    path.write_text(
        seatbelt_profile(
            workspace,
            allow_network,
            target_dir,
            tmpdir,
            forbidden_paths,
            denied_task_dirs,
        )
    )
    return path


def seatbelt_command(command: str, profile_path: Path) -> list[str]:
    return [SEATBELT_BIN, "-f", str(profile_path), "/bin/sh", "-c", command]


# --------------------------------------------------------------------------
# Linux Landlock. Applied inside the forked child via preexec_fn as TWO
# STACKED rulesets:
#   1. writes — the task workspace + its private tmp (never the shared temp)
#   2. reads  — an allow-list; forbidden paths are not readable (S-01)
# Landlock intersects stacked rulesets, and a ruleset only restricts the
# rights it HANDLES, so the two rulesets are independent.
# --------------------------------------------------------------------------

_LL_CREATE_RULESET = 444
_LL_ADD_RULE = 445
_LL_RESTRICT_SELF = 446
_LL_RULE_PATH_BENEATH = 1
_LL_CREATE_RULESET_VERSION = 1  # create_ruleset flag: return the ABI version

# Access bits per landlock ABI v1 (LL_TRUNCATE: ABI 3).
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
LL_TRUNCATE = 1 << 14

_HANDLED = (
    LL_WRITE_FILE | LL_REMOVE_DIR | LL_REMOVE_FILE | LL_MAKE_CHAR | LL_MAKE_DIR
    | LL_MAKE_REG | LL_MAKE_SOCK | LL_MAKE_FIFO | LL_MAKE_BLOCK | LL_MAKE_SYM
)

# The read ruleset handles exactly the rights the allow-list grants.
_READ_HANDLED = LL_READ_FILE | LL_READ_DIR | LL_EXECUTE
_READ_DIR_RIGHTS = LL_READ_FILE | LL_READ_DIR | LL_EXECUTE
_READ_FILE_RIGHTS = LL_READ_FILE | LL_EXECUTE

# Readable roots on Linux. Toolchains need the system trees and the user
# cache; anything not listed (another task's workspace, /var, another user's
# home) is NOT readable. Deny-by-default is the S-01 fix, and it is what
# isolates sibling task workspaces without a per-task deny list.
_LINUX_READ_ROOTS: tuple[str, ...] = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/opt",
    "/etc",
    "/proc",
    "/dev",
    "/sys",
    "/run",
)


# G2 (p04-sandbox-review §5): credential material the read allow-list must
# never expose, even when a granted root contains it (HOME, /etc, /run).
# Seatbelt expresses these as per-path file-read-data denies; Landlock as
# path-beneath subtracts from every granted root (a rule on a subtree narrows
# the parent's rule). "~" expands from the environment's HOME at build time.
CREDENTIAL_DENIES: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.config/gh",
    "~/.gnupg",
    "~/Library/Keychains",
    "/etc/ssh",
    "/etc/kubernetes",
    "/run/secrets",
    "/var/run/secrets",
)


def _credential_deny_paths() -> list[Path]:
    """Existing credential trees on this host, resolved (missing ones skipped)."""
    home = os.environ.get("HOME")
    out: list[Path] = []
    for raw in CREDENTIAL_DENIES:
        expanded = Path(raw.replace("~", home)) if (raw.startswith("~") and home) else Path(raw)
        if expanded.exists():
            out.append(_resolved(expanded))
    return out


def _resolved(path: Path) -> Path:
    """Absolute + symlink-resolved path; tolerant of a missing target."""
    try:
        return Path(path).resolve()
    except OSError:  # pragma: no cover — resolve() is non-strict by default
        return Path(path).absolute()


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _read_rules_for(
    path: Path, denies: list[Path], root: Path
) -> list[tuple[Path, int]]:
    """Landlock read/exec rules for one readable root, forbidden paths removed.

    A rule on a directory covers every descendant, so a directory that
    CONTAINS a forbidden path cannot be granted as a whole: it gets READ_DIR
    (listing stays allowed, as on Seatbelt) and is split one level down.
    """
    if any(_within(path, deny) for deny in denies):
        return []  # the path itself is forbidden (or lives under one)
    if not _within(path, root):
        return []  # a symlink out of the tree: its target has its own rule
    if path.is_dir():
        if not any(deny != path and _within(deny, path) for deny in denies):
            return [(path, _READ_DIR_RIGHTS)]
        rules: list[tuple[Path, int]] = [(path, LL_READ_DIR)]
        try:
            children = sorted(path.iterdir())
        except OSError:  # unreadable directory: keep the listing rule only
            return rules
        for child in children:
            rules.extend(_read_rules_for(_resolved(child), denies, root))
        return rules
    return [(path, _READ_FILE_RIGHTS)]


def landlock_read_allowlist(
    workspace: Path,
    tmpdir: Path | None = None,
    forbidden_paths: list[str] | None = None,
    extra_roots: list[Path] | None = None,
) -> list[tuple[Path, int]]:
    """The (path, rights) rules of the Landlock read/exec ruleset.

    Pure function: no syscall, so the allow-list can be unit-tested on any
    platform. ``forbidden_paths`` are workspace-relative unless absolute, and
    are matched by resolved path, so a symlink that points at a forbidden file
    is denied as well.
    """
    ws = _resolved(workspace)
    denies: list[Path] = []
    for raw in forbidden_paths or []:
        candidate = Path(raw)
        denies.append(
            _resolved(candidate if candidate.is_absolute() else ws / candidate)
        )
    # G2: credential trees are unreadable even inside a granted root.
    denies.extend(_credential_deny_paths())

    roots: list[Path] = [ws]
    if tmpdir is not None:
        roots.append(_resolved(tmpdir))
    home = os.environ.get("HOME")
    if home:
        roots.append(_resolved(Path(home)))
    for raw in _LINUX_READ_ROOTS:
        candidate = Path(raw)
        if candidate.exists():
            roots.append(_resolved(candidate))
    for extra in extra_roots or []:
        roots.append(_resolved(extra))

    rules: list[tuple[Path, int]] = []
    seen: set[tuple[str, int]] = set()
    for root in roots:
        # "/" is never a scoped root. Containers started with `--user` and no
        # passwd entry get HOME="/" (Docker's default), which would otherwise
        # turn the allow-list into "read everything".
        if root == Path("/"):
            continue
        if not root.exists():
            continue
        for path, rights in _read_rules_for(root, denies, root):
            key = (str(path), rights)
            if key in seen:
                continue
            seen.add(key)
            rules.append((path, rights))
    return rules


def apply_landlock_in_child(
    workspace: Path,
    allow_network: bool,
    tmpdir: Path | None = None,
    forbidden_paths: list[str] | None = None,
    extra_read_roots: list[Path] | None = None,
    write_roots: list[Path] | None = None,
) -> bool:
    """Runs INSIDE the forked child (preexec_fn). Returns True when enforced.

    S1-R03d: failure RAISES — subprocess propagates preexec_fn exceptions to
    the parent's ``await create_subprocess_shell``, so a requested-but-failing
    landlock aborts the spawn instead of silently running unconfined.
    """
    import ctypes
    import ctypes.util

    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

    def create_ruleset(handled: int) -> int:
        attr = struct.pack("Q", handled)
        ruleset_fd = libc.syscall(_LL_CREATE_RULESET, attr, len(attr), 0)
        if ruleset_fd < 0:
            raise OSError("landlock_create_ruleset failed")
        return ruleset_fd

    def add_beneath(ruleset_fd: int, parent_fd: int, allowed: int) -> None:
        # struct landlock_path_beneath_attr is packed: u64 allowed_access, s32 parent_fd.
        rule = struct.pack("=Qi", allowed, parent_fd)
        # flags must be passed (0): left out, the kernel reads a stale register and says EINVAL.
        if libc.syscall(_LL_ADD_RULE, ruleset_fd, _LL_RULE_PATH_BENEATH, rule, 0) != 0:
            raise OSError("landlock_add_rule failed")

    O_PATH = os.O_PATH | os.O_CLOEXEC

    def fs_fd(path: Path) -> int:
        return os.open(str(path), O_PATH)

    def restrict_self(ruleset_fd: int) -> None:
        # landlock_restrict_self(2) requires no_new_privs; without it the syscall
        # fails with EPERM and the whole boundary silently looked "unsupported".
        PR_SET_NO_NEW_PRIVS = 38
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError("prctl(PR_SET_NO_NEW_PRIVS) failed")
        if libc.syscall(_LL_RESTRICT_SELF, ruleset_fd, 0) != 0:
            raise OSError("landlock_restrict_self failed")

    # ---- ruleset 1: writes (the workspace + this task's private tmp)
    handled = _HANDLED
    if libc.syscall(_LL_CREATE_RULESET, None, 0, _LL_CREATE_RULESET_VERSION) >= 3:
        handled |= LL_TRUNCATE  # ABI 3+: truncate(2) by path is a write too
    write_ruleset = create_ruleset(handled)
    # T7b: writes only to the workspace and this task's private tmp (never the shared temp).
    # M03: or only to the run intent's scope inside it, when the caller narrows it.
    writable = {_resolved(p) for p in ([workspace] if write_roots is None else write_roots)}
    if tmpdir is not None:
        writable.add(_resolved(tmpdir))
    for p in writable:
        try:
            add_beneath(write_ruleset, fs_fd(p), handled)
        except OSError:  # unreadable temp dir variants etc.
            continue
    try:
        add_beneath(write_ruleset, fs_fd(Path("/dev/null")), LL_WRITE_FILE)
    except OSError:
        pass  # as Seatbelt: tools write to /dev/null
    restrict_self(write_ruleset)
    os.close(write_ruleset)

    # ---- ruleset 2: reads (S-01). Stacked on top of the write ruleset; the
    # two are independent because each only restricts the rights it handles.
    read_ruleset = create_ruleset(_READ_HANDLED)
    for path, rights in landlock_read_allowlist(
        workspace, tmpdir, forbidden_paths, extra_read_roots
    ):
        try:
            add_beneath(read_ruleset, fs_fd(path), rights)
        except OSError:  # vanished between the walk and the rule
            continue
    restrict_self(read_ruleset)
    os.close(read_ruleset)

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
# The target's own directory is added to the readable roots only when it is
# outside every root the allow-list already covers (see exec_read_roots).
_LANDLOCK_EXEC = (
    "import importlib.util, json, os, sys\n"
    "from pathlib import Path\n"
    "spec = importlib.util.spec_from_file_location('confinement', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "_writes = json.loads(sys.argv[5])\n"
    "_roots = module.exec_read_roots(sys.argv[6])\n"
    "# G2: the parent's /proc/<ppid>/environ is NOT denied here — denying\n"
    "# beneath /proc forces per-child enumeration (proc/<pid>/cwd stats EPERM\n"
    "# for other owners) and breaks the ruleset build. The leak is closed at\n"
    "# the source instead: byo_runner spawns the gate with a sanitized env, so\n"
    "# the gate process carries no token for a confined child to read.\n"
    "module.apply_landlock_in_child(\n"
    "    Path(sys.argv[2]), sys.argv[3] == '1', Path(sys.argv[4]), None, _roots,\n"
    "    None if _writes is None else [Path(p) for p in _writes],\n"
    ")\n"
    "os.execvp(sys.argv[6], sys.argv[6:])\n"
)


def exec_read_roots(program: str) -> list[Path]:
    """Extra read roots needed to execute ``program`` itself.

    The previous rule appended the program's grandparent when its directory was
    named ``bin``, so running ``/bin/sh`` granted ``/`` as readable — every
    shell-gated command could read the entire filesystem. A program already
    inside the system roots (or HOME) needs nothing extra; only a tool outside
    all of them gets its own directory.
    """
    directory = _resolved(Path(program)).parent
    covered = [Path(p) for p in _LINUX_READ_ROOTS]
    home = os.environ.get("HOME")
    if home:
        covered.append(_resolved(Path(home)))
    if directory == Path("/"):
        return []  # never grant the filesystem root
    if any(_within(directory, root) for root in covered):
        return []
    return [directory]


def confine_argv(
    argv: list[str],
    *,
    workspace: Path,
    tmpdir: Path,
    allow_network: bool = False,
    write_roots: list[Path] | None = None,
) -> list[str]:
    """Wrap ``argv`` so the process writes only under ``workspace`` and ``tmpdir``.

    macOS runs the command under a Seatbelt profile written into ``tmpdir``; Linux
    applies Landlock in a bootstrap before exec. Raises ConfinementUnavailableError
    when neither is usable: callers must refuse to launch, never run unconfined.
    ``write_roots`` replaces the workspace as the writable set (M03 intent scope).
    """
    if seatbelt_available():
        tmpdir.mkdir(parents=True, exist_ok=True)
        profile = tmpdir / "confine.sb"
        profile.write_text(
            seatbelt_profile(workspace, allow_network, None, tmpdir, write_roots=write_roots)
        )
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
            json.dumps(None if write_roots is None else [str(p) for p in write_roots]),
            *argv,
        ]
    raise ConfinementUnavailableError("no OS confinement is available on this host")
