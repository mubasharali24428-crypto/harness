"""W15 gap: the confinement refusal must describe THIS host, not macOS.

`_run_isolated` used to hardcode "seatbelt breaks its PTY" for every mode it
refused, so a Linux host that resolved `auto -> none` was told about a macOS
sandbox it does not have. The refusal must name the resolved mode and the
alternatives an operator actually has.
"""

from harness_orchestrator import confine
from harness_orchestrator.engine import real_worker_confinement_error

REAL = "harness_orchestrator.engine_worker"


def test_other_workers_are_never_refused():
    assert real_worker_confinement_error("some.other.module", REAL) is None


def test_accepted_modes_are_not_refused(monkeypatch):
    monkeypatch.setenv(confine.CONFINEMENT_ENV, "off")
    assert real_worker_confinement_error(REAL, REAL) is None
    monkeypatch.setenv(confine.CONFINEMENT_ENV, "container")
    assert real_worker_confinement_error(REAL, REAL) is None


def test_linux_auto_names_the_missing_boundary_not_seatbelt(monkeypatch):
    monkeypatch.setenv(confine.CONFINEMENT_ENV, "auto")
    monkeypatch.setattr(confine, "seatbelt_available", lambda: False)
    message = real_worker_confinement_error(REAL, REAL)
    assert message is not None
    assert "'none'" in message
    assert "PTY" not in message
    assert "container" in message and "off" in message


def test_darwin_seatbelt_names_the_pty_limitation(monkeypatch):
    monkeypatch.setenv(confine.CONFINEMENT_ENV, "auto")
    monkeypatch.setattr(confine, "seatbelt_available", lambda: True)
    message = real_worker_confinement_error(REAL, REAL)
    assert message is not None
    assert "seatbelt" in message and "PTY" in message
