"""F078: merge subagent results; surface conflicts and unsupported claims, never
silently pick a winner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Claim:
    key: str
    value: str
    agent: str
    evidence: tuple[str, ...] = ()


def synthesize(results: list[list[Claim]]) -> dict[str, Any]:
    by_key: dict[str, list[Claim]] = {}
    for claims in results:
        for c in claims:
            by_key.setdefault(c.key, []).append(c)
    agreed: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    unsupported: list[str] = []
    for key, cs in sorted(by_key.items()):
        values = {c.value for c in cs}
        sources = sorted({c.agent for c in cs})
        if len(values) > 1:
            conflicts.append(
                {
                    "key": key,
                    "positions": [
                        {
                            "value": v,
                            "agents": sorted({c.agent for c in cs if c.value == v}),
                            "evidence": sorted({e for c in cs if c.value == v for e in c.evidence}),
                        }
                        for v in sorted(values)
                    ],
                }
            )
            continue
        evidence = sorted({e for c in cs for e in c.evidence})
        if not evidence:
            unsupported.append(key)
        agreed[key] = {
            "value": next(iter(values)),
            "agents": sources,
            "evidence": evidence,
            "corroborated": len(sources) > 1,
        }
    return {
        "agreed": agreed,
        "conflicts": conflicts,
        "unsupported": unsupported,
        "needs_human": bool(conflicts),
    }
