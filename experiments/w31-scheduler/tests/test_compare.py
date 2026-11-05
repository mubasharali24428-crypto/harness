"""W31 — the comparison report is built from measured runs, not prose."""

from __future__ import annotations

from w31 import compare


def test_small_report_has_both_solvers_and_a_verdict_input():
    report = compare.build_report(sizes=(4,), repeat=1, time_limit_s=2.0)
    assert report["environment"]["python"]
    assert report["config"] == {"sizes": [4], "repeat": 1, "time_limit_s": 2.0}

    feasibility = report["allocation"]["feasibility"][0]
    assert feasibility["size"] == 4
    assert feasibility["fresh"]["status"] in {"optimal", "feasible"}
    assert feasibility["fresh"]["violations"] == []
    assert len(feasibility["fresh"]["latency"]["samples_ms"]) == 1
    assert feasibility["scheduler_allocation"]["status"] in {
        "optimal",
        "feasible",
        "unavailable",
    }

    timetable = report["timetable"][0]
    assert timetable["translation"]["ok"] is True
    assert timetable["timetable_bridge"]["violations"] == []
    assert timetable["timetable_bridge"]["assigned"] == 4
    assert len(report["constraint_coverage"]) >= 5


def test_repeat_must_be_positive():
    try:
        compare.build_report(sizes=(2,), repeat=0)
    except ValueError as error:
        assert "repeat" in str(error)
    else:  # pragma: no cover - the assertion above is the test
        raise AssertionError("repeat=0 must be rejected")


def test_mapped_back_violations_surface_in_the_report(monkeypatch):
    """A timetable result that fails the allocation validator must not read as clean."""
    def fake_run(passed_request, engine="heuristic"):
        return {
            "status": "invalid",
            "assignments": [{"task_id": "t000", "resource_id": "room-99", "start_slot": 0, "end_slot": 3}],
            "violations": [{"code": "unknown_resource", "detail": "room-99 is not a resource", "task_id": "t000"}],
            "unsupported": [],
            "elapsed_ms": 1,
            "engine": engine,
        }

    monkeypatch.setattr(compare.bridge, "run", fake_run)
    monkeypatch.setattr(
        compare.bridge,
        "translate",
        lambda passed_request: {
            "ok": True,
            "unsupported": [],
            "request": {"sessions": [], "rooms": []},
            "room_to_resource": {},
        },
    )
    entry = compare._compare_timetable(2, repeat=1, time_limit_s=2.0)
    assert entry["timetable_bridge"]["status"] == "invalid"
    assert any(v["code"] == "unknown_resource" for v in entry["timetable_bridge"]["violations"])
    assert entry["agreement"]["timetable_valid"] is False
