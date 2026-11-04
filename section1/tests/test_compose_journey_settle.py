"""W15 gap: stack-health must settle before it passes.

`docker compose ps -a` can lag a container's exit by a second or two. The
journey's first check read it once and passed a stack whose `sandbox-egress`
had already stopped, then failed moments later on the same stack. A clean read
therefore has to survive a short settle before the gate believes it.
"""

import importlib.util
import inspect
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _journey():
    spec = importlib.util.spec_from_file_location(
        "compose_journey", ROOT / "scripts" / "e2e" / "compose_journey.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _rows(state: str) -> list[dict]:
    return [{"Service": "identity", "State": state, "Health": "healthy", "Status": state}]


def test_a_clean_read_must_survive_the_settle():
    j = _journey()
    clock = Clock()
    reads = iter([_rows("running"), _rows("exited")])  # clean, then the truth

    def reader():
        try:
            return next(reads)
        except StopIteration:
            return _rows("running")

    rows = j.stable_health(
        reader,
        lambda: {"identity"},
        deadline=10.0,
        settle_seconds=1.5,
        clock=clock,
        sleep=clock.sleep,
    )
    assert rows == _rows("running")
    assert clock.now >= 1.5  # the settle actually elapsed before passing


def test_a_stopped_container_is_never_passed():
    j = _journey()
    clock = Clock()
    with pytest.raises(j.Failure) as excinfo:
        j.stable_health(
            lambda: _rows("exited"),
            lambda: {"identity"},
            deadline=5.0,
            settle_seconds=1.5,
            clock=clock,
            sleep=clock.sleep,
        )
    assert "identity: exited" in str(excinfo.value)


def test_default_clock_matches_the_deadline_the_caller_passes():
    """The deadline comes from ``time.time()``; a monotonic default would make
    the loop compare two different time bases and never finish. That hang was
    hit for real while the settle logic was written."""
    j = _journey()
    default_clock = inspect.signature(j.stable_health).parameters["clock"].default
    assert default_clock is time.time
