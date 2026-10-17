"""F020 — documentation ingestion: explicit folder and HTTP(S) sources.

Review gap: "Repo context ingestion exists; no automatic wiki/ticket/ADR
connectors verified." This module is the real slice behind the new
``POST /api/v1/context/docs/index`` route: gather documentation text from an
explicit folder or an explicit list of authenticated HTTP(S) URLs, scrub
secrets (same posture as code ingestion), and hand it to the service for
chunking + embedding into the SAME per-project index, under a ``docs/``
source namespace so retrieval provenance stays legible.

Deliberately NOT a connector framework: no crawling, no third-party APIs —
folder paths and direct document URLs only, bounded per request. Everything
a caller supplies is treated as untrusted input.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from harness_context.secret_scan import is_secret_file

logger = logging.getLogger(__name__)

DOC_EXTS = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc", ".markdown"})
MAX_DOC_BYTES = 256 * 1024
MAX_DOCS_PER_REQUEST = 200
DOC_NAMESPACE = "docs"


def _chunkable_rel(rel: str) -> str:
    """Normalize a doc path onto the chunker's line-window geometry.

    The chunker derives its language (and thus chunking strategy) from the
    path extension and only speaks its SUPPORTED_EXTENSIONS set; docs like
    ``.txt``/``.rst`` are plain text, which IS the markdown window path. The
    stored/indexed path carries ``.md`` for those so chunking never fails —
    the docs namespace is synthetic to begin with.
    """
    suffix = Path(rel).suffix.lower()
    if suffix == ".md":
        return rel
    return f"{rel[: -len(suffix)] if suffix else rel}.md"


@dataclass
class DocIngestReport:
    files_indexed: int = 0
    chunks_indexed: int = 0
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def to_wire(self) -> dict:
        return {
            "filesIndexed": self.files_indexed,
            "chunksIndexed": self.chunks_indexed,
            "skipped": list(self.skipped),
            "errors": list(self.errors),
        }


def _doc_rel_path(rel: str) -> str:
    return f"{DOC_NAMESPACE}/{rel.lstrip('/')}"


def collect_folder_docs(root: Path) -> tuple[list[tuple[str, str]], list[dict]]:
    """Walk ``root`` for documentation files; returns (sources, skipped).

    Sorted for determinism. Secret-named files are excluded through the SAME
    secret-name gate code ingestion uses (``secret_scan.is_secret_file``) —
    a ``.env`` inside a docs folder is exactly as dangerous as one inside a
    repo. The code-ingestion LANGUAGE gate does not apply here.
    """
    sources: list[tuple[str, str]] = []
    skipped: list[dict] = []
    if not root.is_dir():
        return [], [{"path": str(root), "reason": "not_a_directory"}]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        # Secret files are excluded through the same secret-name gate code
        # ingestion uses (a .env inside a docs folder is exactly as dangerous);
        # the code-ingestion LANGUAGE gate does not apply to documentation.
        if is_secret_file(rel) or path.suffix.lower() not in DOC_EXTS:
            skipped.append({"path": _doc_rel_path(rel), "reason": "not_documentation"})
            continue
        size = path.stat().st_size
        if size > MAX_DOC_BYTES:
            skipped.append({"path": _doc_rel_path(rel), "reason": "too_large"})
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            skipped.append({"path": _doc_rel_path(rel), "reason": f"unreadable: {exc}"})
            continue
        sources.append((_doc_rel_path(_chunkable_rel(rel)), text))
    return sources, skipped


def url_doc_rel_path(url: str) -> str:
    """Stable synthetic path for a fetched URL, under the docs namespace."""
    parsed = urllib.parse.urlsplit(url)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1] or "index"
    name = urllib.parse.unquote(tail)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in name)
    if Path(safe).suffix.lower() not in DOC_EXTS:
        safe = f"{safe or 'index'}.md"
    return _doc_rel_path(f"{parsed.netloc}/{safe}")


async def fetch_url(url: str, *, timeout: float = 10.0) -> str:
    """Fetch one document over HTTP(S); raises on transport/status errors."""
    import httpx

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    return resp.text
