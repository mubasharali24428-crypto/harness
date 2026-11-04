"""M21-X2 — @refs resolution service (planner feed + context budget).

Commander's intent: typing ``@auth.py`` in a meeting brief means the agent
actually reads auth.py. This module is the bridge: it parses @refs out of
brief/meeting text, resolves each one against Section 11's FROZEN W21-C1
search API, and assembles a bounded, planner-ready context bundle.

W21-C1 contract (section11 ContextService.search — frozen):
    search(query, project_id, top_k) -> [{path, lang, startLine,
                                          endLine, content, score}]
served over HTTP at POST /api/v1/context/search with a camelCase wire body
{query, projectId, topK} (api-conventions.md law).

ISOLATION LAW (M13 audit / AGENTS.md): sections depend ONLY on
harness_contracts. Section 4 must never import harness_context (the guard's
package rule does not know section11 yet — that gap is NOT license). So this
resolver consumes the frozen API over HTTP like every other cross-section
call (see harness_meeting.ai_participant for the established client pattern).
The local :class:`ResolvedChunk` mirrors ScoredChunk additively; if the wire
grows fields they are ignored here.

POSTURE — advisory, by design and by law (same discipline as rules_loader):
a dead/unreachable context service degrades to an EMPTY bundle with a
warning log. Resolving refs can never break planning. What IS enforced is
the BUDGET: total bundle tokens are capped and overflow drops the
lowest-scored chunks FIRST — never silently truncating mid-chunk.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace

from harness_contracts.http_client import env_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Budget configuration (env-tunable; CONTEXT_MAX_TOKENS per mission brief)
# ---------------------------------------------------------------------------

DEFAULT_CONTEXT_MAX_TOKENS = 4000

# Retrieval scoring threshold floor default. STRICTLY POSITIVE by design —
# W21-C1 cosine scores are signed (see apply_score_threshold).
DEFAULT_MIN_SCORE = 0.05


def resolve_context_max_tokens(env: dict[str, str] | None = None) -> int:
    """Read ``CONTEXT_MAX_TOKENS``; junk/absent values fall back to default."""
    source = os.environ if env is None else env
    raw = source.get("CONTEXT_MAX_TOKENS")
    if raw is None:
        return DEFAULT_CONTEXT_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CONTEXT_MAX_TOKENS
    return value if value > 0 else DEFAULT_CONTEXT_MAX_TOKENS


# ---------------------------------------------------------------------------
# Endpoint configuration (env-tunable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextResolverSettings:
    """Endpoint of Section 11's frozen W21-C1 search API."""

    base_url: str | None  # None ⇒ @refs resolution is OFF
    token: str | None


def context_settings_from_env(
    env: dict[str, str] | None = None,
) -> ContextResolverSettings:
    """Read ``CONTEXT_SEARCH_BASE_URL`` / ``CONTEXT_SEARCH_TOKEN``.

    An unset base URL disables @refs resolution entirely: the planner then
    behaves exactly as it did before M21-X2 (no network, no bundle).
    The token is optional but normally required — Section 11's routes sit
    behind the shared contracts bearer dep.
    """
    source = os.environ if env is None else env
    base_url = (source.get("CONTEXT_SEARCH_BASE_URL") or "").strip() or None
    token = (source.get("CONTEXT_SEARCH_TOKEN") or "").strip() or None
    return ContextResolverSettings(base_url=base_url, token=token)


# ---------------------------------------------------------------------------
# Ref parsing (@path/to/file.ext and @symbol-name)
# ---------------------------------------------------------------------------

# A ref's "@" must START a token (preceded by text start, whitespace, or
# opening punctuation): this kills email addresses (``bob@corp.com``) while
# accepting ``(see @auth.py)`` / ``**@auth.py**`` / list items.
REF_PATTERN = re.compile(
    r"(?<![\w/.@])"
    r"@(?P<ref>[\w./+\-]+)"
    r"(?<!\.)"  # no trailing dot on the match
    r"(?=[.,;:!?)\]}'\"*`]*(\s|$))",
)

# Extensions that make an @ref a FILE reference. Deliberately a closed,
# code-flavored set: ``@WidgetStore.put`` must parse as a SYMBOL, not as a
# file with a ".put" extension.
_FILE_EXTS = frozenset(
    """py pyi ts tsx js jsx mjs cjs go rs rb java kt k swift c cc cpp cxx h hpp
    cs php pl lua sh bash zsh fish ps1 sql md mdx rst txt json json5 yaml yml
    toml ini cfg conf css scss sass less html htm vue svelte graphql proto
    tf tfvars env lock csv""".split()
)

# Symbol shape: identifier-ish, optionally dotted (pkg.Class.method).
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


def _is_file_ref(raw: str) -> bool:
    dot = raw.rfind(".")
    return dot > 0 and raw[dot + 1 :].lower() in _FILE_EXTS


@dataclass(frozen=True)
class Ref:
    """One parsed reference from brief/meeting text."""

    kind: str  # "file" | "symbol"
    name: str
    span: tuple[int, int]  # character offsets into the source text


def parse_refs(text: str) -> list[Ref]:
    """Extract @refs from brief/meeting text, in order of appearance.

    - ``@path/to/file.ext`` → kind="file" (any token ending in a plausible
      file suffix)
    - ``@symbol-name`` / ``@Class.method`` → kind="symbol"
    Duplicates are kept (spans differ); ordering is textual.
    """
    refs: list[Ref] = []
    for m in REF_PATTERN.finditer(text or ""):
        raw = m.group("ref")
        start = m.start("ref") - 1  # include the '@' in the span
        end = m.end("ref")
        if _is_file_ref(raw):
            kind = "file"
        elif _SYMBOL.fullmatch(raw):
            kind = "symbol"
        else:
            continue
        refs.append(Ref(kind=kind, name=raw, span=(start, end)))
    return refs


# ---------------------------------------------------------------------------
# Resolution against the frozen W21-C1 search API (HTTP, camelCase wire)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedChunk:
    """Mirror of W21-C1 ScoredChunk (camelCase wire shape, frozen).

    ``confirmed`` is CLIENT-SIDE annotation, not part of the wire shape:
    True when the chunk's path lexically confirms the @file ref that
    retrieved it (the resolver's hybrid ranking — see ``resolve_context``).
    """

    path: str
    lang: str
    start_line: int
    end_line: int
    content: str
    score: float
    confirmed: bool = False


@dataclass
class ContextBundle:
    """Bounded planner feed assembled from resolved @refs."""

    project_id: str | None
    max_tokens: int
    refs: list[Ref]
    chunks: list[ResolvedChunk] = field(default_factory=list)
    dropped_chunks: int = 0  # budget evictions (lowest score first)

    def render(self) -> str:
        """Planner prompt block; "" when nothing was resolved."""
        if not self.chunks:
            return ""
        parts = [CONTEXT_BLOCK_HEADER]
        for c in self.chunks:
            parts.append(f"@@ {c.path}:{c.start_line}-{c.end_line} ({c.lang})")
            parts.append(c.content.rstrip())
        parts.append(CONTEXT_ADVISORY_NOTE)
        return "\n".join(parts)


CONTEXT_BLOCK_HEADER = "RETRIEVED CODEBASE CONTEXT (from @refs in the brief)"
CONTEXT_ADVISORY_NOTE = (
    "This context was retrieved from the indexed codebase to ground plan "
    "proposal; it is advisory input, not an execution permission."
)


class ContextSearchError(RuntimeError):
    """The frozen search API answered with an error status."""


async def search_index(
    base_url: str,
    query: str,
    project_id: str,
    *,
    top_k: int = 8,
    token: str | None = None,
    transport=None,
    timeout_seconds: float = 10.0,
) -> list[ResolvedChunk]:
    """Call POST {base_url}/api/v1/context/search (W21-C1, frozen).

    ``transport`` accepts an httpx transport so tests can mount the real
    section11 ASGI app without sockets; production leaves it None.
    """

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {"query": query, "projectId": project_id, "topK": top_k}
    async with env_client(
        timeout=timeout_seconds, transport=transport
    ) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/api/v1/context/search",
            json=payload,
            headers=headers,
        )
    if resp.status_code >= 400:
        raise ContextSearchError(
            f"context search failed ({resp.status_code}) for query {query!r}"
        )
    data = resp.json()
    results = data.get("results", []) if isinstance(data, dict) else []
    chunks: list[ResolvedChunk] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        try:
            chunks.append(
                ResolvedChunk(
                    path=str(row["path"]),
                    lang=str(row.get("lang", "")),
                    start_line=int(row["startLine"]),
                    end_line=int(row["endLine"]),
                    content=str(row["content"]),
                    score=float(row["score"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("skipping malformed search result row: %r", row)
    return chunks


# ---------------------------------------------------------------------------
# Bundle assembly: threshold + per-ref retrieval + token budget
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Cheap deterministic token estimate (~4 chars/token heuristic).

    Deliberately NOT a tokenizer dependency: the cap only needs to be stable
    and conservative, not exact. CJK-heavy text counts higher (per-char),
    which errs toward dropping earlier — safe direction for a budget.
    """
    if not text:
        return 0
    wide = sum(1 for ch in text if unicodedata.east_asian_width(ch) in ("W", "F"))
    narrow = len(text) - wide
    return narrow // 4 + wide * 2 + 1


def make_retrieval_query(ref: Ref) -> str:
    """Turn a parsed ref into a W21-C1 query string."""
    if ref.kind == "file":
        # The index stores relative paths; querying the basename plus the
        # tail of the given path retrieves that file's chunks best.
        return ref.name.rsplit("/", 1)[-1]
    return ref.name


def _path_confirms(chunk_path: str, ref_name: str) -> bool:
    """True when ``chunk_path`` lexically confirms a @file ref.

    A ref confirms when chunk_path EQUALS it or ENDS WITH ``/`` + ref:
    a pure-filename ref (@auth.py) matches by basename (src/auth/utils/auth.py
    counts — the user named the file they want); a directory-qualified ref
    (@pkg/util.py) matches as whole-segment path suffix, so directory context
    given is directory context honored. Case-sensitive on purpose: code paths
    are case-sensitive where this runs.
    """
    if not ref_name or not chunk_path:
        return False
    return chunk_path == ref_name or chunk_path.endswith(f"/{ref_name}")


def apply_score_threshold(chunks: list[ResolvedChunk], min_score: float) -> list[ResolvedChunk]:
    """Drop chunks below the retrieval scoring threshold.

    The floor must be STRICTLY POSITIVE: W21-C1 cosine scores are signed
    (hash backends project tokens with +/- signs; verified live — a chunk
    anti-correlated with its query scored -0.19). A 0.0 floor would admit
    zero-signal chunks that merely outrank genuinely negative ones.
    """
    return [c for c in chunks if c.score > min_score]


def assemble_bundle(
    chunks: list[ResolvedChunk],
    *,
    project_id: str | None,
    max_tokens: int,
    refs: list[Ref] | None = None,
) -> ContextBundle:
    """Assemble a budgeted bundle from already-resolved chunks.

    Admission is purely score-driven (mission law): chunks are greedily kept
    in descending score order, and overflow drops the LOWEST-scored chunks
    first — never silently truncating mid-chunk (a chunk fits whole or is
    dropped and counted in ``dropped_chunks``).

    Presentation order is a separate concern: within the KEPT set,
    lexically-confirmed file-ref hits (``confirmed=True``, set by
    ``resolve_context``'s hybrid pass) are listed before everything else,
    each group score-descending — so ``@auth.py`` leads the planner feed
    even when collision noise out-scored it at retrieval time.
    """
    ordered = sorted(chunks, key=lambda c: c.score, reverse=True)
    kept: list[ResolvedChunk] = []
    dropped = 0
    used = 0
    for chunk in ordered:
        cost = estimate_tokens(chunk.content)
        if used + cost <= max_tokens:
            kept.append(chunk)
            used += cost
        else:
            dropped += 1
    kept.sort(key=lambda c: (not c.confirmed, -c.score))
    return ContextBundle(
        project_id=project_id,
        max_tokens=max_tokens,
        refs=list(refs or []),
        chunks=kept,
        dropped_chunks=dropped,
    )


async def resolve_context(
    text: str,
    *,
    project_id: str | None,
    base_url: str,
    token: str | None = None,
    top_k_per_ref: int = 4,
    min_score: float = 0.05,
    max_tokens: int | None = None,
    transport=None,
    timeout_seconds: float = 10.0,
) -> ContextBundle:
    """Parse @refs from ``text``, resolve each via the frozen search API,
    and assemble a budgeted bundle for the planner.

    Hybrid ranking (client-side; W21-C1 stays untouched): a @file ref's
    chunks whose path lexically CONFIRMS the ref (basename or path-suffix
    match) are promoted above everything the score alone would order —
    verified live against X1's hashing backend, where ``@auth.py`` queried
    verbatim can rank LAST on a small corpus while hash-collision noise
    scores 0.17. The embedding layer stays authoritative for symbols and for
    unconfirmed file hits.

    Advisory posture: ANY resolution failure (unreachable service, error
    status, malformed rows) degrades to whatever was gathered so far —
    possibly an empty bundle — with a warning log. Planning never breaks.
    """
    if max_tokens is None:
        max_tokens = resolve_context_max_tokens()
    refs = parse_refs(text)
    if not refs:
        return ContextBundle(project_id=project_id, max_tokens=max_tokens, refs=[])

    confirmed_first: list[ResolvedChunk] = []
    confirmed_keys: set[tuple[str, int, int]] = set()

    def _admit(chunk: ResolvedChunk) -> bool:
        key = (chunk.path, chunk.start_line, chunk.end_line)
        if key in confirmed_keys:
            return False
        confirmed_keys.add(key)
        return True

    for ref in refs:
        try:
            found = await search_index(
                base_url,
                make_retrieval_query(ref),
                project_id or "",
                top_k=top_k_per_ref,
                token=token,
                transport=transport,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — advisory: degrade, don't raise
            logger.warning(
                "context resolve failed for ref @%s; continuing without it (%s)",
                ref.name,
                exc,
            )
            continue

        if ref.kind == "file":
            # Pass A: lexically CONFIRMED hits bypass the score threshold
            # entirely — an explicit @ref IS the relevance signal (verified
            # live against X1's hashing backend: the named file can score
            # 0.0 while collision noise scores 0.17). Pass B: remaining
            # chunks are retrieval guesses -> threshold applies, then score
            # order.
            rest: list[ResolvedChunk] = []
            for chunk in found:
                if _path_confirms(chunk.path, ref.name):
                    promoted = replace(chunk, confirmed=True)
                    if _admit(promoted):
                        confirmed_first.append(promoted)
                else:
                    rest.append(chunk)
            confirmed_first.extend(apply_score_threshold(rest, min_score))
        else:
            confirmed_first.extend(apply_score_threshold(found, min_score))

    return assemble_bundle(
        confirmed_first,
        project_id=project_id,
        max_tokens=max_tokens,
        refs=refs,
    )


# ---------------------------------------------------------------------------
# Planner integration helpers
# ---------------------------------------------------------------------------


def seed_files_from_bundle(bundle: ContextBundle) -> list[str]:
    """Dedupe chunk paths (first-seen order) for plan.filesLikelyToChange seeding."""
    seeded: list[str] = []
    seen: set[str] = set()
    for chunk in bundle.chunks:
        if chunk.path not in seen:
            seen.add(chunk.path)
            seeded.append(chunk.path)
    return seeded
