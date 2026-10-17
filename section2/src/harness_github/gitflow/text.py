"""Sanitising untrusted text (goals, titles, file names) before it lands in commits/PRs."""

from __future__ import annotations

import re

_SECRET_PATTERNS = [
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
    ),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]
_KV_SECRET = re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)(\s*[:=]\s*)\S+")
_CLOSING = re.compile(
    r"(?i)\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)(\s*:?\s+)((?:[\w.-]+/[\w.-]+)?#\d+)"
)
_MENTION = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9])")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def scrub_secrets(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return _KV_SECRET.sub(r"\1\2[REDACTED]", text)


def neutralize(text: str) -> str:
    """Stop untrusted text from pinging people or auto-closing issues on merge."""
    text = _CLOSING.sub(lambda m: f"{m.group(1)}{m.group(2)}`{m.group(3)}`", text)
    return _MENTION.sub("@​", text)


def clean_line(text: str, limit: int) -> str:
    one = " ".join(_CTRL.sub(" ", text).split())
    one = scrub_secrets(one)
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def clean_block(text: str, limit: int) -> str:
    body = scrub_secrets(_CTRL.sub(" ", text).replace("\r\n", "\n"))
    body = body.strip()
    return body if len(body) <= limit else body[: limit - 1].rstrip() + "…"
