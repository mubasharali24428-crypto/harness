"""The egress allow-list, derived from the provider registry (W4).

Three things changed here, and each one is a boundary:

* The hosts are **data** (``egress_allowlist.json``), generated from
  ``section3/src/harness_router/provider_registry.py`` by
  ``scripts/gen_egress_allowlist.py``. Sections may not import each other, so
  the derivation is build-time and a drift test keeps the artifact honest.
* A decision is **purpose-scoped**. A sandboxed shell may reach provider
  endpoints, package registries and git hosts; a web tool may read provider
  docs. Docs are *not* sandbox destinations — deriving the list must not hand
  every linked documentation site to a shell.
* The decision is **explainable** (``EgressDecision``), so a tool can put the
  refusal in its result instead of surfacing an opaque proxy 403.

Operator additions from ``SANDBOX_PINNED_REGISTRIES`` still apply to sandbox
purposes; an unknown purpose raises rather than falling back to allow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

PURPOSE_SANDBOX = "sandbox"
PURPOSE_WEB = "web"
PURPOSE_ANY = "any"

ALLOWLIST_PATH = Path(__file__).with_name("egress_allowlist.json")

_GROUPS = ("providerEndpoints", "providerDocs", "packageRegistries", "gitHosts")

#: Which artifact groups each purpose may reach. ``any`` is the union and exists
#: for tests/administration, not as a runtime default.
_PURPOSE_GROUPS: dict[str, tuple[str, ...]] = {
    PURPOSE_SANDBOX: ("packageRegistries", "gitHosts", "providerEndpoints"),
    PURPOSE_WEB: ("providerDocs", "providerEndpoints"),
    PURPOSE_ANY: _GROUPS,
}

_RESERVED_EXACT = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})


@dataclass(frozen=True)
class EgressDecision:
    """Why a destination is or is not reachable for a given purpose."""

    allowed: bool
    host: str
    purpose: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "host": self.host,
            "purpose": self.purpose,
            "reason": self.reason,
        }


@lru_cache(maxsize=1)
def _groups() -> dict[str, frozenset[str]]:
    raw = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    groups = {
        name: frozenset(str(h).strip().lower() for h in raw.get(name, []) if str(h).strip())
        for name in _GROUPS
    }
    groups["_operator"] = frozenset(_operator_registries())
    return groups


def _operator_registries() -> list[str]:
    """``SANDBOX_PINNED_REGISTRIES`` when the package settings are importable.

    Deliberately lazy: the engine worker copies this file next to a stdlib-only
    gate and has no pydantic/docker install, so importing the package settings
    there would make the gate unusable. No settings simply means no additions.
    """
    try:
        from harness_sandbox.config import config
    except Exception:  # pragma: no cover - standalone worker copy
        return []
    return list(config.pinned_registries)


def reset_cache() -> None:
    """Drop the parsed artifact (tests, or an operator replacing the file)."""
    _groups.cache_clear()


def host_of(url_or_host: str) -> str:
    """Host only: lower-case, no userinfo, no port, no brackets."""
    parsed = urlparse(url_or_host)
    netloc = parsed.netloc or parsed.path
    return netloc.rsplit("@", 1)[-1].split(":")[0].strip().lower()


def egress_decision(url_or_host: str, purpose: str = PURPOSE_SANDBOX) -> EgressDecision:
    if purpose not in _PURPOSE_GROUPS:
        raise ValueError(f"unknown egress purpose {purpose!r}")
    host = host_of(url_or_host)
    if not host:
        return EgressDecision(False, host, purpose, "no host in destination")
    if host in _RESERVED_EXACT or host.startswith("[") or host.startswith("]"):
        return EgressDecision(
            False, host, purpose, "loopback and IP-literal destinations are refused"
        )

    groups = _groups()
    for name in _PURPOSE_GROUPS[purpose]:
        if host in groups[name]:
            return EgressDecision(True, host, purpose, f"listed in {name}")
    if purpose in (PURPOSE_SANDBOX, PURPOSE_ANY) and host in groups["_operator"]:
        return EgressDecision(True, host, purpose, "listed in SANDBOX_PINNED_REGISTRIES")
    return EgressDecision(False, host, purpose, f"{host} is not in the {purpose} allow-list")


def is_allowed_destination(url_or_host: str, purpose: str = PURPOSE_SANDBOX) -> bool:
    """Whether ``url_or_host`` may be reached for ``purpose``."""
    return egress_decision(url_or_host, purpose).allowed


class EgressPolicy:
    """Boolean facade over is_allowed_destination."""

    @staticmethod
    def is_url_allowed(url_or_host: str, purpose: str = PURPOSE_SANDBOX) -> bool:
        return is_allowed_destination(url_or_host, purpose)
