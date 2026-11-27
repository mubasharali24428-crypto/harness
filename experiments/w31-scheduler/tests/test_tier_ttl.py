import random
import re
from pathlib import Path

import pytest

from w31 import tier_ttl
from w31.tier_ttl import solve, validate

LOADER = Path(__file__).resolve().parents[3] / "section4/src/harness_orchestrator/memory_loader.py"


def items(n: int, seed: int = 7, paid_every: int = 0) -> list[dict]:
    rng = random.Random(seed)
    return [{"id": f"m{k:03d}", "bytes": rng.randint(200, 4000), "hits_per_day": rng.randint(0, 50),
             "billing_approved": bool(paid_every) and k % paid_every == 0} for k in range(n)]


@pytest.mark.parametrize("n", [10, 50, 100])
def test_zero_violations_on_fixtures(n):
    req = items(n, paid_every=5)
    out = solve(req)
    assert out["status"] in ("optimal", "feasible")
    assert validate(req, out) == []


def test_48h_only_with_billing_approval_default_false():
    req = items(30)  # nobody approved billing
    out = solve(req)
    assert {p["ttl_hours"] for p in out["placements"] if p["tier"] == "hot"} == {24}
    forged = {**out, "placements": [{**p, "ttl_hours": 48} if p["tier"] == "hot" else p
                                    for p in out["placements"]]}
    assert any("billing_approved" in v for v in validate(req, forged))


def test_validator_catches_cap_breach_and_missing_proposal_flag():
    req = items(30)
    all_hot = {"placements": [{"id": i["id"], "tier": "hot", "ttl_hours": 24} for i in req],
               "proposalOnly": False, "requiresHumanApproval": True}
    v = validate(req, all_hot)
    assert "hot tier over cap" in v and any("proposal-only" in x for x in v)


def test_hot_prefers_most_used_items():
    req = [{"id": f"x{k}", "bytes": 100, "hits_per_day": k} for k in range(25)]
    hot = {p["id"] for p in solve(req)["placements"] if p["tier"] == "hot"}
    assert hot == {f"x{k}" for k in range(5, 25)}  # the 20 busiest


def test_caps_match_memory_loader():
    src = LOADER.read_text()
    assert int(re.search(r"MAX_MEMORY_ENTRIES = (\d+)", src).group(1)) == tier_ttl.MAX_MEMORY_ENTRIES
    assert "MAX_TOTAL_BYTES = 32 * 1024" in src and tier_ttl.MAX_TOTAL_BYTES == 32 * 1024
