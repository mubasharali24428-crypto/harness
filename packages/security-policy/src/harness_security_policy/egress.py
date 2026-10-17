"""F096 — per-run network egress policy evaluation.

Decides whether a run may contact a URL. Fail-closed by default: with an
empty ``allowed_hosts`` list every destination is denied. Private, loopback,
link-local (including cloud metadata endpoints) and reserved destinations
are denied even when a host pattern matches, unless the operator explicitly
opts in.

DNS: when a resolver is supplied, every resolved address is classified and
checked, which closes the classic "allowlist the name, resolve to
169.254.169.254" SSRF trick. ``require_dns_check`` (default True) refuses to
approve a hostname that was never resolved, so a caller that forgets a
resolver fails closed instead of open. A URL's configured allowlisting is
never permission to contact arbitrary hosts: each redirect hop must be
re-evaluated via :func:`evaluate_redirect_chain`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from .decisions import Decision, Outcome

Resolver = Callable[[str], Sequence[str]]


def classify_ip(ip: str) -> str:
    addr = ipaddress.ip_address(ip)
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link_local"
    if addr.is_unspecified:
        return "unspecified"
    if addr.is_multicast:
        return "multicast"
    if addr.is_private:
        return "private"
    if addr.is_reserved:
        return "reserved"
    return "public"


@dataclass(frozen=True)
class EgressPolicy:
    allowed_hosts: tuple[str, ...] = ()
    denied_hosts: tuple[str, ...] = ()
    allow_http: bool = False
    allowed_ports: tuple[int, ...] = (443,)
    allow_private: bool = False
    allow_link_local: bool = False
    allow_ip_literals: bool = False
    require_dns_check: bool = True


def host_matches(host: str, pattern: str) -> bool:
    """Exact or ``*.suffix`` match on a normalized hostname."""
    host = host.lower().rstrip(".")
    if pattern.startswith("*."):
        base = pattern[2:].lower().rstrip(".")
        return bool(base) and host.endswith("." + base)
    return host == pattern.lower().rstrip(".")


def _ip_allowed(classification: str, policy: EgressPolicy) -> bool:
    if classification == "public":
        return True
    if classification == "private":
        return policy.allow_private
    if classification == "link_local":
        return policy.allow_link_local
    if classification == "loopback":
        return policy.allow_private
    return False


def _deny(reason: str, **details: object) -> Decision:
    return Decision(Outcome.DENY, reason, details=details)


def evaluate_url(
    url: str,
    *,
    policy: EgressPolicy,
    resolver: Resolver | None = None,
    now: object | None = None,  # placeholder for API symmetry
) -> Decision:
    del now  # evaluation is stateless
    if not isinstance(url, str) or not url.strip():
        return _deny("egress.malformed_url")
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return _deny("egress.malformed_url")

    scheme = (parts.scheme or "").lower()
    if scheme == "https":
        default_port = 443
    elif scheme == "http":
        if not policy.allow_http:
            return _deny("egress.scheme_denied", scheme=scheme)
        default_port = 80
    else:
        return _deny("egress.scheme_denied", scheme=scheme or "<none>")

    if parts.username is not None or parts.password is not None:
        return _deny("egress.userinfo_denied")

    host = (parts.hostname or "").strip().rstrip(".")
    if not host:
        return _deny("egress.malformed_url")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return _deny("egress.malformed_host")

    try:
        port = parts.port if parts.port is not None else default_port
    except ValueError:
        return _deny("egress.malformed_url")

    for pattern in policy.denied_hosts:
        if host_matches(host, pattern):
            return _deny("egress.host_denied", host=host)

    if not any(host_matches(host, pattern) for pattern in policy.allowed_hosts):
        return _deny("egress.host_not_allowed", host=host)

    if port not in policy.allowed_ports:
        return _deny("egress.port_denied", host=host, port=port)

    literal: str | None = None
    try:
        ipaddress.ip_address(host)
        literal = host
    except ValueError:
        literal = None

    if literal is not None:
        if not policy.allow_ip_literals:
            return _deny("egress.ip_literal_denied", host=host)
        classification = classify_ip(literal)
        if not _ip_allowed(classification, policy):
            return _deny("egress.ip_class_denied", host=host, classification=classification)
        return Decision(
            Outcome.ALLOW,
            "egress.allowed",
            details={
                "host": host,
                "port": port,
                "scheme": scheme,
                "classification": classification,
                "resolution": "literal",
            },
        )

    if resolver is None:
        if policy.require_dns_check:
            return _deny("egress.dns_check_unavailable", host=host)
        return Decision(
            Outcome.ALLOW,
            "egress.allowed",
            details={"host": host, "port": port, "scheme": scheme, "resolution": "not_checked"},
        )

    try:
        addresses = list(resolver(host))
    except Exception:  # noqa: BLE001 — any resolver failure is fail-closed
        return _deny("egress.dns_failure", host=host)
    if not addresses:
        return _deny("egress.dns_failure", host=host)
    for address in addresses:
        try:
            classification = classify_ip(address)
        except ValueError:
            return _deny("egress.dns_failure", host=host)
        if not _ip_allowed(classification, policy):
            return _deny(
                "egress.resolved_ip_class_denied",
                host=host,
                classification=classification,
            )
    return Decision(
        Outcome.ALLOW,
        "egress.allowed",
        details={
            "host": host,
            "port": port,
            "scheme": scheme,
            "classification": "public",
            "resolution": "checked",
            "addressCount": len(addresses),
        },
    )


def evaluate_redirect_chain(
    urls: Sequence[str],
    *,
    policy: EgressPolicy,
    resolver: Resolver | None = None,
) -> Decision:
    """Every hop must pass; one denied hop denies the chain."""
    if not urls:
        return _deny("egress.empty_chain")
    for index, url in enumerate(urls):
        decision = evaluate_url(url, policy=policy, resolver=resolver)
        if decision.outcome is Outcome.DENY:
            details = dict(decision.details)
            details["hopIndex"] = index
            details["hopCount"] = len(urls)
            return Decision(Outcome.DENY, decision.reason, details=details)
    return Decision(
        Outcome.ALLOW,
        "egress.chain_allowed",
        details={"hopCount": len(urls)},
    )
