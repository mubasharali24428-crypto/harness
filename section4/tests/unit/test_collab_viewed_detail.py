"""Sprint 5 Tasks 31-32 (G-X07-3): the collab approve path carries the X-07 blind-approval signal.

Task 31: ``POST .../approvals/{id}/decision`` accepts an optional ``viewed_detail`` (same wire name
and meaning as section8's ``ApproveRequest``) and threads it to ``collab.decide_approval``. The
signal never changes the decision: omitted, ``true`` and ``false`` all approve the same way.

Task 32: the ``approval.approved`` audit row carries ``teamId``, ``rendering`` (the approver's
``team_members.approval_rendering``, fail-open ``unknown``) and ``viewedDetail`` — the fields
section8's blind-approval KPI reads. The collaborator event stays free of both.
"""

import asyncio
import json

import pytest
from sqlalchemy import text

from harness_orchestrator import collab
from harness_orchestrator.collab import member_rendering
from tests.unit.test_collab_backend import P, lead_run, new_approval, q
from tests.unit.test_collab_backend import env as env  # noqa: F401


@pytest.fixture(autouse=True)
def _no_pilot_overrides(monkeypatch):
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_SELF_APPROVAL", raising=False)


@pytest.fixture()
def seen(monkeypatch):
    """Record the viewed_detail value each decision reached collab.decide_approval with."""
    calls: list = []
    real = collab.decide_approval

    async def spy(*a, **k):
        calls.append(k.get("viewed_detail", "MISSING"))
        return await real(*a, **k)

    monkeypatch.setattr(collab, "decide_approval", spy)
    return calls


def decide(as_, a, **extra):
    return as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision",
        json={"approve": True, "digest": a["digest"], **extra},
    )


@pytest.mark.parametrize("body, expected", [({}, None), ({"viewed_detail": True}, True),
                                            ({"viewed_detail": False}, False),
                                            ({"viewed_detail": None}, None)])
def test_decision_accepts_optional_viewed_detail_and_threads_it(env, seen, body, expected):
    as_, _ = env
    lead_run(as_)
    a = new_approval(as_)
    r = decide(as_, a, **body)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "approved" and r.json()["decidedBy"] == "lead"
    assert seen == [expected]


def test_viewed_detail_must_be_a_boolean(env, seen):
    as_, _ = env
    lead_run(as_)
    a = new_approval(as_)
    r = decide(as_, a, viewed_detail={"no": "thanks"})
    assert r.status_code == 422
    assert seen == []  # rejected at the edge; nothing decided
    assert as_("view").get(f"{P}/r1/approvals").json()["approvals"][0]["state"] == "pending"


def test_reject_still_works_with_the_signal_present(env, seen):
    as_, _ = env
    lead_run(as_)
    a = new_approval(as_)
    r = as_("lead").post(
        f"{P}/r1/approvals/{a['id']}/decision",
        json={"approve": False, "digest": a["digest"], "viewed_detail": True},
    )
    assert r.status_code == 200 and r.json()["state"] == "rejected"
    assert seen == [True]


# ------------------------------------------------- Task 32: audit row for the X-07 KPI

def audit_detail(f, action):
    return [json.loads(r.detail) for r in
            q(f, "SELECT detail FROM audit_log WHERE action = :a ORDER BY seq", a=action)]


def committed(f, sql, **kw):
    async def go():
        async with f() as db:
            await db.execute(text(sql), kw)
            await db.commit()

    asyncio.run(go())


@pytest.mark.parametrize("body, expected", [({}, None), ({"viewed_detail": True}, True),
                                            ({"viewed_detail": False}, False)])
def test_approve_audit_row_carries_team_rendering_and_signal(env, body, expected):
    as_, f = env
    committed(f, "ALTER TABLE team_members ADD COLUMN approval_rendering VARCHAR(16)"
                 " NOT NULL DEFAULT 'technical'")
    committed(f, "UPDATE team_members SET approval_rendering = 'plain' WHERE user_id = 'lead'")
    lead_run(as_)
    a = new_approval(as_)
    assert decide(as_, a, **body).status_code == 200
    row = audit_detail(f, "approval.approved")[0]
    assert row["teamId"] == "team-a" and row["userId"] == "lead"
    assert row["rendering"] == "plain"
    assert "viewedDetail" in row and row["viewedDetail"] is expected


def test_signal_and_rendering_stay_off_the_collaborator_event(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    decide(as_, a, viewed_detail=False)
    events = as_("view").get(f"{P}/r1/collab-history").json()["events"]
    approved = [e for e in events if e["kind"] == "approval.approved"]
    assert len(approved) == 1
    assert "viewedDetail" not in approved[0]["payload"]
    assert "rendering" not in approved[0]["payload"]


def test_pre_0077_schema_is_unknown_and_the_approval_still_lands(env):
    """The shared DDL has no approval_rendering column: the lookup fails open in its own
    session, so the decision transaction is untouched."""
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    r = decide(as_, a, viewed_detail=True)
    assert r.status_code == 200 and r.json()["state"] == "approved"
    row = audit_detail(f, "approval.approved")[0]
    assert row["rendering"] == "unknown" and row["viewedDetail"] is True


def test_reject_records_rendering_but_no_viewed_signal(env):
    as_, f = env
    lead_run(as_)
    a = new_approval(as_)
    as_("lead").post(f"{P}/r1/approvals/{a['id']}/decision",
                     json={"approve": False, "digest": a["digest"], "viewed_detail": False})
    row = audit_detail(f, "approval.rejected")[0]
    assert row["rendering"] == "unknown" and "viewedDetail" not in row


def test_member_rendering_rejects_out_of_set_values_and_missing_ids(env):
    _, f = env
    committed(f, "ALTER TABLE team_members ADD COLUMN approval_rendering VARCHAR(16)")
    committed(f, "UPDATE team_members SET approval_rendering = 'shouty' WHERE user_id = 'lead'")
    committed(f, "UPDATE team_members SET approval_rendering = 'decision' WHERE user_id = 'eng2'")
    assert asyncio.run(member_rendering(f, "team-a", "lead")) == "unknown"
    assert asyncio.run(member_rendering(f, "team-a", "eng2")) == "decision"
    assert asyncio.run(member_rendering(f, "team-b", "eng2")) == "unknown"  # other team's row
    assert asyncio.run(member_rendering(f, None, "eng2")) == "unknown"
