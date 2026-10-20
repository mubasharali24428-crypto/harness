"""Standalone service entry: python -m harness_extensions.

Wires bearer auth against the frozen Section-1 schema (auth_sessions /
team_members) via harness_contracts.make_bearer_dep — identical semantics to
every deployed section. The sqlalchemy/asyncpg extras live in the optional
``server`` dependency group; without them this entry fails with a clear
message instead of a stack trace.
"""

from __future__ import annotations

import logging
import os


def _session_factory(database_url: str):
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    except ImportError as exc:  # pragma: no cover - install-time posture
        raise SystemExit(
            "harness-extensions standalone mode needs the 'server' extra: "
            "pip install 'harness-extensions[server]'"
        ) from exc

    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from harness_contracts.fastapi_auth import make_bearer_dep

    from .api import make_app
    from .config import Settings

    settings = Settings()
    database_url = os.environ.get("HARNESS_DATABASE_URL", settings.database_url)
    factory = _session_factory(database_url)
    app = make_app(settings, get_principal_dep=make_bearer_dep(factory))

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.http_port)


if __name__ == "__main__":
    main()
