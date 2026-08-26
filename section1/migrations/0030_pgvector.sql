-- Mission 21 X1 — Context Pillar: pgvector + context_chunks (codebase index).
--
-- One row per indexed chunk: a line-window of one file of one project.
-- Identity = (project_id, path, start_line) — UNIQUE, so re-indexing upserts
-- windows in place and never duplicates rows for shifted content.
--
-- chunk_hash (sha256 over path+bounds+content) is the incremental skip key:
-- harness_context.service compares freshly computed hashes against the stored
-- set and re-embeds only genuine changes; rows whose file vanished from disk
-- are pruned by path.
--
-- embedding uses vector(256): Section 11's reference backend
-- (harness_context.embeddings.HashingEmbedding) emits exactly this dimension,
-- and Ollama models are configured to match at attach time. Widening or
-- swapping dims = NEW migration that recreates indexes + re-embeds; do not
-- ALTER type in place (existing vectors become unreadable).
--
-- Mechanics per repo conventions:
--   * No BEGIN/COMMIT (deploy/runners/migrate.py wraps each file in one tx).
--   * CREATE EXTENSION IF NOT EXISTS + CREATE INDEX IF NOT EXISTS keep reruns
--     and legacy-bootstrap databases safe.
--   * HNSW (m32/efc128, cosine ops) chosen over IVFFlat: no training step, so
--     an empty table is immediately queryable and recall is stable as the
--     index grows from zero.
--
-- M21-CI repair: OPTIONAL EXTENSION, honest degradation. The vector type is
-- provided by the pgvector extension, which stock postgres:* images do NOT
-- ship (only pgvector/pgvector:* does). A hard CREATE EXTENSION here made
-- every fresh install on vanilla Postgres die mid-chain and roll back the
-- WHOLE schema (migrate() runs one tx per file — CI's migrations-pg job
-- caught this against postgres:18-alpine). Gate on pg_available_extensions
-- instead: when pgvector is installed on the HOST we create the extension +
-- the full index store; when it is not, this migration still lands (ledger
-- row written) but creates NOTHING, and harness_context degrades to its
-- documented empty-index behavior (search returns [], indexing reports
-- unavailable — advisory posture, never a crash; SQLite suites already run
-- that way via DDL_SQLITE). Provisioning law stays with deployment: point
-- compose's db service and CI's real-PG jobs at a pgvector-bearing image
-- (pgvector/pgvector:pg18) to get the feature. Because the TABLE's embedding
-- column needs the type too, ALL statements below are conditional — hence
-- the DO block with EXECUTE'd DDL (dollar-quoted so no quote-doubling).
DO $$
DECLARE
    have_pgvector boolean;
BEGIN
    SELECT count(*) > 0 INTO have_pgvector
        FROM pg_available_extensions WHERE name = 'vector';
    IF NOT have_pgvector THEN
        RAISE NOTICE 'pgvector not available on this Postgres: context_chunks skipped (M21 context feature disabled)';
        RETURN;
    END IF;

    EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector';

    EXECUTE $ddl$
        CREATE TABLE IF NOT EXISTS context_chunks (
            id SERIAL PRIMARY KEY,
            project_id VARCHAR(36) NOT NULL,
            path TEXT NOT NULL,
            lang VARCHAR(40) NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding vector(256) NOT NULL,
            chunk_hash CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now() NOT NULL
        )
    $ddl$;

    -- Chunk identity: one window per project+path+start. Upsert target of
    -- harness_context.store._UPSERT_SQL (ON CONFLICT (project_id, path, start_line)).
    EXECUTE $ddl$
        CREATE UNIQUE INDEX IF NOT EXISTS uq_context_chunks_window
            ON context_chunks (project_id, path, start_line)
    $ddl$;

    -- Point lookups driving the incremental pass (load_hashes / prune-by-path).
    EXECUTE $ddl$
        CREATE INDEX IF NOT EXISTS idx_context_chunks_project_path
            ON context_chunks (project_id, path)
    $ddl$;

    -- ANN index for cosine search over embeddings (production fast path).
    EXECUTE $ddl$
        CREATE INDEX IF NOT EXISTS idx_context_chunks_embedding_hnsw
            ON context_chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 32, ef_construction = 128)
    $ddl$;
END
$$;
