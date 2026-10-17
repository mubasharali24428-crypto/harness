"""Conservative secret scanning for indexed content (S1 hardening).

Two controls, one goal — secrets must never be hashed into the shared
embedding/index caches:

1. :func:`is_secret_file` — file NAMES that are conventionally secret storage
   (``.env``, private keys, cloud credential files). These are excluded from
   ingestion entirely by ``chunking.should_index``: no read, no hash, no embed.
2. :func:`scrub_secret_text` — high-confidence secret PATTERNS inside
   otherwise-indexable content (a token pasted into a README or a config value
   in a source file). Matches are replaced in-place with ``[REDACTED:<kind>]``
   BEFORE chunking, so the stored ``content`` and the ``chunk_hash`` both
   reflect the redaction, and the embedding never sees the raw value.

Precision over recall on purpose: only patterns that are essentially never
legitimate source code are scrubbed (PEM blocks, AWS access-key IDs, provider
token prefixes, JWTs). Fuzzy "anything that looks like a password" heuristics
are NOT applied — over-redacting code (``password: str`` fields, docs about
secrets) would destroy retrieval quality, which is this pillar's product.

Determinism: both functions are pure; identical input always yields identical
output, so incremental re-index hash comparisons stay stable. Replacement
strings contain no newlines, so line numbers/ranges of every chunk remain
valid.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 1. Secret-named files: never read, never indexed.
# ---------------------------------------------------------------------------

_SECRET_FILE_EXACT = frozenset(
    {
        ".env",
        ".netrc",
        ".pgpass",
        ".npmrc",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
        "secring.gpg",
    }
)

_SECRET_FILE_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".ppk",
)

# ``.env.production`` / ``.env.local`` style variants, and ``.envrc``.
_SECRET_FILE_PREFIXES = (".env.", ".envrc")


def is_secret_file(name: str) -> bool:
    """True for file names that conventionally store credentials/keys.

    ``name`` is a basename or repo-relative path; only the basename is
    consulted (a directory called ``.env`` is not a thing practitioners do).
    """
    base = name.rsplit("/", 1)[-1]
    lowered = base.lower()
    if lowered in _SECRET_FILE_EXACT:
        return True
    if lowered.startswith(_SECRET_FILE_PREFIXES):
        return True
    return lowered.endswith(_SECRET_FILE_SUFFIXES)


# ---------------------------------------------------------------------------
# 2. Content scrubbing: high-confidence patterns only.
# ---------------------------------------------------------------------------

# Ordered: most specific first. Each entry is (kind, compiled pattern).
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        # PEM blocks, any flavour (RSA/EC/OPENSSH/PGP ... KEY-----( )?) including
        # the header/footer lines so the whole block disappears.
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    (
        "github-token",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b"),
    ),
    (
        "github-pat",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,255}\b"),
    ),
    (
        "slack-token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "openai-style-key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "google-api-key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
    ),
)


def scrub_secret_text(text: str) -> str:
    """Replace high-confidence secret patterns with ``[REDACTED:<kind>]``.

    Pure and idempotent: scrubbing already-scrubbed text changes nothing
    (the placeholder matches no pattern). Line count is preserved.
    """
    if not text:
        return text
    out = text
    for kind, pattern in _PATTERNS:
        out = pattern.sub(f"[REDACTED:{kind}]", out)
    return out


def scrub_secret_paths_seen(text: str) -> list[str]:
    """Kinds of secrets present in ``text`` (for logs/metrics, never values)."""
    kinds: list[str] = []
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            kinds.append(kind)
    return kinds
