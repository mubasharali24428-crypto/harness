import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.models import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(db):
    from httpx import ASGITransport, AsyncClient

    app = make_app(db, Settings(master_key=b"test-master-key-0123456789abcdef", cors_origins=[]))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


@pytest.fixture
async def client_ownership_off(db):
    """C-001 (M11-W1): require_ownership now defaults ON everywhere; the
    legacy always-200 passwordless login survives only under this explicit
    opt-out fixture (dev convenience parity with HARNESS_ENV=dev)."""
    from httpx import ASGITransport, AsyncClient

    from harness_identity.config import Settings as S

    app = make_app(
        db,
        S(master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


GOLDEN_DIR = Path(__file__).parent / "golden"


async def test_contracts_version_frozen():
    import harness_contracts

    assert harness_contracts.__version__ == "0.1.0"
