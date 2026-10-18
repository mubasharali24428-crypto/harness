"""Rollback of the collaboration flag HARNESS_PILOT_TEAMS (part 32, directive 006).

Documented rule (collab.pilot_allowed, api._pilot_gate, release plan, rollback in
docs/runbooks/pilot.md): the flag is an ALLOWLIST. Rolling a team back means
leaving a non-empty list that does not name it. Unset or empty = open to every
team, i.e. it is NOT a rollback. A team outside the list gets 404
PILOT_NOT_ENABLED on all 15 collab routes (reads included), checked after the
run-ownership guard. Rollback deletes nothing: shared-run rows survive and the
run itself stays readable through the non-collab routes.

Reuses the real-route fixtures of test_collab_backend.py.
"""

import pytest

from harness_orchestrator import api as orch_api
from tests.unit.test_collab_backend import (  # noqa: F401  (env is a pytest fixture)
    PROPOSAL,
    P,
    audit_actions,
    env,
    lead_run,
    q,
)

FLAG = "HARNESS_PILOT_TEAMS"
ROLLED_BACK = ["__rolled_back__", "team-b"]  # nothing listed / only another team listed
TABLES = ("run_participants", "run_collab_events", "run_handoffs", "high_impact_approvals")


def snapshot(factory):
    return {t: [tuple(r) for r in q(factory, f"SELECT * FROM {t} ORDER BY 1, 2")] for t in TABLES}


def error_code(resp):
    return resp.json()["detail"]["error"]["code"]


@pytest.fixture()
def shared_run(env, monkeypatch):  # noqa: F811
    """A shared run created while collaboration is open: leader, redirect, pending approval."""
    as_, factory = env
    monkeypatch.delenv(FLAG, raising=False)
    lead_run(as_)
    guide = as_("eng2").post(f"{P}/r1/redirect", json={"content": "keep it small"})
    assert guide.status_code == 200
    approval = as_("eng2").post(f"{P}/r1/approvals", json=PROPOSAL).json()
    assert approval["state"] == "pending"

    async def fake_status(run_id):  # the real aggregate needs agent_events tables; auth is real
        return {"runId": run_id, "state": "running", "steps": []}

    monkeypatch.setattr(orch_api._service, "get_status", fake_status)
    return as_, factory, approval


@pytest.mark.parametrize("flag_value", ROLLED_BACK)
def test_rollback_keeps_run_readable_and_data_intact(shared_run, monkeypatch, flag_value):
    as_, factory, _ = shared_run
    before = snapshot(factory)
    assert before["run_collab_events"] and before["high_impact_approvals"]

    monkeypatch.setenv(FLAG, flag_value)  # the rollback

    # Run stays readable for its owner team on the non-collab routes (status + aggregate).
    for user in ("lead", "eng2", "view"):
        r = as_(user).get(f"{P}/r1")
        assert r.status_code == 200 and r.json()["runId"] == "r1", (user, r.text)
    # Stored shared-run state is untouched by the flag (rollback deletes nothing).
    assert snapshot(factory) == before


@pytest.mark.parametrize("flag_value", ROLLED_BACK)
def test_rollback_refuses_collab_mutations_by_documented_rule(shared_run, monkeypatch, flag_value):
    as_, factory, approval = shared_run
    before = snapshot(factory)
    audit_before = audit_actions(factory)
    aid, digest = approval["id"], approval["digest"]

    monkeypatch.setenv(FLAG, flag_value)

    # eng2 is a joined non-leader participant (the "non-owner" of the run's control).
    c = as_("eng2")
    attempts = [
        c.post(f"{P}/r1/redirect", json={"content": "sneak"}),
        c.post(f"{P}/r1/handoff", json={"toUserId": "lead"}),
        c.post(f"{P}/r1/approvals/{aid}/decision", json={"approve": True, "digest": digest}),
        c.post(f"{P}/r1/approvals", json=PROPOSAL),
        c.post(f"{P}/r1/leader"),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    assert {error_code(r) for r in attempts} == {"PILOT_NOT_ENABLED"}

    # The leader is refused too: the gate is per team, not per role. The approval stays pending.
    lead = as_("lead").post(
        f"{P}/r1/approvals/{aid}/decision", json={"approve": True, "digest": digest}
    )
    assert lead.status_code == 404 and error_code(lead) == "PILOT_NOT_ENABLED"

    # Reads are gated as well (UI shows the plain notice); nothing was written anywhere.
    assert as_("lead").get(f"{P}/r1/collab-history").status_code == 404
    assert snapshot(factory) == before
    assert audit_actions(factory) == audit_before  # refusal is not audited today (pinned)


def test_rollback_does_not_open_runs_to_other_teams(shared_run, monkeypatch):
    """Ownership guard runs first: a foreign team gets the same 404 NOT_FOUND as an unknown
    run, whatever the flag says, so a listed team-b still cannot touch team-a's run."""
    as_, factory, approval = shared_run
    before = snapshot(factory)
    for flag_value in ("team-b", "__rolled_back__", "team-a,team-b", ""):
        monkeypatch.setenv(FLAG, flag_value)
        c = as_("evil")
        for r in (
            c.get(f"{P}/r1"),
            c.get(f"{P}/r1/participants"),
            c.post(f"{P}/r1/redirect", json={"content": "x"}),
            c.post(
                f"{P}/r1/approvals/{approval['id']}/decision",
                json={"approve": True, "digest": approval["digest"]},
            ),
        ):
            assert r.status_code == 404 and error_code(r) == "NOT_FOUND", (flag_value, r.text)
    assert snapshot(factory) == before


def test_removing_or_emptying_the_flag_is_not_a_rollback(shared_run, monkeypatch):
    """Pins the trap documented in the runbook: unset/empty opens collaboration to all teams."""
    as_, _, _ = shared_run
    monkeypatch.setenv(FLAG, "__rolled_back__")
    assert as_("lead").get(f"{P}/r1/participants").status_code == 404
    for open_value in ("", "  ", ","):
        monkeypatch.setenv(FLAG, open_value)
        assert as_("lead").get(f"{P}/r1/participants").status_code == 200, repr(open_value)
    monkeypatch.delenv(FLAG)
    assert as_("lead").get(f"{P}/r1/participants").status_code == 200


def test_restoring_the_team_makes_history_and_pending_approval_usable_again(
    shared_run, monkeypatch
):
    as_, factory, approval = shared_run
    open_roster = as_("lead").get(f"{P}/r1/participants").json()
    open_history = as_("lead").get(f"{P}/r1/collab-history").json()["events"]

    monkeypatch.setenv(FLAG, "__rolled_back__")
    assert as_("lead").get(f"{P}/r1/participants").status_code == 404
    monkeypatch.setenv(FLAG, "team-a")  # team re-enabled, not by emptying the list

    assert as_("lead").get(f"{P}/r1/participants").json() == open_roster
    assert as_("lead").get(f"{P}/r1/collab-history").json()["events"] == open_history
    decided = as_("lead").post(
        f"{P}/r1/approvals/{approval['id']}/decision",
        json={"approve": True, "digest": approval["digest"]},
    )
    assert decided.status_code == 200 and decided.json()["state"] == "approved"


def test_gate_is_applied_only_to_collab_handlers():
    """Why the run stays readable: _pilot_gate is wired into collab_* handlers and nothing else."""
    import inspect

    gated = {
        r.endpoint.__name__
        for r in orch_api.router.routes
        if "_pilot_gate(" in inspect.getsource(r.endpoint)
    }
    assert gated and all(name.startswith("collab_") for name in gated), sorted(gated)
    assert len(gated) == 15  # same inventory the allowlist test pins
