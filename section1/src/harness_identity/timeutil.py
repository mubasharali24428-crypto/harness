"""Shared UTC time helpers — the platform-wide single source of "now".

THE LAW (Mission 13 H4b): ALL database timestamps are timezone-aware UTC
datetimes. asyncpg REJECTS ISO-8601 strings on TIMESTAMPTZ and interprets
naive datetimes as UTC without shifting; never bind strings or naive
datetimes for persistence.

M17-B3: this module is now a thin re-export shim over the canonical
implementation in ``harness_contracts.timeutil`` (promoted so S4/S6 stop
reaching into section1's src tree via runtime sys.path injection). Kept for
Section 1's internal callers and test imports; new code should import from
harness_contracts.
"""

from __future__ import annotations

from harness_contracts.timeutil import as_utc, db_now, now_utc, utcnow_iso

__all__ = ["now_utc", "as_utc", "utcnow_iso", "db_now"]
