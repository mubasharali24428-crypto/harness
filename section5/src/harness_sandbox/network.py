"""S3: Internet access modes - disabled | allowlisted | unrestricted.

* disabled     -> processes get no network (confinement allow_network=False).
* allowlisted  -> processes still get NO direct network (the local backends cannot
                  filter by hostname); egress goes through BrokeredFetcher, which
                  checks the allowlist, resolves DNS itself, refuses private /
                  loopback / link-local answers (rebinding) and re-validates every
                  redirect hop.
* unrestricted -> only with a UnrestrictedGrant (named approver, unexpired, bound to
                  the task). It widens network ONLY; command policy, secret-file
                  rules and forbidden_paths still apply unchanged.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urljoin, urlparse

from harness_sandbox.egress import is_allowed_destination
from harness_sandbox.policy import SecurityPolicyViolationError


class NetworkMode(StrEnum):
    DISABLED = "disabled"
    ALLOWLISTED = "allowlisted"
    UNRESTRICTED = "unrestricted"


@dataclass(frozen=True)
class UnrestrictedGrant:
    task_id: str
    approver: str
    expires_at: float


def process_network_allowed(
    mode: NetworkMode, task_id: str, grant: UnrestrictedGrant | None, now: float | None = None
) -> bool:
    """Whether the sandboxed PROCESS itself may open sockets."""
    if mode is not NetworkMode.UNRESTRICTED:
        return False
    t = time.time() if now is None else now
    if grant is None or not grant.approver or grant.task_id != task_id or t >= grant.expires_at:
        raise SecurityPolicyViolationError(
            "unrestricted network needs an unexpired grant with a named approver for this task"
        )
    return True


def _is_public(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved
                or a.is_multicast or a.is_unspecified)


class BrokeredFetcher:
    def __init__(
        self,
        allowed: Callable[[str], bool] = is_allowed_destination,
        resolve: Callable[[str], list[str]] | None = None,
        open_url: Callable[[str], tuple[int, dict[str, str], bytes]] | None = None,
        max_redirects: int = 3,
        max_bytes: int = 5_000_000,
        allow_private: bool = False,
    ) -> None:
        self.allowed = allowed
        self.resolve = resolve or (
            lambda h: [i[4][0] for i in socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)]
        )
        self.open_url = open_url  # None -> pinned default transport
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.allow_private = allow_private  # tests against loopback fixtures only

    def _open(
        self, url: str, ips: list[str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        """One GET with redirects disabled. When ``ips`` is given the socket is
        opened to that already-validated address (Host/SNI keep the name), so a
        second DNS answer can never redirect the connection (TOCTOU rebinding)."""
        p = urlparse(url)
        host = p.hostname or ""
        https = p.scheme == "https"
        port = p.port or (443 if https else 80)
        target = ips[0] if ips else host

        class _Conn(http.client.HTTPSConnection if https else http.client.HTTPConnection):  # type: ignore[misc]
            def connect(self) -> None:
                sock = socket.create_connection((target, port), timeout=10)
                if https:
                    ctx = ssl.create_default_context()
                    sock = ctx.wrap_socket(sock, server_hostname=host)
                self.sock = sock

        conn = _Conn(host, port, timeout=10)
        try:
            path = (p.path or "/") + (f"?{p.query}" if p.query else "")
            conn.request("GET", path, headers={"Host": p.netloc, "Connection": "close"})
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read(self.max_bytes + 1)
        finally:
            conn.close()

    def _check(self, url: str) -> list[str]:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            raise SecurityPolicyViolationError(f"url not permitted: {url}")
        if not self.allowed(p.hostname):
            raise SecurityPolicyViolationError(f"host '{p.hostname}' is not on the allowlist")
        ips = self.resolve(p.hostname)
        for ip in ips:
            if not self.allow_private and not _is_public(ip):
                raise SecurityPolicyViolationError(
                    f"host '{p.hostname}' resolves to non-public address {ip}"
                )
        if not ips:
            raise SecurityPolicyViolationError(f"host '{p.hostname}' did not resolve")
        return ips

    def get(self, url: str) -> tuple[int, bytes]:
        for _ in range(self.max_redirects + 1):
            ips = self._check(url)  # every hop: allowlist + DNS (re)resolution
            if self.open_url is not None:
                status, headers, body = self.open_url(url)
            else:
                status, headers, body = self._open(url, ips)  # connects to the checked IP
            location = next((v for k, v in headers.items() if k.lower() == "location"), None)
            if status in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                continue
            if len(body) > self.max_bytes:
                raise SecurityPolicyViolationError("response exceeds size cap")
            return status, body
        raise SecurityPolicyViolationError("too many redirects")
