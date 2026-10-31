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
import threading
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


@dataclass(frozen=True)
class Revision:
    """One change the host made to a live grant set, and why.

    Recorded because "the host revoked ``tool.test`` at 14:02" is a fact a reviewer
    needs, and it is only knowable if the revocation carries its own reason.
    """

    capability: str
    action: str
    reason: str

    def to_json(self) -> dict[str, str]:
        return {"capability": self.capability, "action": self.action, "reason": self.reason}


class RevisableDecision:
    """A ``Decision`` the host can change between calls: M6's revocation surface.

    A frozen ``Decision`` is the right shape for a run that starts and finishes
    under one grant set. It is the wrong shape for a workflow that is already
    running, because withdrawing authority mid-flight would then mean cancelling
    and restarting — which loses the work the run was allowed to finish.

    The runtime already asks ``allows()`` at every call site rather than once at
    the start, so revocation needs only a decision whose answer can change. It
    does not need a new check, and a capability revoked between two crossings
    denies the second one and never touches the first.

    The request set is fixed: this class can narrow what is granted, never widen
    what the program asked for. A host that wants more authority re-authorize a
    manifest; a run cannot grow its own ceiling by waiting.
    """

    def __init__(
        self,
        *,
        requested: frozenset[str],
        granted: frozenset[str],
        denials: tuple[Denial, ...],
        principal: str,
        policy: str,
    ) -> None:
        self.requested = requested
        self.denials = denials
        self.principal = principal
        self.policy = policy
        self._granted: set[str] = set(granted)
        #: What the host granted when the run was authorised. A denial is only a
        #: revocation if the capability was in this set; ``granted`` has moved on.
        self.initial_granted: frozenset[str] = frozenset(granted)
        self._lock = threading.Lock()
        self._revisions: list[Revision] = []
        self._reported = 0

    @property
    def granted(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._granted)

    @property
    def allowed(self) -> bool:
        """The original authorisation answer.

        A revocation does not rewrite history: the run *was* authorised, and what
        changed is the grant set. ``allows()`` is the live question, ``allowed``
        is the record of the one asked at the start.
        """
        return not self.denials

    def allows(self, capability: str | None) -> bool:
        if capability is None:
            return True
        with self._lock:
            return capability in self._granted

    def revoke(self, capability: str, *, reason: str = "host revoked it") -> bool:
        """Withdraw one capability from here on. False if it was not granted."""
        with self._lock:
            if capability not in self._granted:
                return False
            self._granted.discard(capability)
            self._revisions.append(Revision(capability, "revoked", reason))
        return True

    def restore(self, capability: str, *, reason: str = "host restored it") -> bool:
        """Grant a capability again — only one the program actually requested."""
        with self._lock:
            if capability in self._granted or capability not in self.requested:
                return False
            self._granted.add(capability)
            self._revisions.append(Revision(capability, "restored", reason))
        return True

    def revisions(self) -> tuple[Revision, ...]:
        with self._lock:
            return tuple(self._revisions)

    def pending_revisions(self) -> list[Revision]:
        """Revisions the runtime has not yet turned into events.

        Drained at call sites, so the event log tells the story in the order the
        run learned it: a revocation appears between the crossings it separated.
        """
        with self._lock:
            fresh = self._revisions[self._reported :]
            self._reported = len(self._revisions)
            return list(fresh)

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
            "live": True,
            "initiallyGranted": sorted(self.initial_granted),
            "revisions": [revision.to_json() for revision in self.revisions()],
        }


def as_revisable(decision: Decision) -> RevisableDecision:
    """Hand a host an authority it can walk back without restarting the run."""
    return RevisableDecision(
        requested=decision.requested,
        granted=decision.granted,
        denials=decision.denials,
        principal=decision.principal,
        policy=decision.policy,
    )


#: The runtime asks one question of an authority object; both shapes answer it.
Authorisation = Decision | RevisableDecision


def authority_problems(authority: Authorisation, manifest: Mapping[str, Any]) -> list[str]:
    """Why a decision handed in from outside must not be treated as authority.

    Mid-run revocation moves the decision object outside ``run_source``, and that
    opens two mistakes a caller can make: handing over an authority minted for a
    different program, or one that grants a capability mapping to a human-only
    permission. Both are checked here rather than trusted, because the invariant
    "a program never holds approve/administer/billing/export" must hold on every
    path into the runtime, not only the path that happens to call ``authorize``.
    """
    problems: list[str] = []
    requested = requested_capabilities(manifest)
    if frozenset(authority.requested) != requested:
        missing = sorted(requested - frozenset(authority.requested))
        extra = sorted(frozenset(authority.requested) - requested)
        problems.append(
            f"the decision was made for a different request (unrequested: {extra}, "
            f"not-requested-by-the-program: {missing})"
        )
    for capability in authority.granted:
        entry = CAPABILITIES.get(capability)
        if entry is None:
            problems.append(f"'{capability}' is not in the closed capability table")
        elif entry.permission in HUMAN_ONLY:
            problems.append(f"'{capability}' maps to the human-only permission "
                            f"'{entry.permission}'")
    return problems


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
