"""Empty ``IN`` lists in raw search/export SQL must not be typed as integers.

PostgreSQL rejects ``text_col IN (SELECT CAST(NULL AS INTEGER) ...)`` (``character varying =
integer``) where SQLite accepts it, so an export of a run with no sessions failed only on
PostgreSQL (found by test_session6_live_pg.py). These compile for the PostgreSQL dialect, so they
need no server; the opt-in live test covers the real round trip.
"""

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

from harness_orchestrator import search

SQL = "SELECT 1 FROM comments WHERE scope_id IN :s"


def _pg_sql(values: list) -> str:
    stmt = search._stmt(SQL, {"s": values}).params(s=values)
    compiled = stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True}
    )
    return " ".join(str(compiled).split())


def test_empty_list_renders_as_text_not_integer():
    sql = _pg_sql([])
    assert "CAST(NULL AS VARCHAR)" in sql, sql
    assert "INTEGER" not in sql, sql


def test_string_lists_stay_text():
    assert "::VARCHAR" in _pg_sql(["r1", "r2"])


@pytest.mark.parametrize("values", [[1, 2], [7]])
def test_int_lists_stay_integer(values):
    # agent event keys (``_live``) are integers; they must not be forced to text
    assert "::INTEGER" in _pg_sql(values)


def test_bool_is_not_an_integer_list():
    assert "INTEGER" not in _pg_sql([True, False])


async def test_rows_with_empty_list_returns_nothing_on_sqlite():
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.connect() as db:
            await db.exec_driver_sql("CREATE TABLE comments (scope_id TEXT)")
            await db.exec_driver_sql("INSERT INTO comments VALUES ('r1')")
            assert await search.rows(db, SQL, {"s": []}) == []
            assert len(await search.rows(db, SQL, {"s": ["r1"]})) == 1
    finally:
        await engine.dispose()
