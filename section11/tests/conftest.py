"""Section 11 test fixtures.

DB: temporary file-backed SQLite (aiosqlite) holding ONLY (a) section11's own
context_chunks mirror DDL and (b) minimal auth_sessions/team_members rows so
contracts.make_bearer_dep resolves a REAL principal through the frozen shared
code — gated access is proven end-to-end, not stubbed.

Embeddings: HashingEmbedding(256) — deterministic, zero network. The Ollama
backend is exercised separately by its constructor/config tests; live-model
calls stay out of CI by design.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_context.config import Settings
from harness_context.embeddings import HashingEmbedding

# Bearer token whose sha256 sits in auth_sessions; tests send it verbatim.
TEST_TOKEN = "m21x1-bearer-token"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}

TEST_PRINCIPAL = {"userId": "u-x1", "teamId": "team-x1", "role": "ADMIN"}

_AUTH_DDL_SESSIONS = """
CREATE TABLE auth_sessions (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    expires_at TIMESTAMP NOT NULL
);
"""

_AUTH_DDL_MEMBERS = """
CREATE TABLE team_members (
    user_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    role VARCHAR(20) NOT NULL
);
"""

# W16: `make_bearer_dep` also joins `users` on is_active, so the fixture has to
# carry the third table the frozen schema says a principal depends on. A fixture
# that omits it is not "simpler" — it proves a token resolves when the real
# schema would have refused it.
_AUTH_DDL_USERS = """
CREATE TABLE users (
    id VARCHAR(36) PRIMARY KEY,
    email VARCHAR(200) NOT NULL,
    name VARCHAR(200) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1
);
"""


@pytest.fixture
async def db(tmp_path):
    from harness_context.sql import DDL_SQLITE

    # In-memory SQLite uses a single shared connection. Concurrent auth reads
    # can roll back an index transaction when their session closes, producing
    # a completed job whose reported chunks were never committed. File-backed
    # SQLite gives sessions independent connections, as the deployed DB does.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'context.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(DDL_SQLITE))
        await conn.execute(text(_AUTH_DDL_SESSIONS))
        await conn.execute(text(_AUTH_DDL_MEMBERS))
        await conn.execute(text(_AUTH_DDL_USERS))
        await conn.execute(
            text(
                "INSERT INTO users (id, email, name, is_active) VALUES (:u, :e, :n, 1)"
            ),
            {
                "u": TEST_PRINCIPAL["userId"],
                "e": "context@example.com",
                "n": "Context Tester",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at) "
                "VALUES (:h, :u, :t, :e)"
            ),
            {
                "h": hashlib.sha256(TEST_TOKEN.encode()).hexdigest(),
                "u": TEST_PRINCIPAL["userId"],
                "t": TEST_PRINCIPAL["teamId"],
                "e": datetime.now(UTC) + timedelta(hours=1),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO team_members (user_id, team_id, role) VALUES (:u, :t, :r)"
            ),
            {
                "u": TEST_PRINCIPAL["userId"],
                "t": TEST_PRINCIPAL["teamId"],
                "r": TEST_PRINCIPAL["role"],
            },
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def settings():
    return Settings(embedding_backend="hashing", cors_origins=[])


@pytest.fixture
async def client(db, settings):
    """App client with the REAL contracts bearer dep wired (fail-closed)."""
    from httpx import ASGITransport, AsyncClient

    from harness_context.api import make_app

    app = make_app(db, settings, backend=HashingEmbedding(dims=256))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    """Deterministic mini-repo covering every supported language plus skip cases."""
    root = tmp_path / "minirepo"
    (root / "pkg").mkdir(parents=True)
    (root / "node_modules").mkdir()

    (root / "README.md").write_text(
        "# MiniRepo\n\n"
        "This repository stores widget configurations and greps them fast.\n",
        encoding="utf-8",
    )
    (root / "pkg" / "widget_store.py").write_text(
        '"""Widget storage with a hard per-team quota."""\n'
        "\n"
        "MAX_WIDGETS = 1000\n"
        "\n"
        "\n"
        "class WidgetStore:\n"
        "    def __init__(self, quota: int = MAX_WIDGETS):\n"
        "        self.quota = quota\n"
        "        self.widgets: dict[str, dict] = {}\n"
        "\n"
        "    def put(self, widget_id: str, payload: dict) -> bool:\n"
        '        """Store a widget; refuse once the quota is exhausted."""\n'
        "        if len(self.widgets) >= self.quota:\n"
        "            return False\n"
        "        self.widgets[widget_id] = payload\n"
        "        return True\n",
        encoding="utf-8",
    )
    (root / "pkg" / "telemetry.py").write_text(
        "# Telemetry emitter: ships metrics over UDP.\n"
        "def emit(metric: str, value: float) -> None:\n"
        "    print(f'{metric}={value}')\n",
        encoding="utf-8",
    )
    (root / "data.json").write_text(
        '{"widgets": ["alpha", "beta"], "retries": 3}\n', encoding="utf-8"
    )
    (root / "schema.sql").write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);\n", encoding="utf-8"
    )
    (root / "skipme.txt").write_text("unsupported extension\n", encoding="utf-8")
    (root / "node_modules" / "junk.js").write_text("var junk = 1;\n", encoding="utf-8")
    return root
