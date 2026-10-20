"""Part 29 threat-model close-outs (docs/briefs/threat-model.md).

T11b: a redirect ``requestId`` dedupes per actor, so one participant's id cannot swallow another's.
"""

import asyncio
import json
from pathlib import Path

from harness_contracts.roles import Principal, Role

from harness_orchestrator import collab
from tests.unit.test_collab_backend import P, lead_run, q
from tests.unit.test_collab_backend import env as env  # noqa: F401


def _p(user):
    return Principal(userId=user, teamId="team-a", role=Role.ENGINEER)


def _say(as_, user, text, rid):
    r = as_(user).post(f"{P}/r1/redirect", json={"content": text, "requestId": rid})
    assert r.status_code == 200, r.text
    return r.json()


def test_t11b_the_same_request_id_from_two_actors_records_both(env):
    as_, f = env
    lead_run(as_)
    mine = _say(as_, "eng2", "use tabs", "same-id")
    theirs = _say(as_, "lead", "use spaces", "same-id")  # a guessed or colliding id
    assert not mine["duplicate"] and not theirs["duplicate"] and mine["seq"] != theirs["seq"]
    assert _say(as_, "eng2", "use tabs", "same-id") == {**mine, "duplicate": True}  # own retry
    who = [r.actor_user_id for r in q(f, "SELECT actor_user_id FROM run_collab_events"
                                         " WHERE kind = 'redirect.guidance' ORDER BY seq")]
    assert who == ["eng2", "lead"]


def test_t11b_an_empty_request_id_is_not_a_shared_key(env):
    as_, _f = env
    lead_run(as_)
    first, second = _say(as_, "eng2", "a", ""), _say(as_, "lead", "b", "")
    assert not first["duplicate"] and not second["duplicate"]


def test_t11b_server_built_keys_still_dedupe_across_actors(env):
    """discussion.promote passes ``promote:<commentId>`` straight to collab.redirect on purpose."""
    as_, f = env
    lead_run(as_)
    first = asyncio.run(collab.redirect(f, _p("eng2"), "r1", "guidance", "x", None, "promote:c1"))
    again = asyncio.run(collab.redirect(f, _p("lead"), "r1", "guidance", "x", None, "promote:c1"))
    assert again == {**first, "duplicate": True}


# ---------------------------------------------------------------------------- T18


def _denials(f):
    return [json.loads(r.detail) for r in q(
        f, "SELECT detail FROM audit_log WHERE action = 'authz.denied' ORDER BY seq")]


def test_t18_a_denial_flood_from_one_principal_is_capped_with_one_marker_row(env, monkeypatch):
    as_, f = env
    monkeypatch.setattr(collab, "DENIAL_AUDIT_CAP", 3)
    collab._denial_window.clear()
    for i in range(10):  # a client probing unknown run ids
        asyncio.run(collab.audit_denial(f, _p("eng2"), f"nope-{i}", "history", "NOT_FOUND", 404))
    rows = _denials(f)
    assert [r["reason"] for r in rows] == ["NOT_FOUND"] * 3 + ["DENIAL_AUDIT_CAP"]
    assert rows[-1]["userId"] == "eng2" and rows[-1]["capPerMinute"] == 3
    asyncio.run(collab.audit_denial(f, _p("lead"), "nope", "history", "NOT_FOUND", 404))
    assert len(_denials(f)) == 5  # the cap is per principal


def test_t18_the_budget_comes_back_in_the_next_window():
    collab._denial_window.clear()
    cap = collab.DENIAL_AUDIT_CAP
    verdicts = [collab._denial_verdict("u", now=1.0) for _ in range(cap + 2)]
    assert verdicts[:cap] == ["write"] * cap
    assert verdicts[-2:] == ["marker", "drop"]
    assert collab._denial_verdict("u", now=61.0) == "write"


# ------------------------------------------------------- 29.010 release rule

THREAT_MODEL = Path(__file__).resolve().parents[3] / "docs" / "briefs" / "threat-model.md"


def test_no_critical_or_high_finding_is_open_in_the_threat_model():
    """Part 29.010: an unresolved critical or high finding blocks release. The summary table of
    docs/briefs/threat-model.md is the register; a Critical/High row must say FIXED or MITIGATED."""
    rows = [ln for ln in THREAT_MODEL.read_text().splitlines() if ln.startswith("| ")]
    sev = {ln: ln.split("|")[1].strip() for ln in rows}
    graded = [ln for ln, v in sev.items() if v in ("Critical", "High", "Medium", "Low")]
    assert len(graded) >= 10, "the severity table moved; update this guard"
    open_ = [ln for ln in graded if sev[ln] in ("Critical", "High")
             and not ("FIXED" in ln or "MITIGATED" in ln)]
    assert not open_, f"release blocked by: {open_}"
