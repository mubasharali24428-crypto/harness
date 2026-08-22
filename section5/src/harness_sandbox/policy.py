import fnmatch
import os
import re
import shlex
from pathlib import Path


class SecurityPolicyViolationError(Exception):
    """Raised when a requested command or file edit violates the sandbox security policy."""

    pass


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


def validate_command(command: str, forbidden_paths: list[str] | None = None) -> None:
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
        if token.startswith("$"):
            var_name = token.lstrip("$").split("=")[0].upper()
            if any(var_name.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
                raise SecurityPolicyViolationError(
                    f"Reference to sensitive environment variable '{token}' is blocked."
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

    for token in tokens:
        if is_secret_file(token):
            raise SecurityPolicyViolationError(
                f"Access to secret file or pattern '{token}' is blocked."
            )

    if forbidden_paths:
        for fpath in forbidden_paths:
            fpath_clean = fpath.strip()
            if not fpath_clean:
                continue
            for token in tokens:
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
