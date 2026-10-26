"""The compose journey's container-health check, tested without docker.

The check exists because 708 consecutive failed healthchecks on a sidecar went
unnoticed: every journey step passed, since the demo replay path never calls
the egress proxy. The first version of the check still passed when that
container was stopped — `docker compose ps` without `-a` does not list stopped
containers — which is what these tests pin: a dead container must be visible,
and a service that never came up at all must be visible too.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "e2e" / "compose_journey.py"


@pytest.fixture(scope="module")
def journey():
    spec = importlib.util.spec_from_file_location("compose_journey", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HEALTHY_STACK = [
    {"Service": "identity", "State": "running", "Health": "healthy"},
    {"Service": "sandbox-egress", "State": "running", "Health": "healthy"},
    {"Service": "frontend", "State": "running", "Health": ""},
    {"Service": "migrate", "State": "exited", "ExitCode": 0},
]
ENABLED = {"identity", "sandbox-egress", "frontend", "migrate"}


def test_a_healthy_stack_has_no_problems(journey):
    assert journey._problems_from_rows(HEALTHY_STACK, ENABLED) == []


def test_starting_is_not_a_failure(journey):
    """Inside start_period a healthy container reads `starting`."""
    rows = [{"Service": "db", "State": "running", "Health": "starting"}]
    assert journey._problems_from_rows(rows, {"db"}) == []


def test_unhealthy_sidecar_is_a_failure(journey):
    rows = [
        {"Service": "identity", "State": "running", "Health": "healthy"},
        {"Service": "sandbox-egress", "State": "running", "Health": "unhealthy"},
    ]
    problems = journey._problems_from_rows(rows, {"identity", "sandbox-egress"})
    assert len(problems) == 1
    assert "sandbox-egress" in problems[0]
    assert "unhealthy" in problems[0]


def test_a_stopped_sidecar_is_a_failure(journey):
    """The regression the first version of this check had: a stopped container
    is absent from `ps` (no -a), which made the gate pass the exact case it
    was written for."""
    rows = [
        {"Service": "identity", "State": "running", "Health": "healthy"},
        {"Service": "sandbox-egress", "State": "exited", "ExitCode": 0, "Status": "Exited (0)"},
    ]
    problems = journey._problems_from_rows(rows, {"identity", "sandbox-egress"})
    assert len(problems) == 1
    assert "sandbox-egress" in problems[0] and "exited" in problems[0]


@pytest.mark.parametrize("state", ["exited", "restarting", "dead", "created"])
def test_any_non_running_state_is_a_failure(journey, state):
    rows = [{"Service": "sandbox", "State": state, "Status": f"Up (bad) ({state})"}]
    problems = journey._problems_from_rows(rows, {"sandbox"})
    assert len(problems) == 1
    assert "sandbox" in problems[0] and state in problems[0]


def test_a_service_that_never_came_up_is_a_failure(journey):
    """No container row at all is worse than an unhealthy one, not better."""
    problems = journey._problems_from_rows([], {"identity"})
    assert problems == ["identity: no container"]


def test_containers_outside_the_enabled_service_set_are_ignored(journey):
    """A drill profile exercised last month leaves exited containers behind;
    they are not part of this boot and must not fail it."""
    rows = [
        {"Service": "identity", "State": "running", "Health": "healthy"},
        {"Service": "minio", "State": "exited", "ExitCode": 0},
        {"Service": "walg-db", "State": "exited", "ExitCode": 137},
    ]
    assert journey._problems_from_rows(rows, {"identity"}) == []


def test_migrate_must_exit_cleanly(journey):
    """The one-shot is expected to be `exited`; only a zero exit is success."""
    assert journey._problems_from_rows(
        [{"Service": "migrate", "State": "exited", "ExitCode": 0}], {"migrate"}
    ) == []

    failed = journey._problems_from_rows(
        [{"Service": "migrate", "State": "exited", "ExitCode": 2}], {"migrate"}
    )
    assert len(failed) == 1 and "exit 2" in failed[0]

    # A "running" migrate means the migration job hung: that is a failure too.
    hung = journey._problems_from_rows(
        [{"Service": "migrate", "State": "running", "ExitCode": 0}], {"migrate"}
    )
    assert len(hung) == 1 and "one-shot" in hung[0]
