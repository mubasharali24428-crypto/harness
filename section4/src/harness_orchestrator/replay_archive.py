"""F111 — content-addressed archive for run replay records.

Follows ``diffstore.py``'s contract exactly (that module's phase-1 note said a
later lane could persist replay-relevant bodies this way):

* a replay RECORD (composed prompt + engine result + observations) is written
  once to a local content-addressed store, keyed by sha256 of the canonical
  JSON body;
* the event trail carries only a small immutable reference
  ``{"kind": "replay-record", "sha", "path", "bytes"}`` so replay is
  restart-safe without growing event payloads past the bus's publish law.

Redaction law: nothing reaches disk before :func:`redact_record` has run.
Configured secrets are replaced via the same ``[redacted]`` literal the
engine already uses (``redact.scrub_secrets``), and high-confidence key
patterns are replaced with the same ``[REDACTED:<kind>]`` markers section11's
``secret_scan`` emits — mirrored here (5 patterns) because cross-section
imports are forbidden, not forked for taste.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from harness_orchestrator.redact import scrub_secrets

RECORD_REF_KIND = "replay-record"

#: Hard body bound — a replay record is an observation digest, not a log dump.
MAX_RECORD_BYTES = 512 * 1024

#: Mirrors the high-confidence set in section11 ``secret_scan.py``.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b")),
    ("github-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,255}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
)


def record_root() -> Path:
    raw = os.environ.get("HARNESS_REPLAY_ROOT", "")
    if raw:
        return Path(raw)
    return Path(os.environ.get("TMPDIR") or "/tmp") / "harness_replay"


def redact_record(value: Any, *, secrets: tuple[str | None, ...] = ()) -> tuple[Any, int]:
    """Return (redacted copy, replacements) — never mutates the input."""
    replaced = 0

    def scrub_text(text: str) -> str:
        nonlocal replaced
        out = scrub_secrets(text, *secrets)
        if out != text:
            replaced += 1
        for kind, pattern in _SECRET_PATTERNS:
            out, hits = pattern.subn(f"[REDACTED:{kind}]", out)
            replaced += hits
        return out

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return scrub_text(node)
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        return node

    return walk(value), replaced


def save_record(record: dict[str, Any]) -> dict[str, Any]:
    """Persist one (already redacted) record; return the wire-safe reference."""
    body = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
    if len(body) > MAX_RECORD_BYTES:
        raise ValueError(
            f"replay record exceeds {MAX_RECORD_BYTES} bytes; cap it in the builder"
        )
    sha = hashlib.sha256(body).hexdigest()
    rel = f"{sha[:2]}/{sha}.json"
    path = record_root() / rel
    if not path.is_file():
        root = record_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        os.chmod(tmp, 0o600)  # records carry composed prompts: owner-only even under /tmp
        os.replace(tmp, path)  # atomic publish; racing writers write identical bytes
    return {"kind": RECORD_REF_KIND, "sha": sha, "path": rel, "bytes": len(body)}


def load_record(ref: Any) -> dict[str, Any] | None:
    """Fetch a record by reference. None when absent, malformed, or tampered."""
    if not isinstance(ref, dict) or ref.get("kind") != RECORD_REF_KIND:
        return None
    rel = ref.get("path")
    if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in rel:
        return None
    root = record_root().resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    data = path.read_bytes()
    sha = ref.get("sha")
    if isinstance(sha, str) and sha and hashlib.sha256(data).hexdigest() != sha:
        return None  # integrity mismatch — refuse to serve corrupted records
    try:
        loaded = json.loads(data)
    except ValueError:
        return None
    return loaded if isinstance(loaded, dict) else None
