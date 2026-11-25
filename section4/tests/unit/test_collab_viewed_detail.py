"""Sprint 5 Tasks 31-32 (G-X07-3): the collab approve path carries the X-07 blind-approval signal.

Task 31: ``POST .../approvals/{id}/decision`` accepts an optional ``viewed_detail`` (same wire name
and meaning as section8's ``ApproveRequest``) and threads it to ``collab.decide_approval``. The
signal never changes the decision: omitted, ``true`` and ``false`` all approve the same way.
"""

import pytest

from harness_orchestrator import collab
from tests.unit.test_collab_backend import P, lead_run, new_approval
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
