"""HTTP denial audit coverage: every refused collab request leaves exactly one audit row.

Reuse the existing SQLite HTTP fixture; no authorization dependency is bypassed.
"""

import asyncio

import pytest
from sqlalchemy import text

from harness_orchestrator import api
from tests.unit.test_collab_backend import PROPOSAL, P, audit_actions, lead_run, new_approval
from tests.unit.test_collab_backend import env as env  # noqa: F401

ROUTES = [
    ("join", "POST", "/join", None, False),
    ("roster", "GET", "/participants", None, False),
    ("leader", "POST", "/leader", None, True),
    ("redirect", "POST", "/redirect", {"content": "x"}, True),
    ("history", "GET", "/collab-history", None, False),
    ("handoff", "POST", "/handoff", {"toUserId": "eng2"}, True),
    ("handoff_decide", "POST", "/handoff/h", {"accept": True}, True),
    ("request", "POST", "/approvals", PROPOSAL, True),
    ("list", "GET", "/approvals", None, False),
    ("amend", "PUT", "/approvals/a", PROPOSAL, True),
    ("decision", "POST", "/approvals/a/decision", {"approve": True, "digest": "0" * 64}, True),
    ("claim", "POST", "/approvals/a/claim", {"digest": "0" * 64}, True),
    ("complete", "POST", "/approvals/a/complete", {"ok": True, "outcome": "done"}, True),
    ("staged", "POST", "/staged/approvals", None, True),
    ("apply", "POST", "/approvals/a/apply", {"digest": "0" * 64}, True),
]


def check(response, status, code, factory, before, action="authz.denied"):
    # pytest.fail raises Failed, not AssertionError: broken authorization cannot xfail.
    if response.status_code != status:
        pytest.fail(f"Expected {status}, got {response.status_code}: {response.text}")
    if response.json().get("detail", {}).get("error", {}).get("code") != code:
        pytest.fail(f"Expected {code}: {response.text}")
    added = audit_actions(factory)[before:]
    assert added == [action], f"Expected one {action} audit row; got {added}"


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_SELF_APPROVAL", raising=False)


@pytest.mark.parametrize("route", ROUTES, ids=[r[0] for r in ROUTES])
def test_cross_team_audit(env, route):
    as_, factory = env
    _, method, path, body, _ = route
    before = len(audit_actions(factory))
    response = as_("evil").request(method, f"{P}/r1{path}", json=body)
    check(response, 404, "NOT_FOUND", factory, before)


@pytest.mark.parametrize("route", [r for r in ROUTES if r[4]], ids=[r[0] for r in ROUTES if r[4]])
def test_viewer_write_audit(env, route):
    as_, factory = env
    _, method, path, body, _ = route
    before = len(audit_actions(factory))
    response = as_("view").request(method, f"{P}/r1{path}", json=body)
    check(response, 403, "ROLE_DENIED", factory, before)


# name, method, path, actor, body, preparation, status, code, audit
CASES = [
    (
        "redirect_nonparticipant",
        "POST",
        "/redirect",
        "eng2",
        {"content": "x"},
        "unjoined",
        403,
        "NOT_PARTICIPANT",
        None,
    ),
    (
        "request_nonparticipant",
        "POST",
        "/approvals",
        "eng2",
        PROPOSAL,
        "unjoined",
        403,
        "NOT_PARTICIPANT",
        None,
    ),
    (
        "amend_nonrequester",
        "PUT",
        "/approvals/{a}",
        "lead",
        PROPOSAL,
        "",
        403,
        "NOT_REQUESTER",
        None,
    ),
    (
        "scope_nonleader",
        "POST",
        "/redirect",
        "eng2",
        {"kind": "scope_change", "content": "x", "reason": "x"},
        "",
        403,
        "NOT_LEADER",
        "authz.denied",
    ),
    (
        "permission_nonleader",
        "POST",
        "/redirect",
        "eng2",
        {"kind": "permission_change", "content": "x", "reason": "x"},
        "",
        403,
        "NOT_LEADER",
        "authz.denied",
    ),
    (
        "handoff_nonleader",
        "POST",
        "/handoff",
        "eng2",
        {"toUserId": "lead"},
        "",
        403,
        "NOT_LEADER",
        "authz.denied",
    ),
    (
        "decision_nonleader",
        "POST",
        "/approvals/{a}/decision",
        "eng2",
        {"approve": True},
        "",
        403,
        "NOT_LEADER",
        "authz.denied",
    ),
    (
        "self_approval",
        "POST",
        "/approvals/{a}/decision",
        "lead",
        {"approve": True},
        "self",
        403,
        "SELF_APPROVAL",
        "approval.self_denied",
    ),
    (
        "decision_expired",
        "POST",
        "/approvals/{a}/decision",
        "lead",
        {"approve": True},
        "expired",
        409,
        "EXPIRED",
        "approval.expired",
    ),
    (
        "decision_stale",
        "POST",
        "/approvals/{a}/decision",
        "lead",
        {"approve": True, "digest": "0" * 64},
        "",
        409,
        "DIGEST_MISMATCH",
        None,
    ),
    (
        "decision_replay",
        "POST",
        "/approvals/{a}/decision",
        "lead",
        {"approve": True},
        "approved",
        409,
        "NOT_PENDING",
        None,
    ),
    (
        "handoff_wrong_recipient",
        "POST",
        "/handoff/{h}",
        "lead",
        {"accept": True},
        "handoff",
        403,
        "NOT_LEADER",
        "authz.denied",
    ),
    ("leader_exists", "POST", "/leader", "eng2", None, "", 409, "LEADER_EXISTS", None),
    (
        "handoff_self",
        "POST",
        "/handoff",
        "lead",
        {"toUserId": "lead"},
        "",
        422,
        "SELF_HANDOFF",
        None,
    ),
    (
        "handoff_bad_recipient",
        "POST",
        "/handoff",
        "lead",
        {"toUserId": "view"},
        "",
        422,
        "BAD_RECIPIENT",
        None,
    ),
    (
        "handoff_pending",
        "POST",
        "/handoff",
        "lead",
        {"toUserId": "eng2"},
        "handoff",
        409,
        "HANDOFF_PENDING",
        None,
    ),
    (
        "handoff_missing",
        "POST",
        "/handoff/missing",
        "eng2",
        {"accept": True},
        "",
        404,
        "NOT_FOUND",
        None,
    ),
    (
        "amend_terminal",
        "PUT",
        "/approvals/{a}",
        "eng2",
        PROPOSAL,
        "completed",
        409,
        "NOT_AMENDABLE",
        None,
    ),
    (
        "complete_pending",
        "POST",
        "/approvals/{a}/complete",
        "eng2",
        {"ok": True},
        "",
        409,
        "NOT_EXECUTING",
        None,
    ),
]
for verb in ("claim", "apply"):
    for condition in ("expired", "stale", "pending"):
        CASES.append(
            (
                f"{verb}_{condition}",
                "POST",
                f"/approvals/{{a}}/{verb}",
                "eng2",
                {"digest": "0" * 64} if condition == "stale" else {},
                "approved_expired"
                if condition == "expired"
                else ("approved" if condition == "stale" else ""),
                409,
                "NOT_APPROVED",
                None,
            )
        )
for verb in ("claim", "complete", "apply"):
    CASES.append(
        (
            f"{verb}_nonexecutor",
            "POST",
            f"/approvals/{{a}}/{verb}",
            "eng2",
            {"ok": True} if verb == "complete" else {},
            "self",
            403,
            "NOT_LEADER",
            "authz.denied",
        )
    )


def update(factory, sql):
    async def go():
        async with factory() as db:
            await db.execute(text(sql))
            await db.commit()

    asyncio.run(go())


@pytest.mark.parametrize(
    "case", [pytest.param(c, id=c[0]) for c in CASES]
)
def test_collab_denial_audit(env, monkeypatch, tmp_path, case):
    as_, factory = env
    name, method, path, actor, body, prep, status, code, audit = case
    lead_run(as_)
    a = new_approval(as_, user="lead" if prep == "self" else "eng2")
    h = ""
    if prep == "handoff":
        h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()["handoffId"]
    if prep == "unjoined":
        update(factory, "DELETE FROM run_participants WHERE user_id = 'eng2'")
    if prep.startswith("approved") or prep == "completed":
        update(
            factory,
            "UPDATE high_impact_approvals SET state = '"
            + ("completed" if prep == "completed" else "approved")
            + "'",
        )
    if "expired" in prep:
        update(factory, "UPDATE high_impact_approvals SET expires_at = '2000-01-01 00:00:00'")
    # Real claim code runs for apply; filesystem application must never be reached.
    monkeypatch.setattr(api, "_staging_root", lambda: (str(tmp_path), lambda _: str(tmp_path)))
    payload = None if body is None else dict(body)
    if payload is not None and path.endswith(("/decision", "/claim", "/apply")):
        payload.setdefault("digest", a["digest"])
    before = len(audit_actions(factory))
    response = as_(actor).request(method, f"{P}/r1{path.format(a=a['id'], h=h)}", json=payload)
    check(response, status, code, factory, before, audit or "authz.denied")


# --- review probes: the audit path must never change or break the denial itself ---


def test_audit_store_failure_never_masks_the_denial(env, monkeypatch):
    from harness_orchestrator import collab

    async def boom(*a, **k):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(collab, "_audit", boom)
    as_, factory = env
    before = len(audit_actions(factory))
    for who, status, code in (("evil", 404, "NOT_FOUND"), ("view", 403, "ROLE_DENIED")):
        r = as_(who).post(f"{P}/r1/leader")
        assert (r.status_code, r.json()["detail"]["error"]["code"]) == (status, code)
    assert len(audit_actions(factory)) == before  # nothing written, nothing raised


def test_oversized_run_id_still_gets_the_plain_denial(env):
    as_, factory = env
    r = as_("evil").post(f"{P}/{'x' * 5000}/leader")
    assert r.status_code == 404 and r.json()["detail"]["error"]["code"] == "NOT_FOUND"


def test_pilot_gate_refusal_is_not_audited_today(env, monkeypatch):
    """Pins current behavior: a team outside HARNESS_PILOT_TEAMS gets 404 PILOT_NOT_ENABLED
    and no audit row (the supervisor sees the config, not the attempts)."""
    as_, factory = env
    lead_run(as_)
    before = len(audit_actions(factory))
    monkeypatch.setenv("HARNESS_PILOT_TEAMS", "some-other-team")
    r = as_("lead").get(f"{P}/r1/participants")
    assert r.json()["detail"]["error"]["code"] == "PILOT_NOT_ENABLED"
    assert len(audit_actions(factory)) == before
