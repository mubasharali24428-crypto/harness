"""Outbound HTTP client that honours proxy environment variables without crashing on them.

httpx 0.28 (the newest release) expands every ``NO_PROXY`` entry into a mount pattern and
mishandles a bracketed IPv6 literal: ``[::1]`` becomes ``all://*[::1]``, whose port parses as
``:1]``, and *constructing the client* raises ``InvalidURL``. Docker Desktop exports exactly that
shape (``localhost,...,::1,[::1]``), so a host carrying it failed every SSO login with a 500
before any IdP call. Proxy support itself is kept: this only strips the brackets, which name the
same address, and only when the first attempt failed.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")


def env_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient(**kwargs)`` that survives a bracketed IPv6 ``NO_PROXY`` entry."""
    try:
        return httpx.AsyncClient(**kwargs)
    except httpx.InvalidURL:
        saved = {k: os.environ[k] for k in _NO_PROXY_VARS if k in os.environ}
        if not any("[" in v or "]" in v for v in saved.values()):
            raise  # not the NO_PROXY shape; do not paper over some other bad URL
        try:
            for key, value in saved.items():
                os.environ[key] = value.replace("[", "").replace("]", "")
            return httpx.AsyncClient(**kwargs)
        finally:
            os.environ.update(saved)
