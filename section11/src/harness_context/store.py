"""DB access for Section 11 — context_chunks (migration 0030).

Raw parameterized SQL against the section-owned table only; no other section's
tables are touched. Vector columns go through pgvector text round-trip
("[0.1,0.2,...]") which asyncpg accepts as a VARCHAR-typed bind — this is what
keeps the whole store testable on SQLite, where the vector column is TEXT.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# Column list shared by INSERT and SELECT so the two can never drift apart.
_CHUNK_COLS = "project_id, path, lang, start_line, end_line, content, embedding, chunk_hash"

_UPSERT_SQL = f"""
INSERT INTO context_chunks ({_CHUNK_COLS})
VALUES (:project_id, :path, :lang, :start_line, :end_line, :content,
        CAST(:embedding AS vector), :chunk_hash)
ON CONFLICT (project_id, path, start_line)
DO UPDATE SET
    end_line = EXCLUDED.end_line,
    lang = EXCLUDED.lang,
    content = EXCLUDED.content,
    embedding = EXCLUDED.embedding,
    chunk_hash = EXCLUDED.chunk_hash
"""

_SQLITE_UPSERT_SQL = f"""
INSERT INTO context_chunks ({_CHUNK_COLS})
VALUES (:project_id, :path, :lang, :start_line, :end_line, :content,
        :embedding, :chunk_hash)
ON CONFLICT (project_id, path, start_line)
DO UPDATE SET
    end_line = excluded.end_line,
    lang = excluded.lang,
    content = excluded.content,
    embedding = excluded.embedding,
    chunk_hash = excluded.chunk_hash
"""


def to_vector_literal(vec: list[float]) -> str:
    """Floats -> pgvector text literal '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


class ContextStore:
    """All SQL for the context index."""

    def __init__(self, dialect: str | None = None) -> None:
        # "postgresql" forces pgvector CAST syntax; anything else (sqlite in
        # tests) uses plain binds into a TEXT column. None = autodetect per call.
        self._dialect = dialect

    def _is_pg(self, conn: AsyncSession | AsyncConnection) -> bool:
        if self._dialect is not None:
            return self._dialect == "postgresql"
        # Works for both AsyncSession and AsyncConnection.
        return conn.get_bind().dialect.name == "postgresql"

    async def ensure_schema(self, conn: AsyncSession | AsyncConnection, dims: int) -> None:
        """Test/dev convenience: create context_chunks if absent (SQLite suites;
        on PG prefer migration 0030, but CREATE TABLE IF NOT EXISTS keeps dev
        bootstraps working either way)."""
        from .sql import DDL_PG_TEMPLATE, DDL_SQLITE

        ddl = DDL_PG_TEMPLATE.format(dims=dims) if self._is_pg(conn) else DDL_SQLITE
        await conn.execute(text(ddl))

    async def load_hashes(
        self, conn: AsyncSession | AsyncConnection, project_id: str
    ) -> dict[str, dict[tuple[int, int], str]]:
        """(path -> {(start,end): chunk_hash}) for one project's current index."""
        rows = (
            await conn.execute(
                text(
                    "SELECT path, start_line, end_line, chunk_hash "
                    "FROM context_chunks WHERE project_id = :pid"
                ),
                {"pid": project_id},
            )
        ).mappings()
        out: dict[str, dict[tuple[int, int], str]] = {}
        for row in rows:
            out.setdefault(row["path"], {})[(row["start_line"], row["end_line"])] = row[
                "chunk_hash"
            ]
        return out

    async def upsert_chunk(
        self,
        conn: AsyncSession | AsyncConnection,
        *,
        project_id: str,
        chunk,
        embedding: list[float],
    ) -> None:
        """Insert-or-replace one chunk keyed by (project_id, path, start_line)."""
        params = {
            "project_id": project_id,
            "path": chunk.path,
            "lang": chunk.lang,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "content": chunk.content,
            "embedding": to_vector_literal(embedding),
            "chunk_hash": chunk.chunk_hash,
        }
        sql = _UPSERT_SQL if self._is_pg(conn) else _SQLITE_UPSERT_SQL
        await conn.execute(text(sql), params)

    async def delete_file(
        self, conn: AsyncSession | AsyncConnection, project_id: str, path: str
    ) -> int:
        """Prune every row of a file deleted from disk since last index."""
        res = await conn.execute(
            text("DELETE FROM context_chunks WHERE project_id = :pid AND path = :p"),
            {"pid": project_id, "p": path},
        )
        return int(res.rowcount or 0)

    async def delete_pairs(
        self,
        conn: AsyncSession | AsyncConnection,
        project_id: str,
        path: str,
        bounds: set[tuple[int, int]],
    ) -> int:
        """Prune specific stale windows of one file (shrink/re-chunk case)."""
        deleted = 0
        for start, end in sorted(bounds):
            res = await conn.execute(
                text(
                    "DELETE FROM context_chunks WHERE project_id = :pid AND path = :p "
                    "AND start_line = :s AND end_line = :e"
                ),
                {"pid": project_id, "p": path, "s": start, "e": end},
            )
            deleted += int(res.rowcount or 0)
        return deleted

    async def fetch_vectors(self, conn: AsyncConnection, project_id: str) -> list[dict[str, Any]]:
        """Every indexed chunk of a project with its embedding parsed to floats.

        The SQL-side ANN operator (<=> HNSW) is the production fast path; this
        exact-match scan is the portable baseline both dialects share, so test
        truth and prod truth run the same scoring math.
        """
        rows = (
            await conn.execute(
                text(
                    "SELECT path, lang, start_line, end_line, content, embedding "
                    "FROM context_chunks WHERE project_id = :pid"
                ),
                {"pid": project_id},
            )
        ).mappings().all()
        out: list[dict[str, Any]] = []
        for row in rows:
            emb = row["embedding"]
            vec = _parse_embedding(emb)
            out.append(
                {
                    "path": row["path"],
                    "lang": row["lang"],
                    "startLine": row["start_line"],
                    "endLine": row["end_line"],
                    "content": row["content"],
                    "vec": vec,
                }
            )
        return out


def _parse_embedding(raw: Any) -> list[float]:
    """pgvector text '[a,b,...]' / SQLite JSON '["a","b",...]' -> floats."""
    import json as _json

    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("["):
            try:
                parts = _json.loads(s)
                return [float(x) for x in parts]
            except (ValueError, TypeError):
                pass  # fall through to bracket-split parse
        return [float(x) for x in s.strip("[]").split(",") if x.strip()]
    if hasattr(raw, "tolist"):  # pgvector driver may hand back array types
        return [float(x) for x in raw.tolist()]
    return [float(x) for x in raw]
