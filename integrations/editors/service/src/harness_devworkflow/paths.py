"""Path validation for lane-owned workspace file operations.

Defense in depth mirrors Section 5's rules (validate_path_edit in
harness_sandbox.policy): relative paths only, no traversal, no absolute
targets, forbidden-path prefixes rejected. The lane service never opens a
path it has not validated here and resolved inside the session workspace.
"""

from __future__ import annotations

import unicodedata

MAX_PATH_LEN = 512
_CONTROL_MAX = 0x1F


class InvalidPathError(ValueError):
    """Raised when a path fails lane-side validation."""


class ForbiddenPathError(PermissionError):
    """Raised when a path falls under a session's forbidden_paths."""


def normalize_rel_path(raw: str) -> str:
    """Return a canonical relative path or raise InvalidPathError.

    Rules: non-empty after trim; no NUL/control characters; no backslashes;
    no absolute paths; no "." / ".." segments; no empty segments after
    collapsing doubled slashes; bounded length.
    """
    if raw is None:
        raise InvalidPathError("path is required")
    text = unicodedata.normalize("NFC", raw).strip()
    if not text:
        raise InvalidPathError("path is empty")
    if len(text) > MAX_PATH_LEN:
        raise InvalidPathError(f"path exceeds {MAX_PATH_LEN} characters")
    for ch in text:
        if ord(ch) <= _CONTROL_MAX or ord(ch) == 0x7F:
            raise InvalidPathError("path contains control characters")
    if "\\" in text:
        raise InvalidPathError("backslashes are not allowed in paths")
    if text.startswith("/"):
        raise InvalidPathError("absolute paths are not allowed")
    collapsed = "/".join(seg for seg in text.split("/") if seg)
    segments = collapsed.split("/")
    for seg in segments:
        if seg in (".", ".."):
            raise InvalidPathError("path traversal is not allowed")
        if seg.casefold() == ".git":
            # .git/config (core.fsmonitor) and hooks run code on this host's own git calls.
            raise ForbiddenPathError("git internals are not accessible")
        if seg == "":
            raise InvalidPathError("path contains empty segments")
    return collapsed


def path_is_forbidden(path: str, forbidden_paths: list[str] | None) -> bool:
    """True when `path` equals or lives under any forbidden prefix."""
    if not forbidden_paths:
        return False
    normalized = normalize_rel_path(path)
    for entry in forbidden_paths:
        try:
            prefix = normalize_rel_path(entry)
        except InvalidPathError:
            continue
        # Case-insensitive on purpose: macOS/Windows checkouts treat Secrets/ == secrets/.
        n, pre = normalized.casefold(), prefix.casefold()
        if n == pre or n.startswith(pre + "/"):
            return True
    return False


def check_path(path: str, forbidden_paths: list[str] | None = None) -> str:
    """Validate + enforce forbidden paths; returns the normalized path."""
    normalized = normalize_rel_path(path)
    if path_is_forbidden(normalized, forbidden_paths):
        raise ForbiddenPathError(f"modifying forbidden path '{normalized}' is blocked")
    return normalized
