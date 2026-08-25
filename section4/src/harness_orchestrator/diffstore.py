"""M19-N2 (C8, pbc1 F9) — done-signal evidence lives beside the bus, not on it.

A done-signal may carry up to ``DONE_DIFF_TEXT_MAX`` (200_000 chars) of diff
text. Before this module nothing stopped that body from being folded into an
event payload — where M19-N2's 8KB publish law (section1 EventBus) now
correctly refuses it. The contract is therefore:

* the BODY is written once, content-addressed by sha256, to a local store
  (``HARNESS_DIFFSTORE_ROOT``, default ``$TMPDIR/harness_diffs``);
* the WIRE carries only a small immutable reference
  ``{"kind": "checkpoint", "sha", "path", "bytes"}``;
* consumers fetch the body table-/store-side (``OrchestratorService.
  get_done_diff``) instead of reading it off the bus.

Plain-text evidence stays accepted everywhere: ``resolve_diff`` treats a bare
string as the body itself (recorded flows and unit tests pass text directly),
so the reference flow is strictly additive.

Phase-1 honesty note: the store is process-local disk. Bodies survive
restarts; the in-RAM pointer (``run_data["done_diff_ref"]``) does not — a
later lane can persist references durably if replay needs them.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

DIFF_REF_KIND = "checkpoint"


def store_root() -> Path:
    raw = os.environ.get("HARNESS_DIFFSTORE_ROOT", "")
    if raw:
        return Path(raw)
    return Path(os.environ.get("TMPDIR") or "/tmp") / "harness_diffs"


def save_diff(diff_text: str) -> dict[str, Any]:
    """Persist one diff body; return the wire-safe reference."""
    body = diff_text.encode("utf-8")
    sha = hashlib.sha256(body).hexdigest()
    rel = f"{sha[:2]}/{sha}.diff"
    path = store_root() / rel
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)  # atomic publish; racing writers write identical bytes
    return {"kind": DIFF_REF_KIND, "sha": sha, "path": rel, "bytes": len(body)}


def load_diff(ref: dict[str, Any]) -> str | None:
    """Fetch a body by reference. None when absent or tampered."""
    if not isinstance(ref, dict):
        return None
    rel = ref.get("path")
    if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in rel:
        return None
    root = store_root().resolve()
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
        return None  # integrity mismatch — refuse to serve corrupted bodies
    return data.decode("utf-8")


def resolve_diff(evidence: Any) -> str:
    """Done-signal evidence -> diff text.

    Accepts the body itself (str — legacy/recorded flows) or a stored
    reference dict. Anything else, or a dangling reference, raises honestly.
    """
    if isinstance(evidence, str):
        return evidence
    if isinstance(evidence, dict):
        body = load_diff(evidence)
        if body is None:
            raise ValueError(
                "DIFF_UNAVAILABLE: referenced diff body is missing or corrupt"
            )
        return body
    raise ValueError("DIFF_UNAVAILABLE: unsupported diff evidence type")
