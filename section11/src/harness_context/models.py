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


class DocIndexRequest(CamelModel):
    """POST /api/v1/context/docs/index body — exactly ONE of folder/urls."""

    project_id: str = Field(..., min_length=1)
    folder_path: str | None = Field(default=None, min_length=1, max_length=4096)
    urls: list[str] | None = Field(default=None, max_length=200)


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


# ---------------------------------------------------------------------------
# S1 additive surface (A2): multi-repo search, ranking, history, versions.
# All NEW shapes below are additive to the frozen W21-C1 contract; the
# ScoredChunk unit is reused verbatim wherever per-project results appear.
# ---------------------------------------------------------------------------


class MultiSearchRequest(CamelModel):
    """POST /api/v1/context/search-multi body (F013).

    ``projectIds`` is bounded (1..10) so one request can never fan out over a
    whole workspace; embedding runs ONCE for the shared query.
    """

    query: str = Field(..., min_length=1)
    project_ids: list[str] = Field(..., min_length=1, max_length=10)
    top_k: int = Field(default=8, ge=1, le=50)


class ProjectResults(CamelModel):
    project_id: str
    results: list[ScoredChunk]


class MultiSearchResponse(CamelModel):
    query: str
    results: list[ProjectResults]


class RankRequest(CamelModel):
    """POST /api/v1/context/rank body (F016) — goal -> token-budgeted bundle."""

    goal: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    max_tokens: int = Field(default=8000, ge=500, le=64000)
    top_k: int = Field(default=12, ge=1, le=50)


class GitSearchRequest(CamelModel):
    """POST /api/v1/context/git-search body (F012).

    No repoPath field BY DESIGN: the searched root is resolved server-side
    from the project's most recent index job (fail closed when unknown) —
    a client can never point history retrieval at an arbitrary directory.
    """

    project_id: str = Field(..., min_length=1)
    query: str = ""  # blank + no path => most recent commits
    limit: int = Field(default=20, ge=1, le=50)
    path: str | None = None  # optional repo-relative pathspec filter


class CommitFileModel(CamelModel):
    status: str  # A/M/D/T/R/C…
    path: str  # new path for renames/copies


class HistoryCommitModel(CamelModel):
    sha: str
    subject: str
    author: str
    date_iso: str
    files: list[CommitFileModel] = []
    files_truncated: int = 0


class GitSearchResponse(CamelModel):
    project_id: str
    query: str
    reason: str | None = None  # None on success; else not_a_git_repo|empty_history|…
    commits: list[HistoryCommitModel] = []


class SymbolReferenceModel(CamelModel):
    path: str
    start_line: int
    kind: str  # definition | reference
    snippet: str


class SymbolsResponse(CamelModel):
    project_id: str
    name: str | None = None
    definitions: list[dict] = []
    references: list[SymbolReferenceModel] = []
    truncated: bool = False
    note: str = ""
