"""W16 slice 2 — group-mapped SSO, opt-in and never wider than the map.

SSO mapped email only: `role` came from the stored membership and an IdP group
change meant nothing in Harness. This slice adds a claim → role map, with the
rules that make it safe:

* OFF unless a team configures both a claim name and at least one mapping, so
  every existing team keeps today's behaviour bit for bit;
* login is mapping-only (W22 design), so a claim can never create a seat — it can
  only change the role of someone who is already a member;
* the map is admin-authored and validated (four team roles, bounded size), and an
  unmatched member keeps the stored role: a misconfigured claim must not lock a
  whole team out;
* the decision is recorded, so "why did this person have admin?" has an answer
  that is not "look at the IdP".

These tests pin the pure rule, then the mapping seam, then config validation.
"""

import pytest

from harness_identity.sso import (
    MAP_OK,
    normalize_group_roles,
    resolve_claim_role,
)

# --------------------------------------------------------------------- the rule


def test_no_configured_map_keeps_the_membership_role():
    """Default posture: every team that never configured this sees no change."""
    role, source, matched = resolve_claim_role(
        "viewer", groups_claim="", group_roles=[], claims={"groups": ["harness-admins"]}
    )
    assert (role, source, matched) == ("viewer", "membership", ())


def test_an_exact_group_match_applies_the_mapped_role():
    role, source, matched = resolve_claim_role(
        "viewer",
        groups_claim="groups",
        group_roles=[{"group": "harness-admins", "role": "admin"}],
        claims={"groups": ["harness-admins", "everyone"]},
    )
    assert (role, source, matched) == ("admin", "claim", ("harness-admins",))


def test_a_glob_pattern_matches_directory_naming_conventions():
    role, source, matched = resolve_claim_role(
        "guest",
        groups_claim="groups",
        group_roles=[{"group": "harness-eng-*", "role": "engineer"}],
        claims={"groups": ["harness-eng-platform"]},
    )
    assert (role, source, matched) == ("engineer", "claim", ("harness-eng-platform",))


def test_the_strongest_match_wins_regardless_of_list_order():
    group_roles = [
        {"group": "all-staff", "role": "guest"},
        {"group": "on-call", "role": "engineer"},
    ]
    role, source, _ = resolve_claim_role(
        "guest", "groups", group_roles, {"groups": ["on-call", "all-staff"]}
    )
    assert (role, source) == ("engineer", "claim")
    role, source, _ = resolve_claim_role(
        "guest", "groups", list(reversed(group_roles)), {"groups": ["all-staff", "on-call"]}
    )
    assert (role, source) == ("engineer", "claim")


def test_an_unmatched_member_keeps_the_stored_role():
    """Silence is not demotion: dropping a group in the IdP is not how a team
    revokes access (that is the membership API or SCIM), and a claim outage must
    not empty a team's permissions."""
    role, source, matched = resolve_claim_role(
        "admin", "groups", [{"group": "harness-admins", "role": "admin"}], {"groups": []}
    )
    assert (role, source, matched) == ("admin", "membership", ())


def test_claim_shapes_are_tolerated_not_crashed_on():
    """IdPs are inconsistent: a single string instead of a list, a non-string
    member, a missing claim entirely. None of these may raise on a login path."""
    for claims in ({"groups": "harness-admins"}, {}, {"groups": None}, {"groups": [1, None]}):
        role, source, matched = resolve_claim_role(
            "viewer", "groups", [{"group": "harness-admins", "role": "admin"}], claims
        )
        assert role in {"viewer", "admin"}
        assert source in {"membership", "claim"}
    role, source, _ = resolve_claim_role(
        "viewer", "groups", [{"group": "harness-admins", "role": "admin"}],
        {"groups": "harness-admins"},
    )
    assert (role, source) == ("admin", "claim")


def test_a_custom_claim_name_is_honoured():
    """Entra/Auth0 tenants often carry roles in `roles` or a namespaced URI."""
    role, source, _ = resolve_claim_role(
        "viewer",
        "https://acme.example/roles",
        [{"group": "Platform.Admin", "role": "admin"}],
        {"https://acme.example/roles": ["Platform.Admin"]},
    )
    assert (role, source) == ("admin", "claim")


# ------------------------------------------------------------------- validation


@pytest.mark.parametrize("role", ["git-owner", "superuser", "", "   "])
def test_unknown_roles_are_rejected_at_write_time(role):
    """A typo must fail loudly when the admin saves the map, not silently at the
    next login. Case and surrounding whitespace are normalised, not rejected."""
    with pytest.raises(ValueError):
        normalize_group_roles([{"group": "x", "role": role}])
    assert normalize_group_roles([{"group": "x", "role": "admin"}]) == [
        {"group": "x", "role": "admin"}
    ]
    assert normalize_group_roles([{"group": " x ", "role": " ADMIN "}]) == [
        {"group": "x", "role": "admin"}
    ]


def test_the_map_is_bounded_and_must_be_a_list_of_mappings():
    with pytest.raises(ValueError):
        normalize_group_roles([{"group": "", "role": "admin"}])
    with pytest.raises(ValueError):
        normalize_group_roles([{"group": "x"}])
    with pytest.raises(ValueError):
        normalize_group_roles([{"group": "x", "role": "admin", "extra": 1}])
    with pytest.raises(ValueError):
        normalize_group_roles([{"group": "g", "role": "admin"}] * 51)
    assert normalize_group_roles([]) == []


def test_the_ok_outcome_constant_is_exported_for_callers():
    """The mapping seam returns (outcome, principal); callers compare against
    these constants rather than magic strings."""
    assert MAP_OK == "ok"


# --------------------------------------------------------------- the mapping seam


@pytest.fixture
def sso_stack():
    """In-memory identity DB with a team, a member and an SSO config row.
    Mirrors `_map_email_to_member`'s real inputs, nothing else."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from harness_identity.models import Base

    engine = create_async_engine("sqlite+aiosqlite://")
    loop = asyncio.new_event_loop()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            await db.execute(text("INSERT INTO teams (id, name) VALUES ('t1', 'T')"))
            await db.execute(
                text("INSERT INTO users (id, email, name, is_active) "
                     "VALUES ('u1', 'dev@example.com', 'D', 1)")
            )
            await db.execute(
                text("INSERT INTO team_members (id, user_id, team_id, display_name, "
                     "member_type, role) VALUES ('m1', 'u1', 't1', 'D', 'HUMAN', 'VIEWER')")
            )
            await db.execute(
                text("INSERT INTO team_sso_configs (team_id, issuer_url, client_id, "
                     "client_secret_encrypted, enabled, idp_display_name, groups_claim, "
                     "group_roles) VALUES ('t1', 'https://idp.example', 'cid', 'ct', 1, '', "
                     ":claim, :roles)"),
                {
                    "claim": "groups",
                    "roles": '[{"group": "harness-admins", "role": "admin"}]',
                },
            )
            await db.commit()

    loop.run_until_complete(_seed())
    try:
        yield factory, loop
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()


def test_the_claim_changes_the_role_of_an_existing_member(sso_stack):
    """The end-to-end rule at the real seam: same email, same membership row —
    only the claims differ, and the principal arrives with the mapped role."""
    from harness_identity.sso import _map_email_to_member

    factory, loop = sso_stack
    mapping = loop.run_until_complete(
        _map_email_to_member(factory, "t1", "dev@example.com", {"groups": ["harness-admins"]})
    )
    assert mapping.outcome == MAP_OK
    assert mapping.principal.role.value == "admin"
    assert (mapping.role_source, mapping.matched) == ("claim", ("harness-admins",))


def test_without_claims_the_membership_role_is_used_and_says_so(sso_stack):
    from harness_identity.sso import _map_email_to_member

    factory, loop = sso_stack
    mapping = loop.run_until_complete(
        _map_email_to_member(factory, "t1", "dev@example.com", {"groups": ["other"]})
    )
    assert mapping.principal.role.value == "viewer"
    assert (mapping.role_source, mapping.matched) == ("membership", ())


def test_a_team_without_a_map_is_untouched(sso_stack):
    """Opt-in by construction: clear the map and the claim stops mattering."""
    from sqlalchemy import text

    from harness_identity.sso import _map_email_to_member

    factory, loop = sso_stack

    async def _clear():
        async with factory() as db:
            await db.execute(text("UPDATE team_sso_configs SET group_roles = '[]'"))
            await db.commit()

    loop.run_until_complete(_clear())
    mapping = loop.run_until_complete(
        _map_email_to_member(factory, "t1", "dev@example.com", {"groups": ["harness-admins"]})
    )
    assert mapping.principal.role.value == "viewer"
    assert mapping.role_source == "membership"
