"""A1-S3 — approved model routing, opt-in fallback, tenant cache, comparison, local models.

Pure (no I/O; fetchers/clocks/resolvers are injected). Laws:
- The REQUESTED model is never silently replaced (no forced downgrade, F084).
  A different model is served only when the team's policy explicitly enables
  fallback AND names an approved equivalent, and the swap is attributed.
- An enforced allowlist denies everything not approved; unknown models are
  denied, never mapped to a default.
- Cache entries are keyed by team; a lookup can only ever see its own team's
  entries. Prompts are stored only as SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .decisions import Decision, Outcome
from .egress import classify_ip

MAX_RULES = 64
MAX_EQUIVALENTS = 64
MAX_NAME = 120
REGION_RE = re.compile(r"^[a-z0-9-]{1,20}$")
FALLBACK_ON = frozenset({"unavailable", "rate_limited"})  # never on invalid_key/denied


@dataclass(frozen=True)
class TeamModelPolicy:
    allowlist_enforced: bool = False
    approved_models: frozenset[str] = frozenset()
    routing_rules: tuple[tuple[str, str], ...] = ()  # (taskType, model)
    fallback_enabled: bool = False
    fallback_equivalents: tuple[tuple[str, tuple[str, ...]], ...] = ()
    cache_enabled: bool = False
    cache_ttl_seconds: int = 300
    # A1-S5 data residency: the TEAM declares where each provider processes data;
    # an undeclared provider is "unknown" and is denied while residency is enforced.
    residency_enforced: bool = False
    allowed_regions: frozenset[str] = frozenset()
    provider_regions: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @classmethod
    def from_json(cls, doc: Mapping[str, Any], approved: Iterable[str] = ()) -> TeamModelPolicy:
        """Validate an admin-authored policy document (raises ValueError)."""

        def name(v: Any) -> str:
            if not isinstance(v, str) or not v or len(v) > MAX_NAME:
                raise ValueError("model/task names must be non-empty strings <= 120 chars")
            return v

        rules = doc.get("routingRules", [])
        eq = doc.get("fallbackEquivalents", {})
        if not isinstance(rules, list) or len(rules) > MAX_RULES:
            raise ValueError("routingRules must be a list of <= 64 rules")
        if not isinstance(eq, dict) or len(eq) > MAX_EQUIVALENTS:
            raise ValueError("fallbackEquivalents must be an object of <= 64 entries")
        ttl = doc.get("cacheTtlSeconds", 300)
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not 1 <= ttl <= 86_400:
            raise ValueError("cacheTtlSeconds must be 1..86400")
        parsed_rules = tuple(
            (name(r.get("taskType")), name(r.get("model"))) for r in rules if isinstance(r, dict)
        )
        if len(parsed_rules) != len(rules) or len({t for t, _ in parsed_rules}) != len(rules):
            raise ValueError("routingRules need unique taskType entries")
        parsed_eq = tuple(
            (name(k), tuple(name(x) for x in v[:8]))
            for k, v in sorted(eq.items())
            if isinstance(v, list)
        )
        if len(parsed_eq) != len(eq):
            raise ValueError("fallbackEquivalents values must be lists")
        res = doc.get("residency", {})
        if not isinstance(res, dict):
            raise ValueError("residency must be an object")

        def region(v: Any) -> str:
            if not isinstance(v, str) or not REGION_RE.match(v):
                raise ValueError("regions must match [a-z0-9-]{1,20}")
            return v

        allowed = res.get("allowedRegions", [])
        prov = res.get("providerRegions", {})
        if not isinstance(allowed, list) or len(allowed) > 20:
            raise ValueError("allowedRegions must be a list of <= 20")
        if not isinstance(prov, dict) or len(prov) > 50:
            raise ValueError("providerRegions must be an object of <= 50 providers")
        parsed_prov = tuple(
            (name(k), tuple(region(r) for r in v[:20]))
            for k, v in sorted(prov.items())
            if isinstance(v, list)
        )
        if len(parsed_prov) != len(prov):
            raise ValueError("providerRegions values must be lists")
        enforced = bool(res.get("enforced", False))
        if enforced and not allowed:
            raise ValueError("enforced residency needs at least one allowedRegions entry")
        return cls(
            residency_enforced=enforced,
            allowed_regions=frozenset(region(r) for r in allowed),
            provider_regions=parsed_prov,
            allowlist_enforced=bool(doc.get("allowlistEnforced", False)),
            approved_models=frozenset(approved),
            routing_rules=parsed_rules,
            fallback_enabled=bool(doc.get("fallbackEnabled", False)),
            fallback_equivalents=parsed_eq,
            cache_enabled=bool(doc.get("cacheEnabled", False)),
            cache_ttl_seconds=ttl,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "allowlistEnforced": self.allowlist_enforced,
            "routingRules": [{"taskType": t, "model": m} for t, m in self.routing_rules],
            "fallbackEnabled": self.fallback_enabled,
            "fallbackEquivalents": {k: list(v) for k, v in self.fallback_equivalents},
            "cacheEnabled": self.cache_enabled,
            "cacheTtlSeconds": self.cache_ttl_seconds,
            "residency": {
                "enforced": self.residency_enforced,
                "allowedRegions": sorted(self.allowed_regions),
                "providerRegions": {k: list(v) for k, v in self.provider_regions},
            },
        }


OPEN_POLICY = TeamModelPolicy()


def evaluate_model(policy: TeamModelPolicy, model: str, catalog: Iterable[str]) -> Decision:
    if model not in set(catalog):
        return Decision(Outcome.DENY, "model.unknown", details={"model": model})
    if policy.allowlist_enforced and model not in policy.approved_models:
        return Decision(Outcome.DENY, "model.not_approved", details={"model": model})
    if policy.residency_enforced:
        provider = model.split("/", 1)[0]
        declared = dict(policy.provider_regions).get(provider)
        if not declared:
            return Decision(Outcome.DENY, "model.residency_unknown", details={"provider": provider})
        if not set(declared) <= policy.allowed_regions:
            return Decision(Outcome.DENY, "model.residency_denied", details={"provider": provider})
    return Decision(Outcome.ALLOW, "model.allowed", details={"model": model})


def resolve_model(
    policy: TeamModelPolicy, *, requested: str | None, task_type: str | None, role_default: str
) -> dict[str, str]:
    """Pick the model: explicit request > team routing rule > role default."""
    if requested:
        return {"model": requested, "source": "requested"}
    if task_type:
        for t, m in policy.routing_rules:
            if t == task_type:
                return {"model": m, "source": "routing_rule", "taskType": task_type}
    return {"model": role_default, "source": "role_default"}


def plan_fallback(
    policy: TeamModelPolicy, *, requested: str, failure: str, catalog: Iterable[str]
) -> dict[str, str] | None:
    """Attributed opt-in substitution, or None (the honest failure stands)."""
    if not policy.fallback_enabled or failure not in FALLBACK_ON:
        return None
    known = set(catalog)
    for src, alts in policy.fallback_equivalents:
        if src != requested:
            continue
        for alt in alts:
            if alt != requested and evaluate_model(policy, alt, known).outcome is Outcome.ALLOW:
                return {"requestedModel": requested, "servedModel": alt, "reason": failure}
    return None


# ------------------------------------------------------------------ tenant cache


def cache_key(model: str, messages: Sequence[Mapping[str, str]], opts: Mapping[str, Any]) -> str:
    blob = json.dumps([model, list(messages), dict(opts)], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class TenantCache:
    """Bounded TTL cache; every operation is scoped to a team id."""

    def __init__(self, max_entries: int = 1024, clock: Callable[[], float] = time.monotonic):
        self._max = max_entries
        self._clock = clock
        self._data: OrderedDict[tuple[str, str], tuple[float, str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, team_id: str, key: str) -> Any | None:
        if not team_id:
            return None
        with self._lock:
            hit = self._data.get((team_id, key))
            if hit is None:
                return None
            if hit[0] <= self._clock():
                self._data.pop((team_id, key), None)
                return None
            return hit[2]

    def put(self, team_id: str, key: str, value: Any, *, ttl: int, model: str = "") -> bool:
        if not team_id or ttl <= 0:
            return False
        with self._lock:
            self._data[(team_id, key)] = (self._clock() + ttl, model, value)
            self._data.move_to_end((team_id, key))
            while len(self._data) > self._max:
                self._data.popitem(last=False)
        return True

    def invalidate_team(self, team_id: str, model: str | None = None) -> int:
        with self._lock:
            doomed = [
                k
                for k, v in self._data.items()
                if k[0] == team_id and (model is None or v[1] == model)
            ]
            for k in doomed:
                del self._data[k]
            return len(doomed)

    def size(self) -> int:
        with self._lock:
            return len(self._data)


# ------------------------------------------------------------------ comparison


def score_response(
    text: str, *, expected_contains: Sequence[str] = (), pattern: str | None = None
) -> float | None:
    """Deterministic 0..1 quality score; None when no criterion was supplied."""
    checks: list[bool] = [s.lower() in text.lower() for s in expected_contains]
    if pattern is not None:
        checks.append(re.search(pattern, text) is not None)
    if not checks:
        return None
    return sum(checks) / len(checks)


def compare_models(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rank same-task results: scored rows by quality desc then cost asc.

    Errored or unscored rows are listed with ``rank=None`` — they are never
    ranked against scored output (no quality is invented).
    """
    out = [dict(r, rank=None) for r in rows]
    scored = [r for r in out if r.get("error") is None and r.get("score") is not None]
    scored.sort(key=lambda r: (-r["score"], r["costUsd"], r["latencyMs"]))
    for i, r in enumerate(scored, 1):
        r["rank"] = i
    for r in out:
        cost = r.get("costUsd") or 0
        r["qualityPerDollar"] = (
            round(r["score"] / cost, 4) if r.get("score") is not None and cost > 0 else None
        )
    return out


# ------------------------------------------------------------------ local models


def validate_local_endpoint(
    base_url: str, *, resolver: Callable[[str], Sequence[str]], operator_hosts: Iterable[str] = ()
) -> Decision:
    """Loopback is allowed; private only for operator-listed hosts; metadata/link-local never."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(base_url)
        host, _port = parts.hostname, parts.port
    except ValueError:
        return Decision(Outcome.DENY, "local.malformed_url")
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return Decision(Outcome.DENY, "local.bad_url")
    try:
        ips = list(resolver(host))
    except OSError:
        return Decision(Outcome.DENY, "local.unresolvable")
    if not ips:
        return Decision(Outcome.DENY, "local.unresolvable")
    allowed_hosts = {h.lower() for h in operator_hosts}
    for ip in ips:
        cls = classify_ip(ip)
        if cls == "loopback":
            continue
        if cls == "private" and host.lower() in allowed_hosts:
            continue
        return Decision(Outcome.DENY, f"local.{cls}_denied")
    return Decision(Outcome.ALLOW, "local.endpoint_ok")


def parse_local_models(payload: Any, *, limit: int = 200) -> list[str]:
    """Models from an OpenAI-compatible ``/models`` or Ollama ``/api/tags`` body."""
    names: list[str] = []
    if isinstance(payload, dict):
        for item in payload.get("data") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                names.append(item["id"])
        for item in payload.get("models") or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
    clean = [n for n in dict.fromkeys(names) if 0 < len(n) <= MAX_NAME]
    return clean[:limit]
