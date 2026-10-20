"""Value metrics against the known fixtures — exact denominators.

The fixtures carry failures (a2), rework (a2/a3/b2), abandonment (a4), a
blocked run (a5), in-flight (a6) and the distinguishing case a3: model claims
completion, independent review rejects, NO accepted change.
"""

from __future__ import annotations

from datetime import UTC, datetime

from harness_telemetry import build_records, cost_metrics, fixtures, parse_cost, parse_stream
from harness_telemetry.metrics import percentile, productivity_metrics, quality_metrics

AS_OF = fixtures.AS_OF


def _records():
    events = parse_stream(fixtures.events())
    costs = [parse_cost(c) for c in fixtures.costs()]
    return build_records(events, costs, fixtures.PROJECT_TEAM, as_of=AS_OF)


def test_outcomes_distinguish_claim_from_acceptance():
    by_id = {r.runId: r for r in _records()}
    assert by_id["run-a1"].outcome(AS_OF) == "accepted"
    assert by_id["run-a2"].outcome(AS_OF) == "accepted"  # accepted after rework
    assert by_id["run-a3"].outcome(AS_OF) == "rejected"  # claimed, review said no
    assert by_id["run-a3"].claimedCompletion is True
    assert by_id["run-a3"].accepted is False
    assert by_id["run-a4"].outcome(AS_OF) == "abandoned"
    assert by_id["run-a5"].outcome(AS_OF) == "blocked"
    assert by_id["run-a6"].outcome(AS_OF) == "active"
    assert by_id["run-b1"].outcome(AS_OF) == "accepted"
    assert by_id["run-b2"].outcome(AS_OF) == "rejected"


def test_quality_denominators_are_exact():
    q = quality_metrics(_records(), as_of=AS_OF)
    assert q["tasks"] == 8 and q["planned"] == 8
    assert q["started"] == 7
    assert q["accepted"] == 3 and q["rejected"] == 2 and q["blocked"] == 1
    assert q["abandoned"] == 1 and q["active"] == 1
    assert q["reworked"] == 3
    assert q["reworkRate"] == round(3 / 7, 4)
    assert q["acceptanceRateOfStarted"] == round(3 / 7, 4)
    assert q["acceptanceRateOfPlanned"] == round(3 / 8, 4)
    assert q["abandonmentRate"] == round(1 / 8, 4)
    assert q["claimedCompletions"] == 4
    assert q["claimedButNotAccepted"] == 1  # run-a3 only
    assert q["engineFailedRuns"] == 1
    assert "definitions" in q and q["definitions"]["accepted"].startswith("runs with a pr_opened")


def test_quality_team_and_window_filters():
    records = _records()
    team_b = quality_metrics(records, as_of=AS_OF, team="team-b")
    assert team_b["tasks"] == 2 and team_b["accepted"] == 1 and team_b["rejected"] == 1

    recent = quality_metrics(records, as_of=AS_OF, from_=datetime(2026, 10, 14, tzinfo=UTC))
    assert recent["tasks"] == 6 and recent["accepted"] == 1


def test_time_to_first_useful_change_exact():
    p = productivity_metrics(_records())
    assert p["samples"]["acceptedRuns"] == 3
    assert p["timeToFirstUsefulChangeMinutes"]["values"] == [35.0, 75.0, 85.0]
    assert p["timeToFirstUsefulChangeMinutes"]["p50"] == 75.0
    assert p["timeToFirstUsefulChangeMinutes"]["p90"] == 85.0
    assert p["approvalLatencyMinutes"]["p50"] == 2.0
    assert p["approvalLatencyMinutes"]["p90"] == 30.0


def test_cost_per_task_claimed_and_accepted_are_distinct_numbers():
    c = cost_metrics(_records())
    assert c["totalCost"] == "24.7000"
    assert c["costPerTask"] == "3.0875"
    assert c["costPerClaimedCompletion"] == "4.2750"
    assert c["costPerAcceptedChange"] == "4.3667"
    assert c["costPerAcceptedChange"] != c["costPerClaimedCompletion"]


def test_cost_team_filter():
    c = cost_metrics(_records(), team="team-b")
    assert c["plannedTasks"] == 2 and c["totalCost"] == "7.5000" and c["costPerTask"] == "3.7500"


def test_percentile_nearest_rank_documented_behavior():
    assert percentile([], 0.5) is None
    assert percentile([7], 0.5) == 7
    assert percentile([1, 2, 3, 4], 0.5) == 2  # rank ceil(2)=2
    assert percentile([1, 2, 3, 4], 0.9) == 4  # rank ceil(3.6)=4
