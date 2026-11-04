"""The contract-level outbound client factory.

Lives in Section 1 because Section 1 owns `contracts/`. The point of the test is
that the protection is a property of the FACTORY, not of one call site: three
services needed it and previously each would have had its own copy.
"""

import httpx
import pytest
from harness_contracts.http_client import env_client, sync_env_client

BRACKETED = "localhost,127.0.0.1,::1,[::1]"


def test_an_async_client_survives_a_bracketed_ipv6_no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", BRACKETED)

    client = env_client(timeout=1.0)

    assert isinstance(client, httpx.AsyncClient)


def test_the_sync_twin_survives_it_too(monkeypatch):
    monkeypatch.setenv("NO_PROXY", BRACKETED)

    client = sync_env_client(timeout=1.0)

    assert isinstance(client, httpx.Client)


def test_the_environment_is_restored_after_the_retry(monkeypatch):
    """The retry rewrites NO_PROXY in-process. Leaving it rewritten would change
    proxy routing for every other client in the process."""
    monkeypatch.setenv("NO_PROXY", BRACKETED)

    env_client()
    sync_env_client()

    import os

    assert os.environ["NO_PROXY"] == BRACKETED


def test_an_unrelated_invalid_url_is_not_swallowed(monkeypatch):
    """Only the NO_PROXY shape is retried. A genuinely bad URL must still raise,
    otherwise this helper would hide real configuration errors."""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")  # no brackets

    class Exploding(httpx.AsyncClient):
        def __init__(self, **kwargs):  # noqa: D107 - test double
            raise httpx.InvalidURL("Invalid port: 'nope'")

    monkeypatch.setattr(httpx, "AsyncClient", Exploding)

    with pytest.raises(httpx.InvalidURL, match="nope"):
        env_client()


def test_section_one_still_exports_the_same_factory():
    """Section 1's call sites import from `harness_identity.http_client`; the
    shim must be the contract implementation, not a second copy."""
    from harness_identity.http_client import env_client as section1_env_client

    assert section1_env_client is env_client
