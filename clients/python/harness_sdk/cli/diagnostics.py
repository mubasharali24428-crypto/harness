"""Stable CLI diagnostics and evaluation capture (W5).

Two contracts live here, both deliberately small:

1. **Exit codes.** A script needs to branch on *why* the CLI failed without
   parsing prose. ``USAGE`` is argparse's, ``AUTH`` means the token is missing or
   rejected, ``POLICY`` means the server refused on a rule (approval, role,
   pilot gate), ``TIMEOUT`` means the CLI's own deadline elapsed, ``TRANSPORT``
   means the connection/5xx class and is the only class retried.
2. **One JSON object.** ``--json`` prints exactly one object on stdout. On
   failure that object carries ``error.code``/``message``/``retryable`` and the
   same ``exitCode`` the process returns, so a pipeline never has to scrape
   stderr.

``--capture PATH`` writes the same object atomically (0600) for the evaluation
harness; it is the payload, not a separate log format, so a capture and a pipe
cannot disagree. Tokens never enter either object: the CLI's only token path is
config/`HARNESS_TOKEN`, and nothing here reads it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ..errors import ApiError
from ..runtime import backoff_delay

SCHEMA_VERSION = 1

OK = 0
FAILURE = 1
USAGE = 2
AUTH = 3
POLICY = 4
TIMEOUT = 5
TRANSPORT = 6
INTERRUPTED = 130

POLICY_CODES = frozenset(
    {
        "PLAN_NOT_APPROVED",
        "ROLE_DENIED",
        "PILOT_NOT_ENABLED",
        "NOT_PARTICIPANT",
        "SELF_APPROVAL",
        "PLAN_ALREADY_DECIDED",
        "INTENT_CONFLICT",
        "STALE_INTENT_VERSION",
        "STALE_EDIT",
        "LEASE_HELD",
        "QUEUE_ORDER",
    }
)
AUTH_CODES = frozenset({"UNAUTHENTICATED", "INVALID_SESSION", "INVALID_TOKEN"})
_TRANSPORT_STATUS = frozenset({0, 408, 429, 500, 502, 503, 504})
_BAD_STATES = frozenset({"error", "failed", "execution_failed", "blocked"})


class CliError(RuntimeError):
    """A refusal the user can act on (bad config, ambiguous project, no token)."""


def _auth_message(message: str) -> bool:
    text = message.lower()
    return "not logged in" in text or "no token" in text or "session token" in text


def classify(exc: BaseException) -> tuple[str, int, bool]:
    """(code, exitCode, retryable) for one failure. Pure and total."""
    if isinstance(exc, ApiError):
        code = str(exc.code or "UNKNOWN")
        status = int(exc.status or 0)
        if status == 401 or code in AUTH_CODES:
            return code, AUTH, False
        if code in POLICY_CODES or status == 403:
            return code, POLICY, False
        if status in _TRANSPORT_STATUS or status >= 500:
            return code, TRANSPORT, True
        return code, FAILURE, False
    if isinstance(exc, CliError):
        if _auth_message(str(exc)):
            return "NOT_LOGGED_IN", AUTH, False
        return str(getattr(exc, "code", "") or "CLI_ERROR"), FAILURE, False
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "TIMEOUT", TIMEOUT, True
    if isinstance(exc, (ConnectionError, httpx.TransportError)):
        return "TRANSPORT", TRANSPORT, True
    return "INTERNAL", FAILURE, False


def retryable(exc: BaseException) -> bool:
    return classify(exc)[2]


def run_exit_code(state: str) -> int:
    if state == "timeout":
        return TIMEOUT
    if state in _BAD_STATES:
        return FAILURE
    return OK


def contract(command: str, payload: dict[str, Any], exit_code: int) -> dict[str, Any]:
    """Attach the stable contract keys without mutating the command's payload."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "ok": exit_code == 0,
        "exitCode": exit_code,
        **payload,
    }


def error_payload(command: str, exc: BaseException) -> dict[str, Any]:
    code, exit_code, can_retry = classify(exc)
    error: dict[str, Any] = {"code": code, "message": str(exc), "retryable": can_retry}
    status = getattr(exc, "status", None)
    if isinstance(status, int) and status:
        error["status"] = status
    conflicts = getattr(exc, "conflicts", None)
    if conflicts:
        # Which files moved, and the digest each was reviewed against — enough
        # for a caller to re-review instead of guessing why the write stopped.
        error["conflicts"] = list(conflicts)[:20]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "ok": False,
        "exitCode": exit_code,
        "error": error,
    }


def usage_payload() -> dict[str, Any]:
    """argparse refused the command line before a command existed."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": "cli",
        "ok": False,
        "exitCode": USAGE,
        "error": {"code": "USAGE", "message": "invalid command line", "retryable": False},
    }


def interrupt_payload(command: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "command": command,
        "ok": False,
        "exitCode": INTERRUPTED,
        "error": {"code": "INTERRUPTED", "message": "interrupted", "retryable": False},
    }


def write_capture(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write the payload atomically (0600). The parent directory must exist."""
    target = Path(path).expanduser()
    parent = target.parent
    if not parent.is_dir():
        raise CliError(f"capture directory does not exist: {parent}")
    fd, tmp = tempfile.mkstemp(prefix=".capture-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def reconnect_delay(attempt: int) -> float:
    """Bounded exponential backoff with jitter, shared with the event stream."""
    return backoff_delay(attempt, base_s=0.5, cap_s=8.0)
