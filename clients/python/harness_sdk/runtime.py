"""Shared transport runtime for harness_sdk.

Handwritten layer every generated client rides on:

- bearer-token auth header,
- frozen error-envelope parsing (:mod:`harness_sdk.errors`),
- retries with exponential backoff + full jitter for transient failures,
- WebSocket event streaming with single-use ws-ticket acquisition,
  X-WS-Fingerprint presentation, server-driven replay and client-side
  seq dedupe.

EXTRACTION NOTES (mission M19-N5 law: reuse, don't duplicate):

- ``parse_api_error`` (errors.py) is the Python extraction of
  section7/src/lib/api.ts ``parseApiError`` — same three accepted shapes.
- ``backoff_delay``/``backoff_ceiling`` below extract
  section7/src/lib/backoff.ts ``BACKOFF_BASE_DELAY_MS`` (1000),
  ``BACKOFF_MAX_DELAY_MS`` (30_000), ``exponentialDelay`` and
  ``backoffDelay`` (nominal doubling capped at 30s; scheduled wait drawn
  uniformly from [0, nominal] — full jitter).
- ``WsEventStream`` extracts section7/src/lib/useEventStream.ts
  ``EventStreamController``: single-use ticket minting on EVERY connect
  (H8), fingerprint presentation (M15-W5-S2/M18-C5), ``afterSeq``
  resumption from the highest seq seen, seq dedupe guarding the
  reconnect overlap race (``mergeEvents``), guarded frame parsing
  (malformed frames dropped, never fatal), exponential-backoff
  reconnect with the counter resetting on successful open, and the
  bus-overflow close code 1013 triggering an immediate replaying
  reconnect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import queue
import random
import threading
import time
from typing import Any, AsyncIterator, Iterator

import httpx

from .errors import ApiError

logger = logging.getLogger("harness_sdk")

__all__ = [
    "BACKOFF_BASE_S",
    "BACKOFF_CAP_S",
    "SDK_USER_AGENT",
    "SERVICE_PORTS",
    "HarnessHTTP",
    "WsEventStream",
    "backoff_ceiling",
    "backoff_delay",
]

#: Extraction of section7/src/lib/backoff.ts constants (H-012).
BACKOFF_BASE_S = 1.0   # BACKOFF_BASE_DELAY_MS  = 1000
BACKOFF_CAP_S = 30.0   # BACKOFF_MAX_DELAY_MS  = 30_000

SDK_USER_AGENT = "harness-sdk/1.0.0rc1"

#: Section service name -> default local port (deploy/docker-compose.yml).
SERVICE_PORTS: dict[str, int] = {
    "identity": 8001,
    "github": 8002,
    "router": 8003,
    "orchestrator": 8004,
    "sandbox": 8005,
    "meeting": 8006,
    "review": 8008,
}


def base_url_for(service: str) -> str:
    """Default base URL for a section: HARNESS_<NAME>_URL env var, else
    ``http://localhost:<default port>``."""
    env = os.getenv(f"HARNESS_{service.upper()}_URL")
    if env:
        return env.rstrip("/")
    return f"http://localhost:{SERVICE_PORTS[service]}"


# ---------------------------------------------------------------------------
# Backoff (extraction of section7/src/lib/backoff.ts)
# ---------------------------------------------------------------------------

def backoff_ceiling(attempt: int, base_s: float = BACKOFF_BASE_S,
                    cap_s: float = BACKOFF_CAP_S) -> float:
    """Deterministic delay ceiling for ``attempt`` (1-indexed consecutive
    failures): 1s, 2s, 4s … capped at 30s. Mirrors backoff.ts
    ``exponentialDelay``."""
    if attempt < 1:
        return 0.0
    return min(cap_s, base_s * 2 ** (attempt - 1))


def backoff_delay(attempt: int, base_s: float = BACKOFF_BASE_S,
                  cap_s: float = BACKOFF_CAP_S) -> float:
    """Actual scheduled wait for ``attempt``: uniformly jittered in
    [0, ceiling]. Mirrors backoff.ts ``backoffDelay`` (full jitter, so
    reconnecting clients don't stampede shared endpoints after an outage)."""
    ceiling = backoff_ceiling(attempt, base_s, cap_s)
    if ceiling <= 0:
        return 0.0
    return random.uniform(0.0, ceiling)


_RETRYABLE_STATUS = {429, 502, 503, 504}
_IDEMPOTENT_METHODS = {"GET", "HEAD", "PUT", "DELETE"}


def _bracketed(value: str) -> bool:
    return "[" in value or "]" in value


def _strip_brackets(value: str) -> str:
    return ",".join(part for part in value.split(",") if part.strip() and not _bracketed(part))


def _env_client(**kwargs: Any) -> httpx.Client:
    """Build an httpx client even when NO_PROXY contains a bracketed IPv6 literal.

    httpx 0.28 parses NO_PROXY as a URL list; Docker Desktop ships entries such
    as ``[::1]``, which become ``Invalid port ':1]'`` on the next request. The
    proxy mounts are computed during ``httpx.Client(...)``, so sanitising the
    variables around construction is enough — the caller's environment is
    restored immediately and no other client sees the change.
    """
    names = ("NO_PROXY", "no_proxy")
    originals = {name: os.environ[name] for name in names if name in os.environ}
    try:
        for name, value in originals.items():
            if _bracketed(value):
                os.environ[name] = _strip_brackets(value)
        return httpx.Client(**kwargs)
    finally:
        for name, value in originals.items():
            os.environ[name] = value


class HarnessHTTP:
    """Bearer-authenticated JSON transport shared by all section clients.

    Not usually instantiated directly — :class:`harness_sdk.Harness` builds
    one and hands it to every generated ``Client``.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base_s: float = BACKOFF_BASE_S,
        backoff_cap_s: float = BACKOFF_CAP_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.token = token
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        self._client = _env_client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HarnessHTTP":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public entry point --------------------------------------------------

    def request(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        auth: bool = True,
        token: str | None = None,
    ) -> Any:
        """Send one call; return the decoded JSON body (or ``""``/text for
        empty/non-JSON responses). Raises :class:`ApiError` on any non-2xx.

        Transient failures (connection errors, 429/502/503/504) are retried
        with exponential backoff + jitter on idempotent methods only.
        ``token`` overrides the instance token for this single call (the WS
        stream pins its ticket-mints to the session that opened it).
        """
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._send_once(base_url, method, path,
                                       params=params, json_body=json_body,
                                       auth=auth, token=token)
            except ApiError as err:
                status = err.status
                retryable_status = status in _RETRYABLE_STATUS
                may_retry = (
                    attempt <= self.max_retries
                    and (method.upper() in _IDEMPOTENT_METHODS or status == 429)
                )
                if not (retryable_status and may_retry):
                    raise
                delay = self._retry_after_or_backoff(err.body_headers, attempt)
                logger.debug("retry %s %s (status %s) in %.2fs (attempt %d)",
                             method, path, status, delay, attempt)
                time.sleep(delay)
            except httpx.TransportError as err:
                if attempt > self.max_retries or method.upper() not in _IDEMPOTENT_METHODS:
                    raise ApiError(f"transport error: {err}", "TRANSPORT_ERROR") from err
                delay = backoff_delay(attempt, self.backoff_base_s, self.backoff_cap_s)
                logger.debug("retry %s %s (%s) in %.2fs (attempt %d)",
                             method, path, type(err).__name__, delay, attempt)
                time.sleep(delay)

    # -- internals -----------------------------------------------------------

    def _retry_after_or_backoff(self, headers: httpx.Headers | None, attempt: int) -> float:
        if headers is not None:
            ra = headers.get("retry-after")
            if ra:
                try:
                    return max(0.0, float(ra))
                except ValueError:
                    pass
        return backoff_delay(attempt, self.backoff_base_s, self.backoff_cap_s)

    def _send_once(
        self,
        base_url: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: Any,
        auth: bool,
        token: str | None = None,
    ) -> Any:
        headers = {"user-agent": SDK_USER_AGENT}
        effective_token = token if token is not None else self.token
        if auth and effective_token:
            headers["authorization"] = f"Bearer {effective_token}"
        # Drop unset optional query params (None sentinel from generated stubs).
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self._client.request(
            method, f"{base_url.rstrip('/')}{path}",
            params=clean_params or None,
            json=json_body,
            headers=headers,
        )
        if resp.status_code < 200 or resp.status_code > 299:
            body: Any = None
            try:
                body = resp.json()
            except (ValueError, json.JSONDecodeError):
                body = None
            code, message = _code_message(resp.status_code, body)
            raise ApiError(message, code, resp.status_code, body,
                           headers=resp.headers)
        if resp.status_code == 204 or not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "application/json" not in ctype:
            return resp.text
        return resp.json()


def _code_message(status: int, body: Any) -> tuple[str, str]:
    # Local indirection kept tiny so errors.py stays the single definition.
    from .errors import parse_api_error

    return parse_api_error(status, body)
