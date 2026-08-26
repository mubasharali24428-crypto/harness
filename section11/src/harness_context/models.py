"""Pydantic wire shapes for Section 11.

Python fields are snake_case; the WIRE is camelCase via to_camel aliases
(api-conventions.md law). The search result shape is HALF of W21-C1 (X1↔X2
pair contract) and is frozen: X2's @ref resolver and X4's RAG planner build
directly on ScoredChunk. Keep it exactly
{path, lang, startLine, endLine, content, score} — additive-only evolution.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """snake_case internally, camelCase on the wire (both directions accepted)."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )


class IndexRequest(CamelModel):
    """POST /api/v1/context/index body."""

    repo_path: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)


class SearchRequest(CamelModel):
    """POST /api/v1/context/search body."""

    query: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)


class ScoredChunk(CamelModel):
    """FROZEN W21-C1 unit: one scored chunk with its exact source location."""

    path: str
    lang: str
    start_line: int
    end_line: int
    content: str
    score: float


class SearchResponse(CamelModel):
    project_id: str
    query: str
    results: list[ScoredChunk]


class IndexStatus(CamelModel):
    """GET /api/v1/context/index/{job_id} body — job progress/result."""

    job_id: str
    project_id: str
    status: str  # pending | indexing | completed | failed
    repo_path: str
    files_indexed: int = 0
    chunks_indexed: int = 0
    files_skipped: int = 0  # unchanged files proven by chunk_hash equality
    files_pruned: int = 0  # deleted-from-disk rows removed from the index
    error: str | None = None
