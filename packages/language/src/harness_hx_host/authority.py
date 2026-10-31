"""Authorisation: what the host is willing to grant, decided outside the program.

The manifest is a request. This module is the other half of that sentence: it
takes the request, the host's grant table and the authenticated principal's
permissions, and produces a decision. Nothing in the source can influence it —
there is no call from a program into this module, and the runner consults the
decision at every call site rather than once at the start.

Three refusals are possible per capability, and they mean different things:

* **not in the closed table** — the manifest is corrupted or hand-written; the
  checker would have caught it.
* **not granted by this host** — the policy question. Denied by default: an
  empty grant list grants nothing.
* **the principal lacks the permission** — the identity question, resolved by
  Section 1's policy engine, not here.

``approve``, ``administer``, ``billing`` and ``export`` are human-only. A
program may never hold them, so a granted capability that mapped to one would
be a bug in this module rather than a policy choice — the table in
``harness_hx.capabilities`` cannot produce it, and this code refuses if it ever
does.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from harness_hx.capabilities import CAPABILITIES

from .errors import DigestMismatch

#: Permissions no language program may hold. Keep in step with contracts'
#: ``PERMISSIONS`` minus the three a run legitimately needs (read/comment/steer).
HUMAN_ONLY: frozenset[str] = frozenset({"approve", "administer", "billing", "export"})


@dataclass(frozen=True)
class Denial:
    capability: str
    reason: str
    permission: str | None = None

    def to_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {"capability": self.capability, "reason": self.reason}
        if self.permission is not None:
            record["permission"] = self.permission
        return record


@dataclass(frozen=True)
class Decision:
    """The host's answer, and the only thing the runner treats as authority."""

    requested: frozenset[str]
    granted: frozenset[str]
    denials: tuple[Denial, ...]
    principal: str
    policy: str

    @property
    def allowed(self) -> bool:
        return not self.denials

    def allows(self, capability: str | None) -> bool:
        """A pure operation (no capability) always runs; an effect must be granted."""
        return capability is None or capability in self.granted

    def to_json(self) -> dict[str, Any]:
        return {
            "principal": self.principal,
            "policy": self.policy,
            "requested": sorted(self.requested),
            "granted": sorted(self.granted),
            "denied": [denial.to_json() for denial in self.denials],
            "allowed": self.allowed,
            "authority": "harness",
            "forgeable": False,
        }


def requested_capabilities(manifest: Mapping[str, Any]) -> frozenset[str]:
    requested: set[str] = set()
    for workflow in manifest.get("workflows", ()):  # type: ignore[union-attr]
        if isinstance(workflow, Mapping):
            for name in workflow.get("requires", ()):  # type: ignore[union-attr]
                if isinstance(name, str):
                    requested.add(name)
    return frozenset(requested)


def authorize(
    manifest: Mapping[str, Any],
    *,
    granted: Iterable[str] = (),
    principal_permissions: Sequence[str] | None = None,
    principal: str = "anonymous",
    policy: str = "local-dev",
) -> Decision:
    """Decide which requested capabilities this host actually grants."""
    requested = requested_capabilities(manifest)
    offered = frozenset(granted)
    permitted = None if principal_permissions is None else frozenset(principal_permissions)
    denials: list[Denial] = []
    allowed: set[str] = set()

    for name in sorted(requested):
        table_entry = CAPABILITIES.get(name)
        if table_entry is None:
            denials.append(Denial(name, "not in the closed capability table"))
            continue
        if name not in offered:
            denials.append(
                Denial(name, "requested but not granted by this host", table_entry.permission)
            )
            continue
        if table_entry.permission in HUMAN_ONLY:
            denials.append(Denial(name, "maps to a human-only permission", table_entry.permission))
            continue
        if permitted is not None and table_entry.permission not in permitted:
            denials.append(
                Denial(
                    name,
                    f"principal lacks the '{table_entry.permission}' permission",
                    table_entry.permission,
                )
            )
            continue
        allowed.add(name)

    return Decision(
        requested=requested,
        granted=frozenset(allowed),
        denials=tuple(denials),
        principal=principal,
        policy=policy,
    )


def source_digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def assert_checked_source(manifest: Mapping[str, Any], source: str) -> str:
    """The host runs the program it checked, and only that program.

    The manifest pins the digest of the exact UTF-8 bytes that were checked.
    Re-checking that digest here is what makes "the checked program is the
    executed program" a property rather than a hope; a replay or a queued run
    that swapped the file fails closed.
    """
    pinned = manifest.get("source")
    pinned_digest = pinned.get("sha256") if isinstance(pinned, Mapping) else None
    actual = source_digest(source)
    if pinned_digest != actual:
        raise DigestMismatch(
            "the manifest was built for different source bytes "
            f"(manifest {str(pinned_digest)[:12]}…, supplied {actual[:12]}…)",
            detail={"manifest_sha256": pinned_digest, "source_sha256": actual},
        )
    return actual
