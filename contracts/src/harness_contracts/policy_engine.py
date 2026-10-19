"""Part 38 - the PURE policy engine (no I/O, no clock reads, no randomness).

Lives in ``harness_contracts`` so the identity service (which owns policy storage and
administration) and the orchestrator (which may import only contracts) evaluate the very
same precedence rules. Same inputs always give the same ``Decision``; the rule is documented
in ``docs/permissions.md`` and summarised on :func:`evaluate`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

LEVELS: tuple[str, ...] = ("org", "team", "project", "workspace", "run")
PERMISSIONS: tuple[str, ...] = (
    "read",
    "comment",
    "steer",
    "approve",
    "administer",
    "export",
    "billing",
)
_ALL = frozenset(PERMISSIONS)

# role -> permissions. billing is separate: only owner and billing_admin hold it.
ROLE_DEFAULTS: dict[str, frozenset[str]] = {
    "owner": _ALL,
    "admin": _ALL - {"billing"},
    "billing_admin": frozenset({"read", "billing"}),
    "member": frozenset({"read", "comment", "steer"}),
    "engineer": frozenset({"read", "comment", "steer", "approve"}),
    "viewer": frozenset({"read", "comment"}),
    "guest": frozenset({"read"}),
}

# Actions that may require a recent re-authentication (org policy can replace the list).
STEPUP_ACTIONS: frozenset[str] = frozenset(
    {"policy.save", "role.change", "export", "emergency.grant"}
)
DEFAULT_STEPUP_ACTIONS = tuple(sorted(STEPUP_ACTIONS))
DEFAULT_STEPUP_MAX_AGE = 300
DEFAULT_EMERGENCY_MAX_MINUTES = 240
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
_SUBJECT_RE = re.compile(r"^(\*|user:[^\s]{1,64}|role:[a-z][a-z0-9_-]{1,31}|kind:(human|agent))$")


class PolicyError(ValueError):
    """Invalid policy input (maps to HTTP 422)."""


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    effect: str = Field(pattern="^(allow|deny)$")
    permissions: tuple[str, ...] = Field(min_length=1)
    subjects: tuple[str, ...] = ("*",)
    # Only meaningful for deny: a deeper level may override it with an explicit allow.
    overridable: bool = False

    @field_validator("permissions")
    @classmethod
    def _perms(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if "*" in v:
            return PERMISSIONS
        bad = [p for p in v if p not in _ALL]
        if bad:
            raise ValueError(f"unknown permission(s): {bad}")
        return v

    @field_validator("subjects")
    @classmethod
    def _subjects(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        bad = [s for s in v if not _SUBJECT_RE.match(s)]
        if bad or not v:
            raise ValueError(f"bad subject pattern(s): {bad}")
        return v


class PolicyDoc(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rules: tuple[Rule, ...] = Field(default=(), max_length=100)
    # org-level only: may team/project/workspace/run policies ADD allows? (default no)
    allow_lower_grants: bool = False
    stepup_actions: tuple[str, ...] = DEFAULT_STEPUP_ACTIONS
    stepup_max_age_seconds: int = Field(default=DEFAULT_STEPUP_MAX_AGE, ge=30, le=3600)
    emergency_max_minutes: int = Field(default=DEFAULT_EMERGENCY_MAX_MINUTES, ge=5, le=1440)

    @field_validator("rules")
    @classmethod
    def _unique(cls, v: tuple[Rule, ...]) -> tuple[Rule, ...]:
        ids = [r.id for r in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids")
        return v

    @field_validator("stepup_actions")
    @classmethod
    def _actions(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        bad = [a for a in v if a not in STEPUP_ACTIONS]
        if bad:
            raise ValueError(f"unknown step-up action(s): {bad}")
        return v


@dataclass(frozen=True)
class Node:
    level: str
    id: str


@dataclass(frozen=True)
class Binding:
    """Subject holds ``role`` on the resource node (level, scope_id) and below."""

    level: str
    scope_id: str
    role: str


@dataclass(frozen=True)
class Subject:
    user_id: str
    bindings: tuple[Binding, ...] = ()
    kind: str = "human"  # "human" | "agent"


@dataclass(frozen=True)
class EmergencyGrant:
    id: str
    user_id: str
    level: str
    scope_id: str
    permissions: frozenset[str]
    starts_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class RuleRef:
    rule_id: str
    level: str
    scope_id: str
    effect: str
    source: str  # role | policy | emergency
    status: str  # decisive | superseded | ignored


@dataclass(frozen=True)
class Decision:
    allowed: bool
    permission: str
    reason: str
    chain: tuple[RuleRef, ...] = ()

    @property
    def rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.chain if r.status == "decisive"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "permission": self.permission,
            "reason": self.reason,
            "chain": [asdict(r) for r in self.chain],
        }


Docs = Mapping[tuple[str, str], PolicyDoc]


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def validate_chain(chain: Sequence[Node]) -> None:
    if not chain or chain[0].level != "org":
        raise PolicyError("resource chain must start at the org level")
    idx = []
    for n in chain:
        if n.level not in LEVELS or not n.id:
            raise PolicyError(f"bad chain node {n}")
        idx.append(LEVELS.index(n.level))
    if idx != sorted(set(idx)):
        raise PolicyError("chain levels must be strictly descending in scope: org>team>...>run")


def role_permissions(
    role: str, custom_roles: Mapping[str, frozenset[str]] | None = None
) -> frozenset[str]:
    if role in ROLE_DEFAULTS:
        return ROLE_DEFAULTS[role]
    return (custom_roles or {}).get(role, frozenset())


@dataclass
class _Cand:
    rid: str
    lvl: int
    level: str
    scope: str
    effect: str
    source: str
    overridable: bool = False
    eligible: bool = True


def evaluate(
    subject: Subject,
    permission: str,
    chain: Sequence[Node],
    docs: Docs | None = None,
    custom_roles: Mapping[str, frozenset[str]] | None = None,
    emergency: Iterable[EmergencyGrant] = (),
    *,
    now: datetime,
) -> Decision:
    """Pure decision. Precedence (full text in docs/permissions.md):

    1. default deny; 2. a deny that is not ``overridable`` at ANY level wins over
    everything; 3. an ``overridable`` deny stands unless an eligible allow exists at a
    STRICTLY deeper level; 4. allows come from role bindings (membership), emergency
    grants, and policy allow rules - but a policy allow below the org level is only
    eligible when the org policy sets ``allow_lower_grants`` (otherwise lower levels
    can only narrow); 5. no deny standing and >=1 eligible allow => allow.
    """
    if permission not in _ALL:
        raise PolicyError(f"unknown permission {permission!r}")
    validate_chain(chain)
    docs = docs or {}
    now = aware(now)
    in_chain = {(n.level, n.id) for n in chain}
    roles = sorted(
        {
            (b.level, b.scope_id, b.role)
            for b in subject.bindings
            if (b.level, b.scope_id) in in_chain
        }
    )
    role_names = {r[2] for r in roles}
    org_doc = docs.get(("org", chain[0].id))
    allow_lower = bool(org_doc and org_doc.allow_lower_grants)

    cands: list[_Cand] = []
    for level, scope, role in roles:
        if permission in role_permissions(role, custom_roles):
            cands.append(
                _Cand(
                    f"role:{role}@{level}:{scope}",
                    LEVELS.index(level),
                    level,
                    scope,
                    "allow",
                    "role",
                )
            )
    for g in emergency:
        if (
            g.user_id == subject.user_id
            and (g.level, g.scope_id) in in_chain
            and permission in g.permissions
            and aware(g.starts_at) <= now < aware(g.expires_at)
        ):
            cands.append(
                _Cand(
                    f"emergency:{g.id}",
                    LEVELS.index(g.level),
                    g.level,
                    g.scope_id,
                    "allow",
                    "emergency",
                )
            )
    for node in chain:
        doc = docs.get((node.level, node.id))
        for rule in doc.rules if doc else ():
            if permission not in rule.permissions:
                continue
            if not any(_subject_match(p, subject, role_names) for p in rule.subjects):
                continue
            lvl = LEVELS.index(node.level)
            eligible = rule.effect == "deny" or node.level == "org" or allow_lower
            cands.append(
                _Cand(
                    f"policy:{node.level}:{rule.id}",
                    lvl,
                    node.level,
                    node.id,
                    rule.effect,
                    "policy",
                    rule.overridable and rule.effect == "deny",
                    eligible,
                )
            )
    cands.sort(key=lambda c: (c.lvl, c.rid))

    denies = [c for c in cands if c.effect == "deny"]
    allows = [c for c in cands if c.effect == "allow" and c.eligible]
    final = [d for d in denies if not d.overridable]
    if final:
        allowed, decisive, why = False, final, f"explicit deny at {final[0].level} ({final[0].rid})"
    elif denies:
        dl = max(d.lvl for d in denies)
        over = [a for a in allows if a.lvl > dl]
        if over:
            allowed, decisive, why = (
                True,
                over,
                f"allow at {over[-1].level} overrides overridable deny",
            )
        else:
            allowed, decisive, why = (
                False,
                denies,
                f"overridable deny at {denies[-1].level} not overridden",
            )
    elif allows:
        allowed, decisive, why = True, allows, f"allowed by {allows[0].rid}"
    else:
        allowed, decisive, why = False, [], "no rule grants this permission (default deny)"
    ids = {id(c) for c in decisive}
    ref = tuple(
        RuleRef(
            c.rid,
            c.level,
            c.scope,
            c.effect,
            c.source,
            "decisive" if id(c) in ids else ("ignored" if not c.eligible else "superseded"),
        )
        for c in cands
    )
    return Decision(allowed, permission, why, ref)


def _subject_match(pattern: str, s: Subject, role_names: set[str]) -> bool:
    if pattern == "*":
        return True
    kind, _, val = pattern.partition(":")
    return (
        (kind == "user" and val == s.user_id)
        or (kind == "role" and val in role_names)
        or (kind == "kind" and val == s.kind)
    )


def evaluate_delegated(
    agent: Subject,
    delegator: Subject,
    permission: str,
    chain: Sequence[Node],
    docs: Docs | None = None,
    custom_roles: Mapping[str, frozenset[str]] | None = None,
    emergency: Iterable[EmergencyGrant] = (),
    *,
    now: datetime,
) -> Decision:
    """Agent-generated action: allowed only if the agent AND the human who launched it
    both hold the permission (an agent can never exceed its delegator)."""
    emergency = tuple(emergency)
    a = evaluate(agent, permission, chain, docs, custom_roles, emergency, now=now)
    d = evaluate(delegator, permission, chain, docs, custom_roles, emergency, now=now)
    if a.allowed and d.allowed:
        return a
    who = "agent" if not a.allowed else "delegating user"
    return Decision(
        False,
        permission,
        f"{who} lacks {permission}: {(a if not a.allowed else d).reason}",
        a.chain,
    )


def missing_for_grant(
    grantor: Subject,
    wanted: Iterable[str],
    chain: Sequence[Node],
    docs: Docs | None = None,
    custom_roles: Mapping[str, frozenset[str]] | None = None,
    emergency: Iterable[EmergencyGrant] = (),
    *,
    now: datetime,
) -> list[str]:
    """Anti-escalation: permissions in ``wanted`` that the grantor does NOT hold at ``chain``.
    Empty list == grant is allowed. Use for invitations, role assignments, handoffs,
    custom roles and emergency approvals."""
    emergency = tuple(emergency)
    return sorted(
        p
        for p in set(wanted)
        if not evaluate(grantor, p, chain, docs, custom_roles, emergency, now=now).allowed
    )


def validate_custom_role(name: str, permissions: Iterable[str]) -> frozenset[str]:
    if not _NAME_RE.match(name) or name in ROLE_DEFAULTS:
        raise PolicyError(
            "role name must match [a-z][a-z0-9_-]{2,31} and not shadow a built-in role"
        )
    perms = frozenset(permissions)
    if not perms or not perms <= _ALL:
        raise PolicyError(f"permissions must be a non-empty subset of {list(PERMISSIONS)}")
    return perms


def stepup_required(action: str, doc: PolicyDoc | None) -> bool:
    actions = doc.stepup_actions if doc else DEFAULT_STEPUP_ACTIONS
    return action in actions


def stepup_fresh(stepup_at: datetime | None, now: datetime, max_age_seconds: int) -> bool:
    return stepup_at is not None and timedelta(0) <= aware(now) - aware(stepup_at) <= timedelta(
        seconds=max_age_seconds
    )
