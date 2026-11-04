"""The driver's code set is closed, and every code has exactly one status.

docs/hx-language-spec.md §13 says an added code must be a deliberate change with
"the table entry, a fixture, a golden, and a test that the closed-set assertion
was deliberately updated". No such assertion existed, so codes could have slipped
in through a refactor. This file is that assertion.

It reads the classes, not a copy of the table: a test that pastes the codes the
source already contains would pass even when the source contradicts the spec.
"""

from __future__ import annotations

import inspect

from harness_hx_host import errors

# The codes the spec's §9.5 table names. Adding a class here without a table row
# (or the other way round) must fail this test.
SPEC_CODES: dict[str, str] = {
    "HXH101": "refused",
    "HXH102": "refused",
    "HXH103": "refused",
    "HXH104": "refused",
    "HXH201": "error",
    "HXH301": "error",
    "HXH401": "denied",
    "HXH403": "error",
    "HXH404": "denied",
    "HXH501": "budget",
    "HXH502": "budget",
    "HXH503": "refused",
    "HXH601": "cancelled",
}


def _driver_classes() -> list[type[errors.HxHostError]]:
    found = []
    for _, obj in inspect.getmembers(errors, inspect.isclass):
        if (
            issubclass(obj, errors.HxHostError)
            and obj is not errors.HxHostError
            and obj.__module__ == errors.__name__
        ):
            found.append(obj)
    return found


def implemented() -> dict[str, str]:
    return {cls.code: cls.status for cls in _driver_classes()}


def test_every_driver_code_is_in_the_spec_table():
    extra = sorted(set(implemented()) - set(SPEC_CODES))
    assert not extra, f"codes emitted by the driver but not documented: {extra}"


def test_every_documented_code_is_still_emitted():
    missing = sorted(set(SPEC_CODES) - set(implemented()))
    assert not missing, f"the spec names codes no error class produces: {missing}"


def test_each_code_maps_to_exactly_one_status():
    """A code whose status moved would silently change what a caller does."""
    actual = implemented()
    moved = {
        code: (SPEC_CODES[code], actual[code])
        for code in SPEC_CODES
        if actual.get(code) != SPEC_CODES[code]
    }
    assert not moved, f"status changed for: {moved}"


def test_statuses_come_from_the_closed_vocabulary():
    allowed = set(errors.STATUSES)
    stray = {cls.code: cls.status for cls in _driver_classes() if cls.status not in allowed}
    assert not stray, f"statuses outside STATUSES: {stray}"


def test_the_base_placeholder_is_never_a_real_code():
    assert errors.HxHostError.code == "HXH000"
    assert "HXH000" not in SPEC_CODES
    assert all(cls.code != "HXH000" for cls in _driver_classes())


def test_cost_codes_belong_to_the_budget_family():
    """M6's two cost codes, named as the spec names them.

    An unpriceable crossing is a budget answer (the declared ceiling is what made
    it stop); a table the host could not build is a refusal (nothing should have
    run at all). Collapsing them into one status would lose that distinction.
    """
    assert errors.CostUnpriceable.status == "budget"
    assert errors.BadPriceTable.status == "refused"
    assert errors.StaleAuthority.status == "denied", (
        "authority that is not this program's is a policy answer, not a crash"
    )


def test_recording_mismatch_is_a_refusal_not_an_error():
    """M7: a replay that cannot honour its recording must not pretend to have run.

    `refused` means "nothing ran", which is exactly the claim being made: the
    recording is the wrong version, or a call in it never settled. An `error`
    status would read as "the program failed", and the whole value of a replay is
    that it reports the *original* run's status, never its own packaging.
    """
    assert errors.RecordingMismatch.status == "refused"
    assert "HXH104" in SPEC_CODES


def test_every_code_can_be_rebuilt_from_a_recording():
    """A recording names codes, so replay must be able to raise each one.

    If a failure class were missing from ``BY_CODE``, a recorded run of it would
    replay as `HXH104` — the replay inventing a failure the original never had.
    """
    assert set(errors.BY_CODE) == set(SPEC_CODES), (
        "the replay registry and the spec table must name the same codes"
    )
    for code, cls in errors.BY_CODE.items():
        rebuilt = errors.from_record({"code": code, "message": "m", "line": 3, "col": 1})
        assert isinstance(rebuilt, cls), code
        assert rebuilt.code == code and rebuilt.status == cls.status
        assert (rebuilt.line, rebuilt.col) == (3, 1)
