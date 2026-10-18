"""Outbound-host policy for registered MCP servers (SSRF controls)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class UrlRejected(ValueError):
    pass


def _blocked(ip: ipaddress._BaseAddress, *, loopback: bool, private: bool) -> str | None:
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return "link-local/multicast/unspecified/reserved addresses are never allowed"
    if ip.is_loopback:
        return None if loopback else "loopback is not allowed by this deployment"
    # not is_global also catches CGNAT 100.64/10 and other special-use ranges
    if ip.is_private or not ip.is_global:
        return None if private else "private network addresses are not allowed by this deployment"
    return None


def validate_server_url(raw: str, *, allow_loopback: bool, allow_private: bool) -> str:
    """Static checks at registration time. Returns the normalised origin+path."""
    u = urlsplit(raw)
    if u.username or u.password or not u.hostname or u.fragment:
        raise UrlRejected("url must be a plain URL without credentials or fragment")
    host = u.hostname
    is_loop = host.rstrip(".") in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")
    if u.scheme != "https" and not (u.scheme == "http" and is_loop and allow_loopback):
        raise UrlRejected("url must be https (http only for an allowed loopback host)")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
        try:  # legacy forms such as 127.1 or 0x7f.1 are IPs to the resolver, not names
            ip = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host)))
        except OSError:
            pass
    if ip is not None:
        why = _blocked(ip, loopback=allow_loopback, private=allow_private)
        if why:
            raise UrlRejected(why)
    elif is_loop and not allow_loopback:
        raise UrlRejected("loopback is not allowed by this deployment")
    return f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"


def check_resolution(url: str, *, allow_loopback: bool, allow_private: bool) -> None:
    """Call-time check: every address the name resolves to must be allowed (DNS rebinding).

    Residual: a resolver can still change answers between this check and the connection
    (TOCTOU); closing that needs connect-by-IP with Host pinning, recorded as a limit.
    """
    host = urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise UrlRejected(f"cannot resolve host: {exc}") from exc
    for info in infos:
        why = _blocked(
            ipaddress.ip_address(info[4][0]), loopback=allow_loopback, private=allow_private
        )
        if why:
            raise UrlRejected(f"host resolves to a disallowed address: {why}")
