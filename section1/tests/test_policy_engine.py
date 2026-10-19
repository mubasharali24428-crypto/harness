"""Part 38 (Lane P): pure policy engine - precedence tables, determinism, anti-escalation."""

import itertools
import random
from datetime import UTC, datetime, timedelta

import pytest

from harness_identity.policy import (
    PERMISSIONS,
    Binding,
    EmergencyGrant,
    Node,
    PolicyDoc,
    PolicyError,
    Rule,
    Subject,
    evaluate,
    evaluate_delegated,
    missing_for_grant,
    stepup_fresh,
    stepup_required,
    validate_custom_role,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CHAIN = [
    Node("org", "o1"),
    Node("team", "t1"),
    Node("project", "p1"),
    Node("workspace", "w1"),
    Node("run", "r1"),
]


def sub(*binds, uid="u1", kind="human"):
    return Subject(uid, tuple(Binding(*b) for b in binds), kind)


def doc(*rules, lower=False):
    return PolicyDoc(rules=tuple(rules), allow_lower_grants=lower)


def R(id, effect, perms, subjects=("*",), overridable=False):
    return Rule(id=id, effect=effect, permissions=perms, subjects=subjects, overridable=overridable)


def ev(subject, perm, docs=None, chain=CHAIN, **kw):
    return evaluate(subject, perm, chain, docs, now=NOW, **kw)


# (name, subject bindings, docs, permission, expected_allowed)
CASES = [
    ("default deny without any binding", [], {}, "read", False),
    ("role default allows", [("org", "o1", "member")], {}, "steer", True),
    ("member lacks approve", [("org", "o1", "member")], {}, "approve", False),
    ("billing separate from admin", [("org", "o1", "admin")], {}, "billing", False),
    ("owner holds billing", [("org", "o1", "owner")], {}, "billing", True),
    ("billing_admin cannot administer", [("org", "o1", "billing_admin")], {}, "administer", False),
    (
        "org deny beats role allow",
        [("org", "o1", "admin")],
        {("org", "o1"): doc(R("d", "deny", ["export"]))},
        "export",
        False,
    ),
    (
        "deepest deny beats org allow",
        [("org", "o1", "admin")],
        {("run", "r1"): doc(R("d", "deny", ["export"]))},
        "export",
        False,
    ),
    (
        "deny wins over explicit allow same level",
        [("org", "o1", "guest")],
        {("org", "o1"): doc(R("a", "allow", ["export"]), R("d", "deny", ["export"]))},
        "export",
        False,
    ),
    (
        "overridable deny overridden by deeper role",
        [("project", "p1", "engineer")],
        {("org", "o1"): doc(R("d", "deny", ["steer"], overridable=True))},
        "steer",
        True,
    ),
    (
        "overridable deny stands vs shallower role",
        [("org", "o1", "admin")],
        {("project", "p1"): doc(R("d", "deny", ["steer"], overridable=True))},
        "steer",
        False,
    ),
    (
        "overridable deny stands vs same level role",
        [("org", "o1", "admin")],
        {("org", "o1"): doc(R("d", "deny", ["steer"], overridable=True))},
        "steer",
        False,
    ),
    (
        "lower policy allow ignored by default (narrow only)",
        [("org", "o1", "guest")],
        {("team", "t1"): doc(R("a", "allow", ["steer"]))},
        "steer",
        False,
    ),
    (
        "lower policy allow honoured when org allows it",
        [("org", "o1", "guest")],
        {("org", "o1"): doc(lower=True), ("team", "t1"): doc(R("a", "allow", ["steer"]))},
        "steer",
        True,
    ),
    (
        "org policy allow always eligible",
        [("org", "o1", "guest")],
        {("org", "o1"): doc(R("a", "allow", ["comment"]))},
        "comment",
        True,
    ),
    (
        "subject pattern role:",
        [("org", "o1", "guest")],
        {("org", "o1"): doc(R("a", "allow", ["comment"], ("role:guest",)))},
        "comment",
        True,
    ),
    (
        "subject pattern user: other user",
        [("org", "o1", "guest")],
        {("org", "o1"): doc(R("a", "allow", ["comment"], ("user:zzz",)))},
        "comment",
        False,
    ),
    (
        "binding on another project does not apply",
        [("project", "other", "owner")],
        {},
        "read",
        False,
    ),
    (
        "policy of another project does not apply",
        [("org", "o1", "admin")],
        {("project", "other"): doc(R("d", "deny", ["read"]))},
        "read",
        True,
    ),
]


@pytest.mark.parametrize("name,binds,docs,perm,expected", CASES, ids=[c[0] for c in CASES])
def test_precedence_table(name, binds, docs, perm, expected):
    d = ev(sub(*binds), perm, docs)
    assert d.allowed is expected, d.reason
    assert d.reason
    if expected:
        assert d.rule_ids, "an allow must cite the rule(s) that allowed it"


def test_explanation_chain_names_deciding_rules():
    docs = {("team", "t1"): doc(R("no-export", "deny", ["export"]))}
    d = ev(sub(("org", "o1", "admin")), "export", docs)
    assert not d.allowed and d.rule_ids == ["policy:team:no-export"]
    statuses = {r.rule_id: r.status for r in d.chain}
    assert statuses["role:admin@org:o1"] == "superseded"
    assert "no-export" in d.reason


def test_ignored_lower_allow_is_reported():
    docs = {("team", "t1"): doc(R("a", "allow", ["steer"]))}
    d = ev(sub(("org", "o1", "guest")), "steer", docs)
    assert [(r.rule_id, r.status) for r in d.chain] == [("policy:team:a", "ignored")]


def test_custom_role_permissions_and_validation():
    custom = {"auditor": frozenset({"read", "export"})}
    assert ev(sub(("org", "o1", "auditor")), "export", custom_roles=custom).allowed
    assert not ev(sub(("org", "o1", "auditor")), "steer", custom_roles=custom).allowed
    assert not ev(sub(("org", "o1", "auditor")), "export").allowed  # unknown role grants nothing
    for bad in ("admin", "x", "Bad Name"):
        with pytest.raises(PolicyError):
            validate_custom_role(bad, ["read"])
    with pytest.raises(PolicyError):
        validate_custom_role("okname", ["read", "teleport"])
    with pytest.raises(PolicyError):
        validate_custom_role("okname", [])


def test_bad_inputs():
    with pytest.raises(PolicyError):
        ev(sub(), "nope")
    with pytest.raises(PolicyError):
        evaluate(sub(), "read", [Node("team", "t")], now=NOW)
    with pytest.raises(PolicyError):
        evaluate(
            sub(), "read", [Node("org", "o"), Node("project", "p"), Node("team", "t")], now=NOW
        )
    with pytest.raises(ValueError):
        Rule(id="x", effect="allow", permissions=["fly"])


def test_deterministic_and_deny_wins_property():
    rng = random.Random(38)
    levels = ["org", "team", "project", "workspace", "run"]
    ids = {"org": "o1", "team": "t1", "project": "p1", "workspace": "w1", "run": "r1"}
    for _ in range(300):
        rules_by_level = {}
        for lv in levels:
            rs = [
                R(
                    f"r{i}",
                    rng.choice(["allow", "deny"]),
                    rng.sample(PERMISSIONS, rng.randint(1, 3)),
                    ("*",),
                    rng.random() < 0.3,
                )
                for i in range(rng.randint(0, 3))
            ]
            rules_by_level[(lv, ids[lv])] = PolicyDoc(
                rules=tuple(rs), allow_lower_grants=rng.random() < 0.5
            )
        binds = [
            (lv, ids[lv], rng.choice(["owner", "admin", "member", "guest", "viewer"]))
            for lv in levels
            if rng.random() < 0.5
        ]
        s, perm = sub(*binds), rng.choice(PERMISSIONS)
        d1 = ev(s, perm, rules_by_level)
        shuffled = dict(rng.sample(sorted(rules_by_level.items()), len(rules_by_level)))
        s2 = Subject(s.user_id, tuple(rng.sample(s.bindings, len(s.bindings))))
        assert ev(s2, perm, shuffled) == d1  # input order never matters
        assert ev(s, perm, rules_by_level) == d1
        finals = [
            r
            for lv in levels
            for r in rules_by_level[(lv, ids[lv])].rules
            if r.effect == "deny" and perm in r.permissions and not r.overridable
        ]
        if finals:
            assert not d1.allowed  # a non-overridable deny anywhere always wins


def test_evaluate_delegated_agent_never_exceeds_user():
    human = sub(("org", "o1", "member"), uid="h")
    agent = sub(("org", "o1", "admin"), uid="a", kind="agent")
    assert not evaluate_delegated(agent, human, "administer", CHAIN, now=NOW).allowed
    assert evaluate_delegated(agent, human, "steer", CHAIN, now=NOW).allowed
    docs = {("org", "o1"): doc(R("noagent", "deny", ["steer"], ("kind:agent",)))}
    assert not evaluate_delegated(agent, human, "steer", CHAIN, docs, now=NOW).allowed


@pytest.mark.parametrize(
    "role,wanted,missing",
    [
        ("admin", ["read", "approve"], []),
        ("admin", ["billing"], ["billing"]),
        ("member", ["steer", "approve", "export"], ["approve", "export"]),
        ("owner", list(PERMISSIONS), []),
    ],
)
def test_missing_for_grant(role, wanted, missing):
    assert missing_for_grant(sub(("org", "o1", role)), wanted, CHAIN, now=NOW) == missing


def test_escalation_respects_denies():
    docs = {("org", "o1"): doc(R("d", "deny", ["export"]))}
    assert missing_for_grant(sub(("org", "o1", "owner")), ["export"], CHAIN, docs, now=NOW) == [
        "export"
    ]


def test_emergency_window_with_fake_clock():
    g = EmergencyGrant(
        "g1", "u1", "org", "o1", frozenset({"administer"}), NOW, NOW + timedelta(minutes=30)
    )
    s = sub(("org", "o1", "guest"))

    def at(t):
        return evaluate(s, "administer", CHAIN, None, None, (g,), now=t)

    assert not at(NOW - timedelta(seconds=1)).allowed
    d = at(NOW)
    assert d.allowed and d.rule_ids == ["emergency:g1"]
    assert at(NOW + timedelta(minutes=29, seconds=59)).allowed
    assert not at(NOW + timedelta(minutes=30)).allowed  # auto-expiry, no sweeper needed
    other = EmergencyGrant(
        "g2", "someone-else", "org", "o1", frozenset({"administer"}), NOW, NOW + timedelta(hours=1)
    )
    assert not evaluate(s, "administer", CHAIN, None, None, (other,), now=NOW).allowed


def test_emergency_cannot_beat_final_deny():
    g = EmergencyGrant(
        "g1", "u1", "org", "o1", frozenset({"export"}), NOW, NOW + timedelta(hours=1)
    )
    docs = {("org", "o1"): doc(R("d", "deny", ["export"]))}
    assert not evaluate(sub(), "export", CHAIN, docs, None, (g,), now=NOW).allowed


def test_stepup_helpers():
    assert stepup_required("export", None)
    assert not stepup_required("export", PolicyDoc(stepup_actions=("policy.save",)))
    assert not stepup_required("unknown.action", None)
    assert not stepup_fresh(None, NOW, 300)
    assert stepup_fresh(NOW - timedelta(seconds=300), NOW, 300)
    assert not stepup_fresh(NOW - timedelta(seconds=301), NOW, 300)
    assert not stepup_fresh(NOW + timedelta(seconds=5), NOW, 300)  # future stamp is not trusted


def test_every_role_default_is_subset_of_permissions():
    from harness_identity.policy import ROLE_DEFAULTS

    for role, perms in ROLE_DEFAULTS.items():
        assert perms <= set(PERMISSIONS), role
    assert "billing" not in ROLE_DEFAULTS["admin"]
    assert {r for r, p in ROLE_DEFAULTS.items() if "billing" in p} == {"owner", "billing_admin"}
    assert list(itertools.islice(PERMISSIONS, 7)) == list(PERMISSIONS)
