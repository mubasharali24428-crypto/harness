"""W18 slice 1 — the pricing page may only claim what the code enforces.

`PRICING.md` said it was "mapped to real code" and advertised 50 runs/day, 5k
context chunks, tier-scaled concurrency (4/8/16) and seat caps. The code said 20,
2000, one deploy-wide concurrency default, and no seat cap anywhere. A launch
page that overstates the product is the cheapest way to fail a pilot, so the page
is machine-checked here: the tier table is parsed and compared field by field with
`harness_contracts.tiers.tier_caps`, and the platform table with the constants the
doors actually read. Change a cap without the page, and `make test-section1` says
so.

The tables carry HTML-comment markers so the rest of the prose can change freely
without weakening the check.
"""

import json
import re
from pathlib import Path

import pytest
from harness_contracts.tiers import PLAN_TIERS, tier_caps

from harness_identity.scim import MAX_TOKENS_PER_TEAM
from harness_identity.siem_sink import MAX_SINKS_PER_TEAM
from harness_identity.workspaces import DEFAULT_MAX_PER_TEAM

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "PRICING.md"
SERVICES = ROOT / "deploy" / "runners" / "services.py"
ENGINE_RUNTIME = (
    ROOT / "section4" / "src" / "harness_orchestrator" / "engine_runtime.py"
)
UI_PRICING = ROOT / "section7" / "src" / "lib" / "pricing.json"


def _block(name: str, *, width: int) -> dict[str, list[str]]:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(rf"<!-- pricing:{name} -->(.*?)<!-- /pricing:{name} -->", text, re.S)
    assert match, f"PRICING.md must carry a machine-checkable <!-- pricing:{name} --> block"
    rows: dict[str, list[str]] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != width or set(cells[0]) <= set("-: ") or cells[0].lower() in {
            "field",
            "limit",
        }:
            continue
        rows[cells[0]] = cells[1:]
    assert rows, f"the {name} block parsed no rows"
    return rows


def _int_or_none(text: str, field: str) -> int | None:
    value = text.strip().lower()
    assert value in {"unlimited", "none", "n/a"} or value.isdigit(), (
        f"{field}: {text!r} must be a number or the word 'unlimited'"
    )
    return None if not value.isdigit() else int(value)


@pytest.fixture(scope="module")
def caps() -> dict[str, list[str]]:
    return _block("caps", width=4)


def test_the_page_has_a_row_for_every_tier_cap_field(caps):
    fields = set(tier_caps("free").to_wire()) - {"tier", "upgradeTo"}
    assert fields <= set(caps), f"missing from PRICING.md: {sorted(fields - set(caps))}"


@pytest.mark.parametrize("index,tier", list(enumerate(PLAN_TIERS)))
def test_each_tier_column_matches_tier_caps(caps, index, tier):
    """The enforced caps and the descriptive facts, cell by cell."""
    effective = tier_caps(tier).to_wire()
    for field, cells in caps.items():
        documented = _int_or_none(cells[index], field)
        assert documented == effective[field], (
            f"{tier}.{field}: PRICING.md says {cells[index]!r}, code says"
            f" {effective[field]!r}"
        )


def test_the_platform_table_matches_the_doors():
    rows = _block("platform", width=3)

    def value(name: str) -> int:
        return int(rows[name][0])

    assert value("siemSinksPerTeam") == MAX_SINKS_PER_TEAM
    assert value("scimTokensPerTeam") == MAX_TOKENS_PER_TEAM
    assert value("workspacesPerTeam") == DEFAULT_MAX_PER_TEAM

    # Read the defaults from the modules that own them (text, not import: a
    # section1 test must not import another section's tree — isolation law).
    per_team = re.search(
        r'os\.environ\.get\(\s*"HARNESS_RUN_CONCURRENCY"\s*,\s*"(\d+)"',
        SERVICES.read_text(encoding="utf-8"),
    )
    assert per_team, "HARNESS_RUN_CONCURRENCY lost its literal default in services.py"
    assert value("runConcurrencyPerTeam") == int(per_team.group(1))

    pool = re.search(
        r"_DEFAULT_ENGINE_MAX_CONCURRENCY\s*=\s*(\d+)",
        ENGINE_RUNTIME.read_text(encoding="utf-8"),
    )
    assert pool, "ENGINE_MAX_CONCURRENCY lost its literal default in engine_runtime.py"
    assert value("enginePoolMax") == int(pool.group(1))


def test_the_tier_knobs_an_operator_needs_are_in_the_env_catalogue():
    """"Operator-tunable" is only true if the operator can find the knob."""
    catalogue = (ROOT / ".env.example").read_text(encoding="utf-8")
    for knob in ("HARNESS_TIER_RUNS_PER_DAY", "HARNESS_TIER_CONTEXT_CHUNKS"):
        assert knob in catalogue, f"{knob} tunes a published cap but is undocumented"


def test_the_page_no_longer_advertises_what_no_door_enforces():
    text = DOC.read_text(encoding="utf-8")
    for stale in ("50 runs/day", "5k chunks", "4/8/16", "Agent seats"):
        assert stale not in text, (
            f"PRICING.md still advertises {stale!r}; no door enforces it"
        )


# --------------------------------------------------------------- the /pricing page
# The UI route reads section7/src/lib/pricing.json. It is the same claim in a
# second place, so it gets the same check — otherwise the page a customer
# actually looks at would drift first.


@pytest.fixture(scope="module")
def ui() -> dict:
    return json.loads(UI_PRICING.read_text(encoding="utf-8"))


@pytest.mark.parametrize("tier", PLAN_TIERS)
def test_the_pricing_route_shows_the_caps_the_code_enforces(ui, tier):
    effective = tier_caps(tier).to_wire()
    for field, cells in ui["caps"].items():
        assert cells[tier] == effective[field], (
            f"/pricing shows {tier}.{field} as {cells[tier]!r}; code enforces"
            f" {effective[field]!r}"
        )


def test_the_pricing_route_uses_the_same_platform_limits(ui):
    assert ui["platform"]["siemSinksPerTeam"] == MAX_SINKS_PER_TEAM
    assert ui["platform"]["scimTokensPerTeam"] == MAX_TOKENS_PER_TEAM
    assert ui["platform"]["workspacesPerTeam"] == DEFAULT_MAX_PER_TEAM

    source = SERVICES.read_text(encoding="utf-8")
    per_team = re.search(
        r'os\.environ\.get\(\s*"HARNESS_RUN_CONCURRENCY"\s*,\s*"(\d+)"', source
    )
    assert per_team and ui["platform"]["runConcurrencyPerTeam"] == int(per_team.group(1))
    pool = re.search(
        r"_DEFAULT_ENGINE_MAX_CONCURRENCY\s*=\s*(\d+)",
        ENGINE_RUNTIME.read_text(encoding="utf-8"),
    )
    assert pool and ui["platform"]["enginePoolMax"] == int(pool.group(1))


def test_the_pricing_route_separates_enforced_from_declared(ui):
    enforced = set(ui["enforced"])
    declared = set(ui["declared"])
    assert enforced == {"runsPerDay", "contextIndexMaxChunks"}
    assert declared == {"seatLimit", "auditRetentionDays"}
    assert enforced | declared == set(ui["caps"]), "every row must say whether it refuses"


def test_the_pricing_route_invents_no_amount(ui):
    """"Contact us" is the honest price until counsel signs off; a dollar figure
    on the route would be the same overclaim in a nicer font."""
    for tier in ui["tiers"]:
        assert tier["price"] in {"$0 forever", "Contact us"}, tier
