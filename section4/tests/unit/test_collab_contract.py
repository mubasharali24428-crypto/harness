"""Part 14: docs/collaboration-contract.md is executable.

* every approval state x action pair behaves as the table says
* every error code collab.py can raise is in the doc's error index, and nothing indexed is fiction
* the redirect / handoff / proposal rules the doc states and no other test pinned
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.unit.test_collab_backend import PROPOSAL, P, lead_run, new_approval, q
from tests.unit.test_collab_backend import env as env  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]
DOC = (ROOT / "docs" / "collaboration-contract.md").read_text()
COLLAB = (ROOT / "section4" / "src" / "harness_orchestrator" / "collab.py").read_text()

STATES = ["pending", "approved", "rejected", "expired", "invalidated", "executing", "completed",
          "failed"]
ACTIONS = ["approve", "reject", "amend", "claim", "complete"]


def _code(r):
    return r.json()["detail"]["error"]["code"]


def reach(as_, f, state):
    """A fresh approval by eng2 in `state`; returns (id, digest)."""
    a = new_approval(as_)
    aid, digest = a["id"], a["digest"]
    decide = lambda ok: as_("lead").post(  # noqa: E731
        f"{P}/r1/approvals/{aid}/decision", json={"approve": ok, "digest": digest})
    claim = lambda: as_("eng2").post(f"{P}/r1/approvals/{aid}/claim", json={"digest": digest})  # noqa: E731
    done = lambda ok: as_("eng2").post(  # noqa: E731
        f"{P}/r1/approvals/{aid}/complete", json={"ok": ok, "outcome": "x"})
    if state == "approved":
        assert decide(True).status_code == 200
    elif state == "rejected":
        assert decide(False).status_code == 200
    elif state in ("executing", "completed", "failed"):
        assert decide(True).status_code == 200 and claim().status_code == 200
        if state != "executing":
            assert done(state == "completed").status_code == 200
    elif state == "invalidated":
        r = as_("lead").post(f"{P}/r1/redirect", json={"kind": "scope_change", "content": "c",
                                                        "reason": "r"})
        assert r.status_code == 200 and r.json()["invalidatedApprovals"] == 1
    elif state == "expired":
        async def age():
            async with f() as db:
                await db.execute(text("UPDATE high_impact_approvals SET expires_at='2000-01-01'"))
                await db.commit()
        import asyncio
        asyncio.run(age())
        as_("eng2").get(f"{P}/r1/approvals")  # the list sweeps expiry into the stored state
    assert q(f, "SELECT state FROM high_impact_approvals WHERE id=:i", i=aid)[0].state == state
    return aid, digest


def act(as_, aid, digest, action):
    base = f"{P}/r1/approvals/{aid}"
    if action in ("approve", "reject"):
        return as_("lead").post(f"{base}/decision", json={"approve": action == "approve",
                                                           "digest": digest})
    if action == "amend":
        return as_("eng2").put(base, json={**PROPOSAL, "diff": "-changed"})
    if action == "claim":
        return as_("eng2").post(f"{base}/claim", json={"digest": digest})
    return as_("eng2").post(f"{base}/complete", json={"ok": True, "outcome": "done"})


def expected(state, action):
    """The table in docs/collaboration-contract.md section 4: (status, new state | error code)."""
    if action in ("approve", "reject"):
        if state == "pending":
            return 200, "approved" if action == "approve" else "rejected"
        return 409, "NOT_PENDING"
    if action == "amend":
        return (200, "pending") if state in ("pending", "approved") else (409, "NOT_AMENDABLE")
    if action == "claim":
        return (200, "executing") if state == "approved" else (409, "NOT_APPROVED")
    return (200, "completed") if state == "executing" else (409, "NOT_EXECUTING")


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("state", STATES)
def test_every_state_and_action_pair_follows_the_table(env, state, action):
    as_, f = env
    lead_run(as_)
    aid, digest = reach(as_, f, state)
    status, outcome = expected(state, action)
    r = act(as_, aid, digest, action)
    assert r.status_code == status, (state, action, r.text)
    if status == 200:
        assert r.json()["state"] == outcome
    else:
        assert _code(r) == outcome
        # a refusal changes nothing
        assert q(f, "SELECT state FROM high_impact_approvals WHERE id=:i", i=aid)[0].state == state


def test_the_documented_states_are_the_states_the_code_writes():
    via_variable = {"approved", "rejected", "completed", "failed"}  # `SET state = :s` in collab.py
    written = set(re.findall(r"state = '([a-z_]+)'", COLLAB)) | via_variable
    section = DOC.split("## 4.")[1].split("## 5.")[0]
    listed = set(re.findall(r"`([a-z]+)`", section.split("States:")[1].split("Terminal:")[0]))
    assert listed == set(STATES)
    assert listed - via_variable <= written


def test_every_error_code_collab_can_raise_is_in_the_doc_index_and_vice_versa():
    raised = set(re.findall(r'CollabError\(\s*\d+,\s*"([A-Z_]+)"', COLLAB))
    raised |= set(re.findall(r'code, msg = "([A-Z_]+)"', COLLAB))
    raised |= {"NOT_PENDING", "NOT_APPROVED", "SELF_APPROVAL"}  # raised through variables
    index = DOC.split("## 9. Error index")[1]
    indexed = set(re.findall(r"^\| `([A-Z_]+)` \|", index, re.M))
    assert raised <= indexed, f"undocumented: {sorted(raised - indexed)}"
    assert indexed <= raised, f"documented but never raised: {sorted(indexed - raised)}"


# ----------------------------------------------------- rules the doc states, one test each


def test_redirect_rules_empty_content_viewers_and_writers_who_never_joined(env):
    as_, _f = env
    lead_run(as_)
    blank = as_("eng2").post(f"{P}/r1/redirect", json={"content": "   "})
    assert blank.status_code == 422 and _code(blank) == "BAD_CONTENT"
    viewer = as_("view").post(f"{P}/r1/redirect", json={"content": "x"})
    assert viewer.status_code == 403 and _code(viewer) == "ROLE_DENIED"
    unjoined = as_("eng2").post(f"{P}/r2/redirect", json={"content": "x"})
    assert unjoined.status_code == 403 and _code(unjoined) == "NOT_PARTICIPANT"
    scope = as_("eng2").post(f"{P}/r1/redirect", json={"kind": "scope_change", "content": "x",
                                                        "reason": "why"})
    assert scope.status_code == 403 and _code(scope) == "NOT_LEADER"


def test_proposal_rules_empty_summary_viewers_and_writers_who_never_joined(env):
    as_, _f = env
    lead_run(as_)
    empty = as_("eng2").post(f"{P}/r1/approvals", json={**PROPOSAL, "summary": "  "})
    assert empty.status_code == 422 and _code(empty) == "BAD_PROPOSAL"
    viewer = as_("view").post(f"{P}/r1/approvals", json=PROPOSAL)
    assert viewer.status_code == 403 and _code(viewer) == "ROLE_DENIED"
    unjoined = as_("eng2").post(f"{P}/r2/approvals", json=PROPOSAL)
    assert unjoined.status_code == 403 and _code(unjoined) == "NOT_PARTICIPANT"


def test_only_the_requester_may_amend_and_only_the_requester_or_leader_may_execute(env):
    as_, _f = env
    lead_run(as_)
    aid = new_approval(as_)["id"]
    other = as_("lead").put(f"{P}/r1/approvals/{aid}", json=PROPOSAL)  # leader, not requester
    assert other.status_code == 403 and _code(other) == "NOT_REQUESTER"
    as_("boss").post(f"{P}/r1/join")  # a joined admin who is neither requester nor leader
    bystander = as_("boss").post(f"{P}/r1/approvals/{aid}/claim", json={"digest": "0" * 64})
    assert bystander.status_code == 403 and _code(bystander) == "NOT_LEADER"


def test_handoff_states_pending_then_cancelled_by_release_or_rejected_by_takeover(env):
    as_, f = env
    lead_run(as_)
    h = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"}).json()
    assert h["state"] == "pending" and h["from"] == "lead" and h["to"] == "eng2"
    again = as_("lead").post(f"{P}/r1/handoff", json={"toUserId": "eng2"})
    assert again.status_code == 409 and _code(again) == "HANDOFF_PENDING"
    as_("boss").post(f"{P}/r1/join")
    stranger = as_("boss").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True})
    assert stranger.status_code == 403 and _code(stranger) == "NOT_LEADER"
    assert as_("boss").post(f"{P}/r1/leader").status_code == 200  # admin takeover
    state = q(f, "SELECT state FROM run_handoffs WHERE id=:i", i=h["handoffId"])[0].state
    assert state == "rejected"
    gone = as_("eng2").post(f"{P}/r1/handoff/{h['handoffId']}", json={"accept": True})
    assert gone.status_code == 404 and _code(gone) == "NOT_FOUND"
