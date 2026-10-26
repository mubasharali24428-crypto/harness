"""env_client keeps proxy support and survives Docker Desktop's bracketed-IPv6 NO_PROXY."""

from __future__ import annotations

import httpx
import pytest

from harness_identity.http_client import env_client

DOCKER_DESKTOP_NO_PROXY = "localhost,localhost.,.localhost,127.0.0.1,127.0.0.0/8,::1,[::1]"


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key in ("NO_PROXY", "no_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_plain_httpx_still_rejects_the_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the premise: if httpx fixes this, the helper is dead weight and can go."""
    _set(monkeypatch, NO_PROXY=DOCKER_DESKTOP_NO_PROXY)
    with pytest.raises(httpx.InvalidURL):
        httpx.AsyncClient()


def test_bracketed_ipv6_no_proxy_constructs(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, NO_PROXY=DOCKER_DESKTOP_NO_PROXY, no_proxy=DOCKER_DESKTOP_NO_PROXY)
    client = env_client(timeout=5)
    assert client.timeout.read == 5


def test_proxy_and_bypass_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, HTTPS_PROXY="http://proxy.corp:3128", NO_PROXY=DOCKER_DESKTOP_NO_PROXY)
    client = env_client()
    patterns = {p.pattern: t for p, t in client._mounts.items()}
    assert patterns["https://"] is not None  # still routed through the proxy
    assert patterns["all://[::1]"] is None  # and the bypass for ::1 still holds
    assert patterns["all://127.0.0.1"] is None


def test_environment_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, NO_PROXY=DOCKER_DESKTOP_NO_PROXY)
    env_client()
    import os

    assert os.environ["NO_PROXY"] == DOCKER_DESKTOP_NO_PROXY


def test_clean_environment_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, NO_PROXY="localhost,127.0.0.1,::1")
    assert isinstance(env_client(), httpx.AsyncClient)


def test_a_different_bad_url_is_not_papered_over(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, NO_PROXY="http://host:notaport")  # InvalidURL, but no bracket to blame
    with pytest.raises(httpx.InvalidURL):
        env_client()
