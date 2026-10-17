"""A1-S4 — pure security analytics: event correlation (F098) + dependency remediation (F099).

No I/O, no network. Correlation reports PATTERNS over audit metadata (action,
actor, time, target id) — never content. Remediation only PROPOSES upgrades from
an advisory feed the caller supplies; it never installs anything and every
proposal is gated on tests + human review.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from packaging.version import InvalidVersion, Version

DENIAL_ACTIONS = frozenset({"policy.denied", "authz.denied", "forbidden.command_blocked"})
CREDENTIAL_ACTIONS = frozenset({"apikey.added", "personal_key.added", "key_share.offered"})
REVOCATION_ACTIONS = frozenset({"apikey.removed", "personal_key.removed", "key_share.revoked"})

DENIAL_BURST = (5, timedelta(minutes=10))
PROBE_THEN_CREDENTIAL = (3, timedelta(minutes=30))
MASS_REVOCATION = (5, timedelta(minutes=10))


def _window_hits(times: Sequence[datetime], n: int, span: timedelta) -> list[int]:
    """Indexes i where times[i-n+1..i] fit within span (times sorted)."""
    return [i for i in range(n - 1, len(times)) if times[i] - times[i - n + 1] <= span]


def correlate(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """events: {id, action, actor, ts(datetime)}. Returns correlation findings."""
    by_actor: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for e in events:
        by_actor[e.get("actor") or "unknown"].append(e)
    out: list[dict[str, Any]] = []
    for actor, evs in by_actor.items():
        evs.sort(key=lambda e: e["ts"])
        denials = [e for e in evs if e["action"] in DENIAL_ACTIONS]
        n, span = DENIAL_BURST
        hits = _window_hits([e["ts"] for e in denials], n, span)
        if hits:
            last = hits[-1]
            out.append(
                {
                    "rule": "repeated_denials",
                    "severity": "high",
                    "actor": actor,
                    "count": len(denials),
                    "firstAt": denials[last - n + 1]["ts"].isoformat(),
                    "evidenceIds": [d["id"] for d in denials[last - n + 1 : last + 1]],
                }
            )
        pn, pspan = PROBE_THEN_CREDENTIAL
        probe_hits = _window_hits([e["ts"] for e in denials], pn, timedelta(minutes=10))
        if probe_hits:
            burst_end = denials[probe_hits[0]]["ts"]
            follow = [
                e
                for e in evs
                if e["action"] in CREDENTIAL_ACTIONS and burst_end <= e["ts"] <= burst_end + pspan
            ]
            if follow:
                out.append(
                    {
                        "rule": "denials_then_credential_change",
                        "severity": "critical",
                        "actor": actor,
                        "evidenceIds": [d["id"] for d in denials[: probe_hits[0] + 1]]
                        + [f["id"] for f in follow],
                    }
                )
        revs = [e for e in evs if e["action"] in REVOCATION_ACTIONS]
        rn, rspan = MASS_REVOCATION
        if _window_hits([e["ts"] for e in revs], rn, rspan):
            out.append(
                {
                    "rule": "mass_revocation",
                    "severity": "medium",
                    "actor": actor,
                    "count": len(revs),
                    "evidenceIds": [r["id"] for r in revs],
                }
            )
    order = {"critical": 0, "high": 1, "medium": 2}
    return sorted(out, key=lambda f: (order[f["severity"]], f["actor"]))


def _ver(text: str) -> Version | None:
    try:
        return Version(text)
    except InvalidVersion:
        return None


def propose_remediation(
    installed: Mapping[str, str], advisories: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Upgrade PROPOSALS for installed packages hit by advisories.

    advisory: {id, package, fixedIn (version), severity?, affectedBelow?}. A package
    is affected when installed < fixedIn (and < affectedBelow when given). Multiple
    advisories for one package collapse to the highest required fix. Unparseable
    versions are reported as ``unresolved`` instead of guessed.
    """
    key = {k.lower().replace("_", "-"): (k, v) for k, v in installed.items()}
    needed: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    for adv in advisories:
        pkg = str(adv.get("package", "")).lower().replace("_", "-")
        if pkg not in key:
            continue
        name, cur_txt = key[pkg]
        cur, fix = _ver(cur_txt), _ver(str(adv.get("fixedIn", "")))
        if cur is None or fix is None:
            unresolved.append(
                {"package": name, "advisoryId": adv.get("id"), "reason": "bad_version"}
            )
            continue
        if cur >= fix:
            continue
        slot = needed.setdefault(
            name,
            {"package": name, "from": cur_txt, "to": str(fix), "advisories": [], "severity": "low"},
        )
        if fix > Version(slot["to"]):
            slot["to"] = str(fix)
        slot["advisories"].append(adv.get("id"))
        rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        sev = adv.get("severity", "low")
        if rank.get(sev, 0) > rank[slot["severity"]]:
            slot["severity"] = sev
    proposals = []
    for slot in sorted(needed.values(), key=lambda s: s["package"]):
        major_jump = Version(slot["to"]).major > Version(slot["from"]).major
        proposals.append(
            {
                **slot,
                "advisories": sorted(set(slot["advisories"])),
                "status": "proposed",
                "autoApply": False,
                "gates": ["tests_pass", "manual_review"]
                + (["major_upgrade_review"] if major_jump else []),
            }
        )
    return proposals + [{"status": "unresolved", **u} for u in unresolved]
