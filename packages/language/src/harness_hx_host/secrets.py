"""Secret shapes, recognised once and applied at every surface.

Key-based redaction (``"api_key": ...``) covers settings; it does not cover
repository content, prompts or model replies, which are free text and are
exactly where a token turns up. So the shapes below are recognised by prefix,
not by guessing at entropy: a reviewed digest or a commit sha must survive, a
provider key must not.

Three surfaces use this module, and each uses it for the same reason:

* the runtime, before a value becomes an event argument;
* the model relay, before a prompt leaves and before a reply becomes a value;
* the evidence bundle, before anything is persisted.

``scrub`` returns what it found as *kinds and counts* — never the matched text.
A report that says "one provider-key was redacted" is reviewable; one that
repeats the key is the leak it was meant to stop.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

REDACTED = "[redacted]"

#: ``(kind, pattern)``. Ordered: a whole PEM block must win over the fragments
#: inside it, so it is replaced first.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("provider-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("provider-key", re.compile(r"\brk-[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("slack-token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{18,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{28,}\b")),
    (
        "bearer-token",
        re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE),
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    ("url-credential", re.compile(r"://[^/\s:@]+:[^/\s@]+@")),
)


@dataclass(frozen=True)
class Scrub:
    """What was replaced, in the only form safe to keep: kinds and counts."""

    text: str
    kinds: tuple[str, ...] = ()
    count: int = 0

    def __bool__(self) -> bool:
        return self.count > 0


@dataclass
class Tally:
    """The running total for one surface, accumulated across many strings."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, kinds: Iterable[str]) -> None:
        for kind in kinds:
            self.counts[kind] = self.counts.get(kind, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_json(self) -> dict[str, Any]:
        return {"kinds": dict(sorted(self.counts.items())), "total": self.total}


def _literal_pass(text: str, secrets: Iterable[str | None], tally: Tally | None) -> str:
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, REDACTED)
            if tally is not None:
                tally.add(["registered-secret"])
    return text


def scrub(text: str, *, secrets: Iterable[str | None] = (), tally: Tally | None = None) -> Scrub:
    """Replace registered secrets and every recognised shape; keep the counts."""
    kinds: list[str] = []
    text = _literal_pass(text, secrets, tally if tally is not None else Tally())
    for kind, pattern in PATTERNS:
        text, replaced = pattern.subn(REDACTED, text)
        if replaced:
            kinds.extend([kind] * replaced)
    if tally is not None:
        tally.add(kinds)
    return Scrub(text, tuple(sorted(set(kinds))), len(kinds))


def scrub_text(text: str, *, secrets: Iterable[str | None] = ()) -> str:
    """The string-only shortcut, for callers that already keep their own tally."""
    return scrub(text, secrets=secrets).text


def scrub_value(
    value: Any,
    *,
    secrets: Iterable[str | None] = (),
    tally: Tally | None = None,
    depth: int = 0,
) -> Any:
    """Scrub every string inside a value, preserving its shape.

    Shape matters: a scrubbed payload is still handed to a schema validator, so
    keys, list lengths and non-strings must survive untouched.
    """
    if depth > 12:
        return "…"
    if isinstance(value, str):
        return scrub(value, secrets=secrets, tally=tally).text
    if isinstance(value, Mapping):
        return {
            key: scrub_value(item, secrets=secrets, tally=tally, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub_value(item, secrets=secrets, tally=tally, depth=depth + 1) for item in value]
    return value


def find(text: str) -> list[str]:
    """The kinds present in ``text``. Used to assert a surface is clean."""
    found: set[str] = set()
    for kind, pattern in PATTERNS:
        if pattern.search(text):
            found.add(kind)
    return sorted(found)
