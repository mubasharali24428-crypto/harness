"""Provider secret scrubbing for engine-derived text.

Mirrors section3 providers' _scrub semantics: literal replacement of any
configured secret that could surface in logs, events, or the UI. Engine
exceptions may embed provider bodies or the Authorization header, and the
never-log-or-model-context rule applies to every surface a string reaches.
"""

import re


def scrub_secrets(text: str, *secrets: str | None) -> str:
    """Replace each non-empty secret with “[redacted]”, leaving other text
    untouched (honest error wording stays honest)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*?(?:api[_-]?key|secret|token|password|passwd|authorization|access[_-]?key)"
    r"\s*[=:]\s*)(?:(?:bearer|basic|token)\s+)?(\"[^\"]*\"|'[^']*'|\S+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{6,}")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
# Bare, well-known token shapes (GitHub, Slack, AWS, Google, JWT) with no key name in front.
_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})"
)


def scrub_secret_patterns(text: str) -> str:
    """Mask secret-shaped material regardless of whether the literal value is
    known: PEM private-key blocks, secret-bearing KEY=value / KEY: value
    assignments (incl. an optional Bearer/Basic scheme and quoted values with
    spaces), bare Bearer/Basic credentials, sk- keys, and bare GitHub / Slack /
    AWS / Google / JWT tokens. Complements scrub_secrets; prefer
    over-redaction to a leak. Normal code identifiers (tokenizer=, token_count=)
    survive."""
    text = _PEM_RE.sub("[redacted-private-key]", text)
    text = _SECRET_ASSIGN_RE.sub(r"\1[redacted]", text)
    text = _BEARER_RE.sub(r"\1 [redacted]", text)
    text = _SK_RE.sub("[redacted]", text)
    text = _TOKEN_RE.sub("[redacted]", text)
    return text