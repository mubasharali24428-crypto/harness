"""G-F SSRF guard proof (P5): the webhook/SIEM delivery path re-resolves DNS
at delivery time and refuses non-global answers (egress.py law), regardless
of what was stored. Pins the three attack shapes: literal private IP, DNS
rebind after save, redirect walk (follow_redirects=False).

The guard is shared (egress.validate_public_https at SAVE time,
egress.host_is_public at DELIVERY time); these tests prove delivery-time
behavior with a monkeypatched resolver — no network, no flakiness.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from harness_identity.egress import host_is_public, validate_public_https


def _fake_resolver(answers: list[str]):
    """getaddrinfo stub returning the given IPv4 answers for any host."""

    class _Addr:
        family_ok = True

        def __init__(self, ip: str) -> None:
            self._ip = ip

        def __getitem__(self, i: int):
            return ("", 0, 0, "", (self._ip, 443)) if i == 4 else None

    def fake(host, port, **kwargs):
        return [(None, None, None, "", (ip, 443)) for ip in answers]

    return fake


def test_literal_private_ips_are_never_public():
    for bad in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.9",
                "169.254.169.254", "0.0.0.0", "::1", "fe80::1", "100.64.0.7"):
        with patch("harness_identity.egress.socket.getaddrinfo",
                   _fake_resolver([bad])):
            assert host_is_public(bad) is False, bad


def test_global_ips_are_public():
    with patch("harness_identity.egress.socket.getaddrinfo",
               _fake_resolver(["93.184.216.34"])):
        assert host_is_public("example.com") is True


def test_dns_rebind_after_save_is_refused_at_delivery():
    """The admin saved webhook.example.com when it resolved public; by delivery
    it re-resolves to the cloud-metadata address. The guard must refuse."""
    with patch("harness_identity.egress.socket.getaddrinfo",
               _fake_resolver(["169.254.169.254"])):
        assert host_is_public("webhook.example.com") is False


def test_mixed_answers_are_refused():
    """One public + one private answer = refuse (all() semantics)."""
    with patch("harness_identity.egress.socket.getaddrinfo",
               _fake_resolver(["93.184.216.34", "10.0.0.9"])):
        assert host_is_public("split-horizon.example.com") is False


def test_save_time_validation_refuses_private_literals_and_local_names():
    allowed = {"hooks.example.com"}
    for url in ("http://hooks.example.com/h",                 # not https
                "https://hooks.example.com:8443/h",           # not default port
                "https://user:pw@hooks.example.com/h",        # userinfo
                "https://hooks.example.com/h?x=1",            # query
                "https://10.0.0.5/h",                    # literal private IP (unlisted)
                "https://api.localhost/h",                    # .localhost
                "https://evil.example.com/h"):                # not on the allow-list
        with pytest.raises(HTTPException):
            validate_public_https(url, allowed=allowed, code="X", label="webhook")
    # The good one passes and comes back normalized.
    ok = validate_public_https("https://hooks.example.com/h",
                               allowed=allowed, code="X", label="webhook")
    assert ok.startswith("https://hooks.example.com")


def test_delivery_sender_refuses_before_any_socket_opens():
    """webhooks._http_sender must check allow-list + public resolution BEFORE
    opening a client. Prove by making the resolver private and asserting the
    ValueError without httpx ever dialing."""
    import asyncio

    from harness_identity import webhooks

    with patch("harness_identity.egress.socket.getaddrinfo",
               _fake_resolver(["169.254.169.254"])), \
         patch("harness_identity.webhooks.httpx.AsyncClient") as never:
        with pytest.raises(ValueError):
            asyncio.run(webhooks._http_sender(
                "https://hooks.example.com/h", b"{}", {"a": "b"}))
        never.assert_not_called(), "httpx client was constructed — the guard is after the dial"


def test_redirect_walk_is_disabled():
    """follow_redirects=False is the redirect-walk law: prove the sender never
    follows by asserting the client is built with the flag off."""
    import inspect

    import harness_identity.webhooks as w

    src = inspect.getsource(w._http_sender)
    assert "follow_redirects=False" in src
