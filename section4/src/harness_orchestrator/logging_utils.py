"""Observability primitives for the orchestrator (Wave 8 node A).

Single-line JSON logging + a contextvars-based request id. Consumed by the
RequestContextMiddleware /metrics machinery in ``harness_orchestrator.api``
and wired at deploy time by ``deploy/runners/services.py::app_orchestrator``
(env knob ``HARNESS_LOG_FORMAT=json|console``, default ``json``).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"

request_id_ctxvar: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "harness_request_id", default=None
)


def set_request_id(value: str | None) -> contextvars.Token[str | None]:
    """Bind the current logical request id; returns the token to reset with."""
    return request_id_ctxvar.set(value)


def get_request_id() -> str | None:
    """Current request id, or None outside any request."""
    return request_id_ctxvar.get()


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    """Restore the previous request id (middleware finally-block hygiene)."""
    request_id_ctxvar.reset(token)


@contextmanager
def request_id_scope(value: str) -> Iterator[str]:
    """Context-manager sugar for tests/tools: bind, yield, always reset."""
    token = set_request_id(value)
    try:
        yield value
    finally:
        reset_request_id(token)


class JsonFormatter(logging.Formatter):
    """Emit ONE JSON object per log line.

    Common keys on every record: ``ts`` (UTC ISO-8601), ``level``, ``logger``,
    ``msg``. ``requestId`` is always present (null outside a request).
    ``method`` / ``path`` / ``status`` / ``durMs`` appear ONLY on records that
    carry them, so plain uvicorn/asyncio records stay small:

        {"ts":"...","level":"INFO","logger":"uvicorn.error","msg":"...","requestId":null}

    Records logged by the request middleware add the request keys:

        {...,"requestId":"a1b2c3d4e5f6","method":"GET",
         "path":"/api/v1/orchestrator/runs/{runId}/status","status":200,"durMs":3.21}
    """

    # LogRecord attr -> JSON key (camelCase wire names, snake_case attrs).
    # M20-T2: ``user`` carries the sha256[:12] ANONYMIZED principal label
    # emitted by deploy/runners/observability.RequestContextMiddleware;
    # records without the attr (plain logs, orchestrator's own middleware)
    # simply omit the key.
    _EXTRA_KEYS: tuple[tuple[str, str], ...] = (
        ("method", "method"),
        ("path", "path"),
        ("status_code", "status"),
        ("duration_ms", "durMs"),
        ("user", "user"),
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "requestId": getattr(record, "request_id", None),
        }
        for attr, key in self._EXTRA_KEYS:
            value = getattr(record, attr, None)
            if value is not None and not (attr == "user" and value == ""):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single-line format for local dev (HARNESS_LOG_FORMAT=console)."""

    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%H:%M:%S"
        )


_HARNESS_HANDLER_ATTR = "_harness_obs_handler"


def configure_logging(fmt: str = "json", level: str | int | None = None) -> None:
    """Install harness formatting on the ROOT logger. Idempotent.

    ``fmt`` is ``json`` (default) or ``console``; anything else raises
    ValueError. Uvicorn's own loggers (uvicorn, uvicorn.error, uvicorn.access)
    are pointed at the root formatter too, so access/error lines come out in
    the same shape as application lines. Safe to call again: prior harness
    handlers are replaced instead of stacked.
    """
    chosen = (fmt or "json").strip().lower()
    if chosen not in {"json", "console"}:
        raise ValueError(f"log format must be 'json' or 'console', got {fmt!r}")

    root = logging.getLogger()
    if level is None:
        level = os.environ.get("HARNESS_LOG_LEVEL", "INFO")
    root.setLevel(level)

    for existing in list(root.handlers):
        if getattr(existing, _HARNESS_HANDLER_ATTR, False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if chosen == "json" else ConsoleFormatter())
    setattr(handler, _HARNESS_HANDLER_ATTR, True)
    root.addHandler(handler)

    # Route uvicorn's loggers through the root handler so EVERYTHING the
    # service prints shares one format (their records get requestId=null).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
