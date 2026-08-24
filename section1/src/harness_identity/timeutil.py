"""Shared time helpers — the single source of "now" for every DB timestamp.

THE LAW (Mission 13 H4b):

    ALL database timestamps are timezone-aware UTC datetimes.

    asyncpg (the PostgreSQL driver behind production sessions) receives
    timezone-aware ``datetime`` objects only:

    * it REJECTS ISO-8601 strings bound to TIMESTAMPTZ columns
      (``DataError``/``InterfaceError`` at execute time);
    * it silently accepts NAIVE datetimes but they are interpreted as UTC
      *without* shifting — any local-time arithmetic upstream then lands an
      hour (or a day) off once compared against server-side ``now()``.

    Therefore: never bind ``str`` timestamps to TIMESTAMPTZ columns, never
    call naive ``datetime.now()``/``datetime.utcnow()`` for persistence.
    Route every "now" through :func:`now_utc`, normalize anything foreign
    with :func:`as_utc`, or use :func:`db_now` directly as a bind value.

Helpers
-------
``now_utc()``
    Timezone-aware current UTC instant (replaces ``datetime.utcnow()`` /
    ``datetime.now()`` without tz).
``as_utc(dt)``
    Normalize any datetime to aware UTC: naive input is *interpreted as
    already-UTC* and stamped ``+00:00``; aware input is converted.
``utcnow_iso()``
    Aware-UTC instant rendered as ISO-8601 text — ONLY for columns that are
    deliberately TEXT-typed (e.g. frozen migration 0006's run_claims), never
    for TIMESTAMPTZ columns.
``db_now()``
    A bind-value expression helper: returns an aware UTC datetime suitable
    for binding to TIMESTAMPTZ parameters on both PostgreSQL/asyncpg and
    SQLite/aiosqlite (whose adapter accepts aware datetimes). Use it wherever
    a statement needs "the time of this transaction" without hand-rolling
    ``datetime.now(UTC)``.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["now_utc", "as_utc", "utcnow_iso", "db_now"]


def now_utc() -> datetime:
    """Current instant as a timezone-aware UTC datetime (never naive)."""
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    """Normalize *dt* to a timezone-aware UTC datetime.

    Naive input is treated as already-UTC (the monorepo convention) and gets
    ``tzinfo=UTC`` attached; aware input is converted to UTC preserving the
    instant. Returns the same aware object when it is already UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utcnow_iso() -> str:
    """Aware-UTC ISO-8601 string for TEXT-typed timestamp columns only.

    Never bind this to a TIMESTAMPTZ column — asyncpg rejects strings there;
    pass :func:`db_now` instead.
    """
    return now_utc().isoformat()


def db_now() -> datetime:
    """Bind-value helper: the current aware-UTC instant for DB timestamps.

    Identical value to :func:`now_utc`; kept as a named seam so call sites
    read as intent ("this is what goes into the DB") and so a future swap to
    a SQL-side ``now()`` expression has exactly one place to land.
    """
    return datetime.now(UTC)
