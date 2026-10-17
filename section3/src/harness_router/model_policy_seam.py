"""A1-S3 — where Section 3 consults the team's model policy.

Opt-in wiring: until ``set_policy_provider`` is called the router behaves
exactly as before (no allowlist, no cache, no fallback). Deploy wiring supplies
:func:`pool_policy_provider` (reads Section 1's ``model_allowlist_entries`` and
``team_settings['model_policy']`` through the same asyncpg-style pool the cost
ledger uses). Policy reads are cached per team for ``ttl`` seconds, so an admin
change takes effect within that window; a read failure FAILS CLOSED
(allowlist enforced, nothing approved) rather than open.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from harness_security_policy.model_policy import TeamModelPolicy, TenantCache

PolicyProvider = Callable[[str], Awaitable[TeamModelPolicy]]

_provider: list[PolicyProvider | None] = [None]
TENANT_CACHE = TenantCache()

APPROVED_SQL = (
    "SELECT model FROM model_allowlist_entries WHERE team_id = $1 AND status = 'approved'"
)
POLICY_SQL = "SELECT value FROM team_settings WHERE team_id = $1 AND key = 'model_policy'"


def set_policy_provider(provider: PolicyProvider | None) -> None:
    _provider[0] = provider


def get_policy_provider() -> PolicyProvider | None:
    return _provider[0]


def pool_policy_provider(
    pool: Any, *, ttl: float = 15.0, clock: Callable[[], float] = time.monotonic
) -> PolicyProvider:
    cache: dict[str, tuple[float, TeamModelPolicy]] = {}

    async def provider(team_id: str) -> TeamModelPolicy:
        hit = cache.get(team_id)
        if hit and hit[0] > clock():
            return hit[1]
        try:
            rows = await pool.fetch(APPROVED_SQL, team_id)
            raw = await pool.fetch(POLICY_SQL, team_id)
            approved = [r["model"] for r in rows]
            value = raw[0]["value"] if raw else None
            policy = (
                TeamModelPolicy(approved_models=frozenset(approved))
                if value is None
                else TeamModelPolicy.from_json(json.loads(value), approved=approved)
            )
        except Exception:  # noqa: BLE001 — unreadable/corrupt policy => closed, not open
            return TeamModelPolicy(allowlist_enforced=True)
        cache[team_id] = (clock() + ttl, policy)
        return policy

    return provider
