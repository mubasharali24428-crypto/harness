"""Network policy for external MCP endpoints (deny-by-default SSRF posture).

A registered MCP server URL is attacker-influenceable: a team member could
point it at cloud metadata (169.254.169.254), internal admin planes or
loopback services, and exfiltrate responses through tool calls. Every URL is
checked on registration AND re-checked right before each discovery/health/
call, including resolving the hostname and scrubbing every resolved address.

Residual risks, documented rather than hand-waved:
  - TOCTOU/DNS rebinding: we resolve, then httpx resolves again to connect.
    A TTL-0 rebinding attacker can win that race. Full protection needs
    connection pinning (connect to the checked IP, SNI = hostname), which is
    on the follow-up list; the posture here still stops static private
    targets and re-checks dissipate long-lived rebinds.
  - The operator opt-in (allow_private_hosts=True) is required for
    self-hosted MCP servers on localhost/private networks; it widens the
    policy deliberately and is logged by callers.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class UrlPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


#: Networks refused unless allow_private_hosts is set.
def _is_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    )


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    host: str
    resolved_ips: tuple[str, ...]


def validate_server_url(url: str, *, allow_private_hosts: bool) -> ValidatedTarget:
    """Static + DNS checks. Raises :class:`UrlPolicyError` on refusal."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UrlPolicyError("URL_SCHEME_DENIED", f"scheme {parts.scheme!r} not allowed")
    if parts.username or parts.password:
        raise UrlPolicyError("URL_USERINFO_DENIED", "credentials in server URL are not allowed")
    host = parts.hostname
    if not host:
        raise UrlPolicyError("URL_HOST_MISSING", "server URL must include a host")
    if parts.fragment:
        raise UrlPolicyError("URL_FRAGMENT_DENIED", "fragments are meaningless for MCP endpoints")

    resolved: list[str] = []
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError as exc:
        raise UrlPolicyError("URL_DNS_FAILED", f"cannot resolve {host!r}: {exc}") from exc
    for info in infos:
        ip = info[4][0]
        resolved.append(ip)
    if not resolved:
        raise UrlPolicyError("URL_DNS_FAILED", f"no addresses for {host!r}")

    if not allow_private_hosts:
        for ip_str in resolved:
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                raise UrlPolicyError("URL_DNS_FAILED", f"unparsable address {ip_str!r}") from None
            if _is_blocked(addr):
                raise UrlPolicyError(
                    "URL_PRIVATE_DENIED",
                    f"{host!r} resolves to non-public address {ip_str}; "
                    "set HARNESS_EXTENSIONS_ALLOW_PRIVATE_HOSTS=1 to opt in",
                )
    return ValidatedTarget(url=url, host=host, resolved_ips=tuple(sorted(set(resolved))))
