"""Shared async engine/session factory — THE pool-math enforcement point.

Mission 13 Wave 3, lane H1a (finding H-017): every deployed service used to
build its engine with a bare ``create_async_engine(DATABASE_URL)``, silently
inheriting SQLAlchemy's default sizing (pool_size=5 + max_overflow=10 ⇒ up
to 15 server connections per process) with no documented ceiling and no
single place where the numbers lived.

The law now lives here:

    pool_size    = 8   (steady-state connections per service)
    max_overflow = 4   (burst headroom per service)

    (8 + 4) × 7 services = 84 ≤ Postgres max_connections = 120

leaving 36 slots of headroom for migrations, admin tooling and operator
psql sessions. docs/pool-math.md is the human-readable contract;
section1/tests/test_deploy_pool_math.py asserts BOTH ends of it (the
engines this module produces AND the compose max_connections cap), plus a
grep-equivalent proving no deploy-side code creates an ad-hoc engine that
bypasses this factory.

Every knob stays env-tunable for operators (HARNESS_DB_POOL_SIZE,
HARNESS_DB_MAX_OVERFLOW) — retuning is allowed, silent drifting is not:
the doc/compose/tests triangle fails CI if the published numbers move
without the whole chain moving together.
"""

from __future__ import annotations

import os

# Shipped pool math (see module docstring + docs/pool-math.md). Read once at
# import; tests reload this module to prove the env overrides work.
POOL_SIZE = int(os.environ.get("HARNESS_DB_POOL_SIZE", "8"))
MAX_OVERFLOW = int(os.environ.get("HARNESS_DB_MAX_OVERFLOW", "4"))

_DEFAULT_DSN = "postgresql+asyncpg://harness:harness@db:5432/harness"


def default_database_url() -> str:
    """Same DSN resolution services.py has always used (kept in lockstep)."""
    return os.environ.get("HARNESS_DATABASE_URL", _DEFAULT_DSN)


def build_engine(database_url: str | None = None):
    """Create THE async engine for a deployed process (pool_size=8, overflow=4).

    The ONLY sanctioned ``create_async_engine`` call site on the deploy side.
    SQLite URLs (aiosqlite — tests/local scripts) get NullPool instead: there
    is no server connection ceiling to honor and pool knobs would be noise
    masquerading as the documented math.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    url = default_database_url() if database_url is None else database_url
    if url.startswith("sqlite"):
        from sqlalchemy.pool import NullPool

        return create_async_engine(url, poolclass=NullPool)

    return create_async_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
    )


def make_session_factory(engine):
    """Bind an async_sessionmaker to ``engine`` — identical shape to the
    pre-H1a factories (expire_on_commit=False is a load-bearing contract for
    every make_app() consumer)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


def get_session_factory(database_url: str | None = None):
    """One-call convenience: engine + sessionmaker straight from the factory."""
    return make_session_factory(build_engine(database_url))
