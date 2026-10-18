import fnmatch
import os
import re
import shlex
from pathlib import Path


class SecurityPolicyViolationError(Exception):
    """Raised when a requested command or file edit violates the sandbox security policy."""

    pass


class InvalidTaskIdError(SecurityPolicyViolationError):
    """taskId is not a safe single path component (T16)."""


class TaskIdInUseError(Exception):
    """taskId already names a task (live or on disk) and the caller may not take it (T15)."""


TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def validate_task_id(task_id: str) -> str:
    """T16: a taskId becomes a directory name, so it must be one safe path component.

    ``fullmatch`` (not ``match``/``$``) so a trailing newline cannot slip through.
    """
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise InvalidTaskIdError(
            "taskId must be 1-64 chars of [A-Za-z0-9_-] and start with a letter or digit."
        )
    return task_id


class SandboxCommandTimeoutError(Exception):
    """Raised when a sandboxed command exceeds its execution budget."""

    pass


def find_unquoted_shell_syntax(cmd: str) -> str | None:
    """Quote-aware scan for shell features that make first-token allow-listing moot.

    The command runs via create_subprocess_shell, so chaining/piping/substitution
    would let an allow-listed first token execute arbitrary followers
    (`git status && python -c '...'`). One command per exec call: no operators.
    Returns a human-readable reason or None when clean.
    """
    quote = None
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if ch == "\\":
                i += 1
            elif ch == '"':
                quote = None
            elif ch == "`":
                # S1-R03a: /bin/sh expands backticks inside double quotes too.
                return "command substitution `...` is not allowed"
            elif ch == "$" and i + 1 < n and cmd[i + 1] == "(":
                # S1-R03a: same for $(...) — double quotes do NOT suppress it.
                return "command substitution $(...) is not allowed"
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "\\" and i + 1 < n:
                i += 1
            elif ch in ";&|`\n":
                return f"shell control character {ch!r} is not allowed"
            elif ch == "$" and i + 1 < n and cmd[i + 1] == "(":
                return "command substitution $(...) is not allowed"
            elif ch in "<>":
                return f"redirection {ch!r} is not allowed"
        i += 1
    if quote:
        return "unterminated quote"
    return None


ALLOWLIST_BINARIES = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "head",
        "tail",
        "find",
        "grep",
        "rg",
        "pwd",
        "echo",
        "git",
        "pytest",
        "python",
        "python3",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "vitest",
        "jest",
        "cargo",
        "go",
        "ruff",
        "flake8",
        "mypy",
        "black",
        "isort",
        "eslint",
        "prettier",
        "tsc",
    }
)

GIT_ALLOWED_SUBCOMMANDS = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "branch",
        "checkout",
        "switch",
        "add",
        "commit",
        "reset",
        "clean",
        "stash",
        "fetch",
        "pull",
        "rev-parse",
    }
)

BLOCKED_PATTERNS = [
    re.compile(
        r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+(/|~|\.\./)",
        re.IGNORECASE,
    ),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\b(shutdown|reboot|init\s+0|halt)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\b.*(--force|-f|\+[\w/]+)", re.IGNORECASE),
    re.compile(r"\bnpm\s+publish\b", re.IGNORECASE),
    re.compile(r"\btwine\s+upload\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+publish\b", re.IGNORECASE),
    re.compile(r"\b(docker|podman|kubectl|terraform|gcloud|aws|az)\b", re.IGNORECASE),
    re.compile(r"(curl|wget)\b.*\|\s*(sh|bash|zsh|python|perl)", re.IGNORECASE),
    re.compile(r"\b(nc|ncat|socat)\b", re.IGNORECASE),
    re.compile(r"/(etc/(shadow|passwd|sudoers)|proc/kcore|sys/)", re.IGNORECASE),
    # S1-R03a: find(1) can execute an arbitrary follower that first-token
    # validation never sees (`find . -exec curl ... \;`).
    re.compile(r"\bfind\b[^|;]*(-execdir|-exec|-okdir|-ok)\b", re.IGNORECASE),
    # S1-R03b: package installers fetch and execute unvetted code; the
    # pinned_registries config is not enforced at runtime, so block the verbs.
    re.compile(r"\b(pip|pip3)\s+install\b", re.IGNORECASE),
    re.compile(r"\bpython3?\s+-m\s+pip\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+(install|i|ci)\b", re.IGNORECASE),
    re.compile(r"\b(yarn|pnpm)\s+(add|install)\b", re.IGNORECASE),
    re.compile(r"\bnpx\s+(-y|--yes)\b", re.IGNORECASE),
    re.compile(r"\b(yarn|pnpm)\s+dlx\b", re.IGNORECASE),
]

SECRET_PATTERNS = [
    ".env*",
    "*.pem",
    "*.key",
    "*id_rsa*",
    "*id_ed25519*",
    "*secret*",
    "*credential*",
    "*token*",
    "*.pfx",
    "*.p12",
]

SENSITIVE_ENV_PREFIXES = (
    "HARNESS_",  # S1-R03e: assembly config incl. HARNESS_DATABASE_URL creds
    "GITHUB_",
    "OPENAI_",
    "ANTHROPIC_",
    "DEEPSEEK_",
    "LITELLM_",
    "AWS_",
    "GOOGLE_",
    "POSTGRES_",
    "DATABASE_",
    "SECRET_",
    "TOKEN_",
    "KEY_",
)


def is_secret_file(path_str: str) -> bool:
    name = Path(path_str).name
    for pattern in SECRET_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path_str, pattern):
            return True
    return False


def sanitize_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    base_env = dict(os.environ) if env is None else dict(env)
    sanitized = {}
    for k, v in base_env.items():
        k_upper = k.upper()
        if any(k_upper.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
            continue
        if "KEY" in k_upper or "SECRET" in k_upper or "TOKEN" in k_upper or "PASSWORD" in k_upper:
            continue
        sanitized[k] = v
    sanitized["PATH"] = sanitized.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    sanitized["PYTHONUNBUFFERED"] = "1"
    return sanitized


def _expand_globs(token: str, cwd: str | None) -> list[str]:
    """Path spellings this token can reach after shell glob expansion (S1-R03a).

    The shell performs expansion at exec time; policy pre-expands the same
    pattern against the task workspace so checks see the real targets —
    ``db/*/sensitive/init.sql`` must not hide behind ``*``. Returns absolute,
    workspace-relative and symlink-resolved spellings of every match.
    """
    if not any(ch in token for ch in "*?["):
        return []
    import glob as _glob

    base = os.path.abspath(cwd) if cwd else os.getcwd()
    pattern = token if os.path.isabs(token) else os.path.join(base, token)
    try:
        matches = _glob.glob(pattern, recursive=True)
    except re.error:
        return []
    expanded: list[str] = []
    for match in matches:
        expanded.append(match)
        try:
            expanded.append(os.path.relpath(match, base))
        except ValueError:  # pragma: no cover — no common path (other volumes)
            pass
        expanded.append(os.path.realpath(match))
    return expanded


def _symlink_spellings(token: str, cwd: str | None) -> list[str]:
    """T17: where a token that names a workspace symlink really points.

    ``realpath`` resolves every component, so a link in the middle of the path
    (``innocent/x.txt`` with ``innocent -> secrets``) is caught as well as a
    link at the end. Targets inside the workspace come back workspace-relative
    (matching how forbidden paths are written); targets outside come back absolute.
    """
    if not cwd or "\0" in token:
        return []
    base = os.path.abspath(cwd)
    candidate = os.path.normpath(os.path.join(base, token))
    if not os.path.lexists(candidate):
        return []
    real, real_base = os.path.realpath(candidate), os.path.realpath(base)
    if real == real_base or real.startswith(real_base + os.sep):
        return [os.path.relpath(real, real_base)]
    return [real]


def validate_command(
    command: str,
    forbidden_paths: list[str] | None = None,
    cwd: str | None = None,
) -> None:
    cmd_stripped = command.strip()
    if not cmd_stripped:
        raise SecurityPolicyViolationError("Empty command is not allowed.")

    for pattern in BLOCKED_PATTERNS:
        if pattern.search(cmd_stripped):
            raise SecurityPolicyViolationError(
                f"Command contains forbidden pattern: '{pattern.pattern}'"
            )

    shell_reason = find_unquoted_shell_syntax(cmd_stripped)
    if shell_reason:
        raise SecurityPolicyViolationError(shell_reason)

    try:
        tokens = shlex.split(cmd_stripped)
    except ValueError as e:
        raise SecurityPolicyViolationError(f"Malformed shell command: {e}") from e

    if not tokens:
        raise SecurityPolicyViolationError("No executable tokens found in command.")

    for token in tokens:
        # S1-R03a: unbraced, braced and mid-token forms all resolve the same
        # variable name; match on the name, not on the spelling.
        for name in re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", token):
            if name.upper().startswith(SENSITIVE_ENV_PREFIXES):
                raise SecurityPolicyViolationError(
                    f"Reference to sensitive environment variable '{name}' is blocked."
                )

    binary = Path(tokens[0]).name

    if binary not in ALLOWLIST_BINARIES:
        raise SecurityPolicyViolationError(
            f"Binary '{binary}' is not permitted in sandbox allow-list."
        )

    if binary == "git" and len(tokens) > 1:
        subcmd = tokens[1]
        if subcmd not in GIT_ALLOWED_SUBCOMMANDS:
            raise SecurityPolicyViolationError(
                f"git subcommand '{subcmd}' is not permitted in sandbox."
            )

    # S1-R03a: check literal tokens AND every path a glob token can reach.
    checked_paths = list(tokens)
    for token in tokens:
        checked_paths.extend(_expand_globs(token, cwd))
        checked_paths.extend(_symlink_spellings(token, cwd))

    for token in checked_paths:
        if is_secret_file(token):
            raise SecurityPolicyViolationError(
                f"Access to secret file or pattern '{token}' is blocked."
            )

    if forbidden_paths:
        for fpath in forbidden_paths:
            fpath_clean = fpath.strip()
            if not fpath_clean:
                continue
            for token in checked_paths:
                if fpath_clean in token or fnmatch.fnmatch(token, f"*{fpath_clean}*"):
                    raise SecurityPolicyViolationError(
                        f"Command accesses forbidden path: '{fpath_clean}'"
                    )


def validate_path_edit(target_path: str, forbidden_paths: list[str] | None = None) -> None:
    path_obj = Path(target_path)
    if ".." in path_obj.parts:
        raise SecurityPolicyViolationError(f"Path traversal detected in target path: {target_path}")

    if is_secret_file(target_path):
        raise SecurityPolicyViolationError(
            f"Modifying secret file pattern '{target_path}' is strictly blocked."
        )

    if forbidden_paths:
        for fpath in forbidden_paths:
            fpath_clean = fpath.strip()
            if not fpath_clean:
                continue
            if (
                target_path == fpath_clean
                or target_path.startswith(fpath_clean.rstrip("/") + "/")
                or fnmatch.fnmatch(target_path, fpath_clean)
            ):
                raise SecurityPolicyViolationError(
                    f"Modifying file in forbidden path '{fpath_clean}' is strictly blocked."
                )


class SandboxPolicy:
    """Boolean facades over the module-level validators."""

    @staticmethod
    def is_command_allowed(command: str, forbidden_paths: list[str] | None = None) -> bool:
        try:
            validate_command(command, forbidden_paths)
        except SecurityPolicyViolationError:
            return False
        return True

    @staticmethod
    def is_secret_file(path_str: str) -> bool:
        return is_secret_file(path_str)

    @staticmethod
    def sanitize_env(env: dict[str, str] | None = None) -> dict[str, str]:
        return sanitize_environment(env)

    @staticmethod
    def is_path_forbidden(path_str: str, forbidden_paths: list[str]) -> bool:
        return validate_path_bool(path_str, forbidden_paths)


def validate_path_bool(path_str: str, forbidden_paths: list[str]) -> bool:
    clean = path_str.strip()
    if not clean or not forbidden_paths:
        return False
    for fpath in forbidden_paths:
        fpath_clean = fpath.strip()
        if not fpath_clean:
            continue
        if (
            fpath_clean in clean
            or fnmatch.fnmatch(clean, f"*{fpath_clean}*")
            or fnmatch.fnmatch(clean, f"{fpath_clean}*")
        ):
            return True
    return False
