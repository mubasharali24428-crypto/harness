"""Shared DDL for the context index — single source for tests AND dev bootstraps.

Migration 0030_pgvector.sql is the authoritative schema on PG (it also creates
the extension + ANN indexes). This module mirrors that table for SQLite test
fixtures and `ensure_schema` convenience; keep both in lockstep when changing
columns.
"""

from __future__ import annotations

# PG flavor: vector(dims) column + HNSW cosine index. {dims} comes from the
# active embedding backend at ensure_schema time.
DDL_PG_TEMPLATE = """
CREATE TABLE IF NOT EXISTS context_chunks (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    path TEXT NOT NULL,
    lang VARCHAR(40) NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding vector({dims}) NOT NULL,
    chunk_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
)
"""

# SQLite flavor (test fixture): embedding is TEXT holding the pgvector literal;
# chunk identity is enforced with the same UNIQUE constraint as 0030.
DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS context_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id VARCHAR(36) NOT NULL,
    path TEXT NOT NULL,
    lang VARCHAR(40) NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT NOT NULL,
    chunk_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    UNIQUE (project_id, path, start_line)
)
"""
