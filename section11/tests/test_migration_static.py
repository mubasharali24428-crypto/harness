"""Migration 0030 static law: slot uniqueness + pgvector statements present.

The LIVE apply walk against real PG (pgvector extension + HNSW index) runs in
tests/test_live_pg.py when Docker is available; this file keeps the cheap
invariants green everywhere.
"""

import re
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = HARNESS_ROOT / "section1" / "migrations"


def _migration_text() -> str:
    return (MIGRATIONS_DIR / "0030_pgvector.sql").read_text(encoding="utf-8")


def test_slot_0030_is_unique():
    prefixes = [p.name.split("_", 1)[0] for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    counts = {}
    for v in prefixes:
        counts[v] = counts.get(v, 0) + 1
    dupes = {v: c for v, c in counts.items() if c > 1}
    assert dupes == {}, f"duplicate migration prefixes would crash migrate(): {dupes}"


def test_migration_contains_pgvector_law():
    sql = _migration_text()
    # M22-M2 attribution: this pin was left stale by cb9af7e (M21 CI repair) —
    # the extension statement became conditional inside a DO block
    # (`EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector';` — no bare literal
    # with a trailing semicolon anymore). The LAW is unchanged: 0030 must
    # still idempotently ensure the vector extension.
    assert re.search(r"CREATE EXTENSION IF NOT EXISTS vector", sql)
    assert "CREATE TABLE IF NOT EXISTS context_chunks" in sql
    cols = (
        "project_id",
        "path",
        "lang",
        "start_line",
        "end_line",
        "content",
        "embedding",
        "chunk_hash",
    )
    for col in cols:
        assert re.search(rf"\b{col}\b", sql), f"context_chunks missing column {col}"
    assert re.search(r"embedding\s+vector\(\d+\)", sql)
    assert "USING hnsw" in sql and "vector_cosine_ops" in sql
    assert "UNIQUE INDEX" in sql.upper()


def test_migration_has_no_transaction_control():
    """migrate() wraps each file in one transaction — nested BEGIN breaks it."""
    sql = _migration_text()
    assert not re.search(r"(?mi)^\s*BEGIN\s*;", sql)
    assert not re.search(r"(?mi)^\s*COMMIT\s*;", sql)
