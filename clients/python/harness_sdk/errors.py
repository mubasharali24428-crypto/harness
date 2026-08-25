"""Error types for harness_sdk.

``ApiError`` mirrors the frozen error envelope from contracts/api-conventions.md:

    {"error": {"code": "<machine_code>", "message": "<human>"}}

Sections built on FastAPI's HTTPException wrap that envelope as
``{"detail": {"error": {...}}}`` — both shapes are parsed (extraction of
section7/src/lib/api.ts ``parseApiError``, which handles exactly these two
plus the plain-string ``detail`` form).
"""

from __future__ import annotations

from typing import Any

__all__ = ["ApiError", "parse_api_error"]


def parse_api_error(status: int, body: Any) -> tuple[str, str]:
    """Pure: turn a non-2xx status + decoded JSON body into (code, message).

    Handles, in order:

    1. direct contract envelope:  ``{"error": {"code", "message"}}``
    2. FastAPI-wrapped envelope:  ``{"detail": {"error": {"code", "message"}}}``
    3. FastAPI string detail:     ``{"detail": "..."}`
    4. anything else (non-dict / undecodable): generic message + ``UNKNOWN``.
    """
    fallback = ("UNKNOWN", f"Request failed with status {status}")
    if not isinstance(body, dict):
        return fallback
    err = body.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "UNKNOWN")
        message = str(err.get("message") or f"Request failed with status {status}")
        return code, message
    detail = body.get("detail")
    if isinstance(detail, dict):
        derr = detail.get("error")
        if isinstance(derr, dict):
            code = str(derr.get("code") or "UNKNOWN")
            message = str(derr.get("message") or f"Request failed with status {status}")
            return code, message
    if isinstance(detail, str) and detail:
        return "UNKNOWN", detail
    return fallback


class ApiError(RuntimeError):
    """A non-2xx API response, carrying the machine ``code`` from the envelope.

    Attributes:
        code: machine-readable error code (e.g. ``PLAN_NOT_APPROVED``),
              ``UNKNOWN`` when the body carried none.
        status: HTTP status number.
        body: raw decoded response body (or ``None`` for non-JSON bodies).
    """

    def __init__(self, message: str, code: str = "UNKNOWN", status: int = 0,
                 body: Any = None, headers: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.body = body
        #: Response headers when the error came from a live HTTP response
        #: (read ``retry-after`` off this to honour server pacing).
        self.headers = headers

    @property
    def body_headers(self) -> Any:
        return self.headers

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"ApiError(status={self.status}, code={self.code!r}, message={str(self)!r})"
