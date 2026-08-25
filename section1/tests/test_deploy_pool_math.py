"""Mission 13 Wave 3, lane H1a — pool math contract tests (finding H-017).

Pins BOTH ends of docs/pool-math.md so drift fails CI:

* the shared factory (deploy/runners/db.py) produces async engines with
  pool_size=8 / max_overflow=4 (env-overridable, sqlite ⇒ NullPool);
* the compose db service caps Postgres at max_connections=120;
* the doc's published numbers match the code's shipped constants AND the
  arithmetic (8+4)×7 = 84 ≤ 120 actually holds;
* zero ad-hoc ``create_async_engine`` call sites exist anywhere under
  deploy/ outside db.py itself (grep-equivalent, enforced).
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
RUNNERS = DEPLOY / "runners"
_DB_RUNNER = RUNNERS / "db.py"
_SERVICES = RUNNERS / "services.py"
_RETENTION = RUNNERS / "events_retention.py"
_ENTRYPOINT = DEPLOY / "entrypoint.sh"
_COMPOSE = DEPLOY / "docker-compose.yml"
_DOC = REPO_ROOT / "docs" / "pool-math.md"

ASYNCPG_URL = "postgresql+asyncpg://harness:harness@localhost:5432/harness"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None  # noqa: S101 — structural
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------- the factory


async def test_factory_produces_expected_pool_config():
    """THE H-017 assertion: 8 steady + 4 overflow, not SQLAlchemy defaults."""
    db = _load_module("db_poolmath_main", _DB_RUNNER)
    engine = db.build_engine(ASYNCPG_URL)
    try:
        assert type(engine.pool).__name__ == "AsyncAdaptedQueuePool"
        assert engine.pool.size() == 8  # was 5 (sqlalchemy default) pre-H1a
        assert engine.pool._max_overflow == 4  # was 10 (sqlalchemy default)
    finally:
        await engine.dispose()


async def test_env_overrides_retune_the_factory(monkeypatch):
    """Operator knobs HARNESS_DB_POOL_SIZE / HARNESS_DB_MAX_OVERFLOW work."""
    monkeypatch.setenv("HARNESS_DB_POOL_SIZE", "3")
    monkeypatch.setenv("HARNESS_DB_MAX_OVERFLOW", "2")
    db = _load_module("db_poolmath_override", _DB_RUNNER)
    engine = db.build_engine(ASYNCPG_URL)
    try:
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 2
    finally:
        await engine.dispose()


async def test_sqlite_urls_get_nullpool():
    """No server connection ceiling to honor on aiosqlite — knobs are noise."""
    db = _load_module("db_poolmath_sqlite", _DB_RUNNER)
    engine = db.build_engine("sqlite+aiosqlite://")
    try:
        assert type(engine.pool).__name__ == "NullPool"
    finally:
        await engine.dispose()


async def test_session_factory_shape_unchanged():
    """expire_on_commit=False is load-bearing for every make_app consumer."""
    db = _load_module("db_poolmath_shape", _DB_RUNNER)
    engine = db.build_engine(ASYNCPG_URL)
    try:
        factory = db.make_session_factory(engine)
        assert factory.kw["bind"] is engine  # entrypoint.sh reaches the engine this way
        assert factory.kw["expire_on_commit"] is False  # make_app consumers rely on this
    finally:
        await engine.dispose()


async def test_real_get_factory_routes_through_shared_db_module(monkeypatch):
    """Exec services.py's actual get_factory (plus its DATABASE_URL global)
    in isolation — proving the deployed wiring lands on the shared factory
    without tripping services.py's module-level master-key fail-fast."""
    src = _SERVICES.read_text()
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
            "_parse_cors_origins",
            "get_factory",
        ):
            keep.append(node)
        if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
            keep.append(node)
        if (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "DATABASE_URL" for t in node.targets)
        ):
            keep.append(node)
    snippet = ast.Module(body=keep, type_ignores=[])
    ns: dict = {}
    monkeypatch.syspath_prepend(str(RUNNERS))  # get_factory imports its `db` sibling
    exec(compile(snippet, str(_SERVICES), "exec"), ns)  # noqa: S102 — pinned input
    factory = ns["get_factory"]()
    engine = factory.kw["bind"]
    try:
        assert engine.pool.size() == 8
        assert engine.pool._max_overflow == 4
    finally:
        await engine.dispose()


# ------------------------------------------------------------ zero bypasses


def test_zero_adhoc_create_async_engine_call_sites_outside_db_py():
    """Grep-equivalent: deploy/**.py containing create_async_engine( must be
    db.py plus the sanctioned exceptions. A second hit means someone built an
    engine around the documented pool math — change db.py + docs/pool-math.md
    instead."""
    # M17-B2 sanctioned exception: migrate_selftest.py builds its own
    # NullPool/AUTOCOMMIT admin engine — CREATE/DROP DATABASE cannot run
    # inside a transaction block and must never draw from a pooled factory.
    # It is a scratch-DB probe (drops what it creates), so it holds no slot
    # in the 84-of-120 connection budget. See docs/pool-math.md.
    sanctioned = {
        "deploy/runners/db.py",
        "deploy/runners/migrate_selftest.py",
    }
    offenders = []
    for path in sorted(DEPLOY.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if rel in sanctioned:
            continue
        if re.search(r"create_async_engine\s*\(", path.read_text()):
            offenders.append(rel)
    assert offenders == [], (
        f"ad-hoc engine construction bypassing deploy/runners/db.py: {offenders}"
    )


def test_known_consumers_import_the_factory():
    """The two former bare-engine sites now route through db.py."""
    assert "from db import build_engine" in _SERVICES.read_text()
    assert "from db import build_engine" in _RETENTION.read_text()
    # The migrate job boots via entrypoint.sh, which pulls the factory from services.
    assert "get_factory" in _ENTRYPOINT.read_text()


def test_events_retention_sidecar_is_single_engine_by_construction():
    """Retention builds exactly one engine from the shared factory."""
    src = _RETENTION.read_text()
    assert len(re.findall(r"build_engine\s*\(", src)) == 1


# ------------------------------------------- compose cap + doc math (CI law)


def _doc_numbers() -> dict[str, int]:
    """Pull the published values out of docs/pool-math.md's table."""
    text = _DOC.read_text()
    wanted = {
        "pool_size": r"\|\s*`pool_size`\s*\|\s*(\d+)\s*\|",
        "max_overflow": r"\|\s*`max_overflow`\s*\|\s*(\d+)\s*\|",
        "services": r"\|\s*Services with an async engine\s*\|\s*(\d+)\s*\|",
        "max_connections": r"\|\s*Postgres `max_connections`\s*\|\s*(\d+)\s*\|",
    }
    nums: dict[str, int] = {}
    for key, pattern in wanted.items():
        m = re.search(pattern, text)
        assert m, f"docs/pool-math.md lost the '{key}' table row"
        nums[key] = int(m.group(1))
    return nums


def test_doc_numbers_match_shipped_code_constants():
    db = _load_module("db_poolmath_docsync", _DB_RUNNER)
    nums = _doc_numbers()
    assert nums["pool_size"] == db.POOL_SIZE
    assert nums["max_overflow"] == db.MAX_OVERFLOW


def test_compose_caps_postgres_at_120_connections():
    """The pool equation's ceiling: exactly ONE max_connections flag, =120.
    M17-B2 (916e325) appended WAL-G archiving + autovacuum tuning to
    db.command, so this pins the CAP ITSELF instead of exact list equality —
    sibling postgres flags may evolve without touching the budget math."""
    compose = yaml.safe_load(_COMPOSE.read_text())
    cmd = compose["services"]["db"]["command"]
    caps = [a for a in cmd if a.startswith("max_connections=")]
    assert caps == ["max_connections=120"], f"drifted: {cmd}"


@pytest.mark.parametrize(
    ("pool", "overflow", "services", "cap"),
    [
        pytest.param(
            _doc_numbers()["pool_size"],
            _doc_numbers()["max_overflow"],
            _doc_numbers()["services"],
            _doc_numbers()["max_connections"],
            id="published-doc-numbers",
        ),
        pytest.param(8, 4, 7, 120, id="hard-shipped-literal"),
    ],
)
def test_pool_equation_holds(pool, overflow, services, cap):
    worst_case = (pool + overflow) * services
    assert worst_case <= cap, (
        f"({pool}+{overflow})×{services}={worst_case} exceeds PG max_connections={cap}"
    )


def test_services_registry_count_matches_doc():
    """SERVICES dict in services.py is the source of '×7' — keep both honest."""
    src = _SERVICES.read_text()
    block = re.search(r"^SERVICES\s*=\s*\{(.*?)^\}", src, re.S | re.M)
    assert block, "SERVICES registry not found in services.py"
    registered = len(re.findall(r'"[a-z]+":\s*\("app_', block.group(1)))
    assert registered == _doc_numbers()["services"]
