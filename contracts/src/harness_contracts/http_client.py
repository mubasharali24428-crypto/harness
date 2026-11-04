"""Outbound httpx clients that honour proxy env vars without crashing on them.

httpx 0.28 (currently the newest release) expands every ``NO_PROXY`` entry into a
mount pattern and mishandles a bracketed IPv6 literal: ``[::1]`` becomes
``all://*[::1]``, whose port parses as ``:1]``, and *constructing the client*
raises ``InvalidURL``. Docker Desktop exports exactly that shape
(``localhost,...,::1,[::1]``), so any service on such a host failed at startup of
its outbound path — SSO logins 500'd before any IdP call, and three other
services failed their own suites for the same reason.

This lives in the contract package because the alternative is three copies of a
security-relevant HTTP client factory in three sections, which is the drift the
isolation guard exists to prevent. Sections may import ``harness_contracts``;
they may not import each other.

Proxy support itself is kept. The retry strips the brackets — ``[::1]`` and
``::1`` name the same address — and only when the first attempt failed *and* the
environment actually carries a bracketed entry. Any other ``InvalidURL`` is
re-raised untouched, because papering over an unrelated bad URL would hide it.

httpx is imported lazily: it is an optional extra (``harness-contracts[http]``)
so the contract package does not force a transport on consumers that never make
outbound calls.
"""

from __future__ import annotations

import os
from typing import Any, Callable

_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")


def _httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise ImportError(
            "harness_contracts.http_client requires httpx; "
            "install harness-contracts[http]"
        ) from exc
    return httpx


def _build(factory: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    httpx = _httpx()
    try:
        return factory(**kwargs)
    except httpx.InvalidURL:
        saved = {k: os.environ[k] for k in _NO_PROXY_VARS if k in os.environ}
        if not any("[" in value or "]" in value for value in saved.values()):
            raise  # not the NO_PROXY shape; do not paper over some other bad URL
        try:
            for key, value in saved.items():
                os.environ[key] = value.replace("[", "").replace("]", "")
            return factory(**kwargs)
        finally:
            # Always restore: this ran because the environment was the problem,
            # and leaving a rewritten NO_PROXY behind would change proxy routing
            # for every other client in the process.
            os.environ.update(saved)


def env_client(**kwargs: Any) -> Any:
    """``httpx.AsyncClient(**kwargs)`` that survives a bracketed IPv6 ``NO_PROXY``."""
    return _build(_httpx().AsyncClient, dict(kwargs))


def sync_env_client(**kwargs: Any) -> Any:
    """The ``httpx.Client`` twin, for callers that are not async."""
    return _build(_httpx().Client, dict(kwargs))
