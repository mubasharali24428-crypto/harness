"""Phase 7.7 (C-07) offline plan evaluator. Uses its own inline scenarios; the held-out file is only
checked for shape here, never used to assert scores (it must not tune the planner)."""

import copy
import json
from pathlib import Path

from harness_orchestrator import plan_eval
from harness_orchestrator.assignment_plan import propose

ROOT = Path(__file__).resolve().parents[3]
HELDOUT = ROOT / "docs/eval/plan-scenarios-heldout-v1.json"

TEAM = [
    {"userId": "lead", "role": "admin", "capabilities": []},
    {"userId": "sec", "role": "engineer", "capabilities": ["otp"]},
    {"userId": "obs", "role": "viewer", "capabilities": ["docs"]},
]


def doc(*scenarios):
    return {"schema": 1, "revision": "unit", "scenarios": list(scenarios)}


def scenario(tasks, expected, review=None, sid="s", team=TEAM):
    sc = {"id": sid, "team": team, "brief": {"goals": ["g"], "tasks": tasks},
          "expected": {"tasks": expected}}
    if review is not None:
        sc["review"] = {"approved": review}
    return sc


def only_task(report):
    (r,) = report["results"]
    (t,) = r["tasks"]
    return r, t


def test_good_plan_passes_with_no_failures():
    rep = plan_eval.evaluate(doc(scenario(
        [{"key": "a", "title": "A", "preferredAssignee": "sec"},
         {"key": "b", "title": "B", "requiredRole": "dba", "dependsOn": ["a"]}],
        {"a": {"acceptable": ["sec"]}, "b": {"acceptable": []}})))
    assert rep["summary"]["scenariosPassed"] == 1 and rep["summary"]["fitRate"] == 1.0
    assert rep["results"][0]["failures"] == []


def test_fit_verdicts():
    cases = [
        ("sec", ["sec"], "fit"),
        ("sec", ["lead"], "misfit"),
        (None, ["sec"], "unfilled"),
        ("sec", [], "overclaimed"),
        (None, [], "fit"),
    ]
    for named, acceptable, verdict in cases:
        task = {"key": "a", "title": "A", "requiredRole": "x"}
        if named:
            task["preferredAssignee"] = named
        rep = plan_eval.evaluate(doc(scenario([task], {"a": {"acceptable": acceptable}})))
        _, t = only_task(rep)
        assert t["fit"] == verdict, (named, acceptable)


def test_unsafe_assignments_are_flagged():
    def naive(brief, members):  # a proposer that ignores seat roles (the pre-fix planner)
        return propose(brief, members)

    _, t = only_task(plan_eval.evaluate(doc(scenario(
        [{"key": "a", "title": "A", "preferredAssignee": "obs"}],
        {"a": {"acceptable": ["sec"], "unsafe": ["obs"]}})), naive))
    assert t["unsafe"] and {"read_only_assignee", "listed_unsafe"} <= set(t["issues"])
    # The live planner (default proposer) refuses the viewer and names the gap instead.
    _, t = only_task(plan_eval.evaluate(doc(scenario(
        [{"key": "a", "title": "A", "preferredAssignee": "obs"}],
        {"a": {"acceptable": ["sec"], "unsafe": ["obs"]}}))))
    assert not t["unsafe"] and t["assignee"] is None and t["fit"] == "unfilled"

    def outsider(brief, members):  # a proposer that fabricates a non-member assignee
        out = propose(brief, members)
        out[0]["assignee"] = "stranger"
        return out

    _, t = only_task(plan_eval.evaluate(doc(scenario(
        [{"key": "a", "title": "A"}], {"a": {"acceptable": ["sec"]}})), outsider))
    assert t["unsafe"] and "non_member" in t["issues"]


def test_dishonest_proposer_is_caught():
    def liar(brief, members):
        out = propose(brief, members)
        out[0].update(capabilityFit="strong", availability="free Tuesday",
                      costEstimate="$40", missingExpertise=[], dependsOn=[])
        out.append({"task": "ghost", "assignee": None})  # a task the brief never had
        return out[:1] + out[2:]  # and drop task b entirely

    rep = plan_eval.evaluate(doc(scenario(
        [{"key": "a", "title": "A", "dependsOn": ["b"]}, {"key": "b", "title": "B"}],
        {"a": {"acceptable": []}, "b": {"acceptable": []}})), liar)
    r = rep["results"][0]
    by = {t["task"]: t for t in r["tasks"]}
    assert {"silent_gap", "asserted_fit", "asserted_availability", "invented_cost",
            "dependency_changed"} <= set(by["a"]["issues"])
    assert by["b"]["issues"] == ["omitted"]
    assert r["invented"] == ["ghost"] and not r["passed"]
    assert rep["summary"]["omissions"] == 1 and rep["summary"]["honestyIssues"] == 1


def test_rejections_scored_both_ways():
    cyclic = {"id": "c", "team": TEAM, "expected": {"rejected": True},
              "brief": {"goals": ["g"], "tasks": [{"key": "a", "title": "A", "dependsOn": ["a"]}]}}
    ok = plan_eval.evaluate(doc(cyclic))["results"][0]
    assert ok["rejected"] and ok["passed"] and "BAD_BRIEF" in ok["rejectionReason"]
    accepted = copy.deepcopy(cyclic)
    accepted["brief"]["tasks"][0]["dependsOn"] = []
    bad = plan_eval.evaluate(doc(accepted))["results"][0]
    assert not bad["rejected"] and not bad["passed"]
    surprise = {**cyclic, "expected": {"tasks": {}}}  # refused but should have been accepted
    r = plan_eval.evaluate(doc(surprise))["results"][0]
    assert not r["passed"] and "refused unexpectedly" in r["failures"][0]
    schema_bad = {**cyclic, "brief": {"goals": [], "tasks": []}}  # pydantic refusal
    assert plan_eval.evaluate(doc(schema_bad))["results"][0]["rejected"]


def test_human_corrections_are_listed_not_just_counted():
    rep = plan_eval.evaluate(doc(scenario(
        [{"key": "a", "title": "A", "preferredAssignee": "sec"}, {"key": "b", "title": "B"}],
        {"a": {"acceptable": ["sec"]}, "b": {"acceptable": ["lead"]}},
        review={"a": "sec", "b": "lead"})))
    assert rep["corrections"] == [{"task": "b", "proposed": None, "approved": "lead",
                                   "scenario": "s"}]
    assert rep["summary"]["humanCorrections"] == 1
    assert rep["summary"]["correctionRate"] == 0.5


def test_proposer_never_sees_held_out_labels():
    seen = {}

    def spy(brief, members):
        seen["members"], seen["brief"] = members, brief.model_dump()
        return propose(brief, members)

    plan_eval.evaluate(doc(scenario([{"key": "a", "title": "A"}], {"a": {"acceptable": ["sec"]}})),
                       spy)
    assert seen["members"] == {"lead", "sec", "obs"}  # ids only, as on the live route
    assert "otp" not in json.dumps(seen["brief"]) and "acceptable" not in json.dumps(seen["brief"])


def test_cli_exit_codes_and_outputs(tmp_path):
    safe = tmp_path / "safe.json"
    safe.write_text(json.dumps(doc(scenario(
        [{"key": "a", "title": "A", "preferredAssignee": "sec"}], {"a": {"acceptable": ["sec"]}}))))
    md, js = tmp_path / "r.md", tmp_path / "r.json"
    assert plan_eval.main([str(safe), "--md-out", str(md), "--json-out", str(js)]) == 0
    assert "PASS" in md.read_text()
    assert json.loads(js.read_text())["scenarioSha256"]
    omitted = tmp_path / "omitted.json"  # an expected task the brief never had -> omission
    sc = scenario([{"key": "a", "title": "A"}], {"a": {"acceptable": []}})
    sc["brief"]["tasks"].append({"key": "b", "title": "B"})
    omitted.write_text(json.dumps(doc(sc)))
    assert plan_eval.main([str(omitted)]) == 0  # b is scored (unexpected but present), safe
    outsider = tmp_path / "outsider.json"
    outsider.write_text(json.dumps(doc(scenario(
        [{"key": "a", "title": "A", "preferredAssignee": "obs"}], {"a": {"acceptable": ["sec"]}},
        team=[{"userId": "obs", "role": "engineer", "capabilities": []}]))))
    assert plan_eval.main([str(outsider)]) == 0  # an engineer seat is a safe (if misfit) pick


def test_unknown_schema_is_refused():
    try:
        plan_eval.evaluate({"schema": 2, "scenarios": []})
    except ValueError as e:
        assert "schema" in str(e)
    else:
        raise AssertionError("schema 2 accepted")


def test_heldout_file_shape():
    """Shape only: every scenario has a team, a brief and an expectation for each task."""
    d = json.loads(HELDOUT.read_text())
    assert d["schema"] == 1 and len(d["scenarios"]) >= 8
    ids = [s["id"] for s in d["scenarios"]]
    assert len(set(ids)) == len(ids)
    for s in d["scenarios"]:
        members = {m["userId"] for m in s["team"]}
        if s["expected"].get("rejected"):
            continue
        keys = {t["key"] for t in s["brief"]["tasks"]}
        assert set(s["expected"]["tasks"]) == keys, s["id"]
        for exp in s["expected"]["tasks"].values():
            assert set(exp.get("acceptable", [])) <= members, s["id"]
