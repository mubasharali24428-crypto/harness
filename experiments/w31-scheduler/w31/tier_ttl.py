"""W31 extension — memory tier placement proposals (hot / warm / cold + TTL), CP-SAT.

PROPOSAL ONLY (docs/decisions/2026-11-04-w31-scheduler.md): the output says where memory items
*could* live; the orchestrator stays authoritative and a human approves. Nothing here moves,
injects or deletes memory (CD-02).

* hot  = what memory_loader may inject: <= MAX_MEMORY_ENTRIES items and <= MAX_TOTAL_BYTES.
* warm = cheap to promote: <= WARM_FACTOR x the hot caps. cold = unbounded.
* TTL: every hot item gets the 24h base window. 48h is the paid extension and is proposed only
  when the item carries ``billing_approved=True`` (default False). Warm/cold carry no TTL.
* ``validate`` is independent of the model: it re-checks a proposal against the request.
"""

from __future__ import annotations

from ortools.sat.python import cp_model

# Mirrors section4 harness_orchestrator/memory_loader.py (drift-checked in tests).
MAX_MEMORY_ENTRIES = 20
MAX_TOTAL_BYTES = 32 * 1024
WARM_FACTOR = 4
TIERS = ("hot", "warm", "cold")
VALUE = {"hot": 3, "warm": 2, "cold": 1}  # per hit/day; hot is worth most
BASE_TTL_H, PAID_TTL_H = 24, 48
SCHEMA = "w31-tier-ttl/1"  # stable wire tag for consumers (section4 scheduler_proposal.py)


def _caps() -> dict[str, tuple[int, int] | None]:
    return {"hot": (MAX_MEMORY_ENTRIES, MAX_TOTAL_BYTES),
            "warm": (MAX_MEMORY_ENTRIES * WARM_FACTOR, MAX_TOTAL_BYTES * WARM_FACTOR),
            "cold": None}


def solve(items: list[dict], time_limit_s: float = 5.0) -> dict:
    """items: [{id, bytes, hits_per_day, billing_approved?}] -> proposal dict."""
    items = sorted(items, key=lambda i: i["id"])  # deterministic model order
    m = cp_model.CpModel()
    x = {(i["id"], t): m.NewBoolVar(f"{i['id']}@{t}") for i in items for t in TIERS}
    for i in items:
        m.AddExactlyOne(x[i["id"], t] for t in TIERS)
    for t, cap in _caps().items():
        if cap:
            m.Add(sum(x[i["id"], t] for i in items) <= cap[0])
            m.Add(sum(i["bytes"] * x[i["id"], t] for i in items) <= cap[1])
    m.Maximize(sum(VALUE[t] * i["hits_per_day"] * x[i["id"], t] for i in items for t in TIERS))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 1  # reproducible proposals
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"schema": SCHEMA, "status": "unknown", "placements": [], "proposalOnly": True,
                "requiresHumanApproval": True}
    placements = []
    for i in items:
        tier = next(t for t in TIERS if solver.Value(x[i["id"], t]))
        ttl = None
        if tier == "hot":
            ttl = PAID_TTL_H if i.get("billing_approved") is True else BASE_TTL_H
        placements.append({"id": i["id"], "tier": tier, "ttl_hours": ttl})
    return {"schema": SCHEMA, "status": "optimal" if status == cp_model.OPTIMAL else "feasible",
            "placements": placements, "proposalOnly": True, "requiresHumanApproval": True}


def validate(items: list[dict], proposal: dict) -> list[str]:
    """Independent re-check. Empty list = zero violations."""
    by_id = {i["id"]: i for i in items}
    seen = [p["id"] for p in proposal.get("placements", [])]
    v = []
    if sorted(seen) != sorted(by_id):
        v.append("placements must cover every item exactly once")
    if proposal.get("proposalOnly") is not True or proposal.get("requiresHumanApproval") is not True:
        v.append("output must be marked proposal-only and human-approved")
    for t, cap in _caps().items():
        chosen = [p for p in proposal.get("placements", []) if p["tier"] == t and p["id"] in by_id]
        if cap and (len(chosen) > cap[0] or sum(by_id[p["id"]]["bytes"] for p in chosen) > cap[1]):
            v.append(f"{t} tier over cap")
    for p in proposal.get("placements", []):
        if p["tier"] not in TIERS:
            v.append(f"{p['id']}: unknown tier")
        elif p["tier"] != "hot" and p["ttl_hours"] is not None:
            v.append(f"{p['id']}: only hot items carry a TTL")
        elif p["tier"] == "hot" and p["ttl_hours"] not in (BASE_TTL_H, PAID_TTL_H):
            v.append(f"{p['id']}: hot TTL must be 24 or 48")
        elif p["ttl_hours"] == PAID_TTL_H and by_id.get(p["id"], {}).get("billing_approved") is not True:
            v.append(f"{p['id']}: 48h needs billing_approved")
    return v
