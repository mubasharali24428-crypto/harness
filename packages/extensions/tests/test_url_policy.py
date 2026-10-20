"""URL policy unit tests (deny-by-default SSRF posture)."""

from __future__ import annotations

import pytest

from harness_extensions.url_policy import UrlPolicyError, validate_server_url


def test_https_public_host_allowed():
    # example.com resolves publicly; allowed with default posture.
    target = validate_server_url("https://example.com/mcp", allow_private_hosts=False)
    assert target.host == "example.com"
    assert target.resolved_ips


def test_scheme_denied():
    with pytest.raises(UrlPolicyError) as err:
        validate_server_url("ftp://example.com/mcp", allow_private_hosts=False)
    assert err.value.code == "URL_SCHEME_DENIED"


def test_userinfo_denied():
    with pytest.raises(UrlPolicyError) as err:
        validate_server_url("https://user:pass@example.com/mcp", allow_private_hosts=False)
    assert err.value.code == "URL_USERINFO_DENIED"


def test_fragment_denied():
    with pytest.raises(UrlPolicyError) as err:
        validate_server_url("https://example.com/mcp#frag", allow_private_hosts=False)
    assert err.value.code == "URL_FRAGMENT_DENIED"


def test_metadata_ip_denied():
    with pytest.raises(UrlPolicyError) as err:
        validate_server_url("http://169.254.169.254/latest", allow_private_hosts=False)
    assert err.value.code == "URL_PRIVATE_DENIED"


def test_loopback_denied_by_default_allowed_by_optin():
    with pytest.raises(UrlPolicyError) as err:
        validate_server_url("http://127.0.0.1:9999/mcp", allow_private_hosts=False)
    assert err.value.code == "URL_PRIVATE_DENIED"
    target = validate_server_url("http://127.0.0.1:9999/mcp", allow_private_hosts=True)
    assert target.host == "127.0.0.1"


def test_private_ranges_denied():
    for url in ("http://10.0.0.5/mcp", "http://192.168.1.10/mcp", "http://[::1]/mcp"):
        with pytest.raises(UrlPolicyError):
            validate_server_url(url, allow_private_hosts=False)


def test_missing_host():
    with pytest.raises(UrlPolicyError) as err:
        validate_server_url("http:///mcp", allow_private_hosts=False)
    assert err.value.code == "URL_HOST_MISSING"
