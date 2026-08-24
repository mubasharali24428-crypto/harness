"""M13-H4b unit tests — harness_identity.timeutil (the aware-UTC law).

Covers the three helpers against the failure modes the law exists to kill:

* ``now_utc``/``db_now`` never return naive datetimes and sit on UTC;
* ``as_utc`` interprets naive input as already-UTC (monorepo convention)
  and converts aware input from any offset without shifting the instant;
* ``utcnow_iso`` renders an aware-UTC ISO string (TEXT-column discipline
  only) that round-trips through ``datetime.fromisoformat`` as UTC.
"""

import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

# Same seam every section uses: this venv may not carry harness_identity as
# an installed distribution, so append the S1 src tree explicitly.
_ROOT = Path(__file__).resolve().parents[2]
_S1_SRC = _ROOT / "section1" / "src"
if _S1_SRC.exists() and str(_S1_SRC) not in sys.path:
    sys.path.append(str(_S1_SRC))

from harness_identity.timeutil import as_utc, db_now, now_utc, utcnow_iso  # noqa: E402


def test_now_utc_is_aware_and_utc():
    value = now_utc()
    assert value.tzinfo is not None, "law: never naive"
    assert value.utcoffset() == timedelta(0)


def test_now_utc_is_monotonic_pair_with_db_now():
    before = now_utc()
    after = db_now()
    assert before <= after, "db_now is the same clock, read later"
    assert db_now().tzinfo is not None


def test_db_now_matches_now_utc_semantics():
    # Named seam for bind values; must be interchangeable with now_utc().
    assert db_now().utcoffset() == timedelta(0)


def test_as_utc_stamps_naive_input_as_utc():
    naive = datetime(2026, 8, 23, 12, 0, 0)
    stamped = as_utc(naive)
    assert stamped.tzinfo is not None
    assert stamped.utcoffset() == timedelta(0)
    # Naive means "already UTC by convention": wall time must not move.
    assert (stamped.year, stamped.month, stamped.day, stamped.hour) == (
        2026,
        8,
        23,
        12,
    )


def test_as_utc_converts_aware_non_utc_instant():
    tokyo = timezone(timedelta(hours=9))
    aware = datetime(2026, 8, 23, 21, 30, 0, tzinfo=tokyo)
    converted = as_utc(aware)
    assert converted.utcoffset() == timedelta(0)
    assert converted.timestamp() == aware.timestamp(), "instant preserved"
    assert (converted.hour, converted.minute) == (12, 30)


def test_as_utc_already_utc_is_same_instant():
    aware = datetime(2026, 8, 23, 0, 0, tzinfo=UTC)
    out = as_utc(aware)
    assert out == aware
    assert out.tzinfo is UTC or out.utcoffset() == timedelta(0)


def test_utcnow_iso_round_trips_as_utc():
    text = utcnow_iso()
    parsed = datetime.fromisoformat(text)
    assert parsed.tzinfo is not None, "ISO output carries the offset"
    assert abs(parsed.timestamp() - now_utc().timestamp()) < 5


def test_law_docstring_states_the_timestamptz_contract():
    import harness_identity.timeutil as mod

    doc = mod.__doc__ or ""
    assert "timezone-aware UTC" in doc
    assert "TIMESTAMPTZ" in doc
