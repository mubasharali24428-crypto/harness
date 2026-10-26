"""W3: checkpoints for a RUN's own workspace, so "rewind" has something to revert to.

Section 5 owns checkpointing for sandbox task workspaces and exposes it over HTTP
(``POST /api/v1/sandbox/{projectId}/checkpoints/{sha}/restore``). A run does not
execute there: it gets its own directory on the ``harness_runs`` volume, and
nothing snapshots it — so the resume caveat's old advice to "review the workspace
checkpoints" named a control that did not exist.

Two ways to close that, and why this one:

* Give the sandbox the ``harness_runs`` volume and reuse its git checkpoints.
  Rejected: one run's sandbox could then read every other run's workspace, which
  is a tenant-isolation regression bought for convenience.
* Snapshot locally, in the orchestrator, on the volume both it and the worker
  already mount. Chosen.

Git was the obvious local tool and is **not available**: the orchestrator image
has no ``git`` binary (verified in the container). Rather than add a build
dependency for this, a checkpoint here is a manifest plus content-addressed file
blobs:

    <root>/<runId>/<seq>-<label>.json     {"files": {"rel": "<sha256>", ...}, ...}
    <root>/blobs/<sha256>                 the bytes, deduplicated across snapshots

``root`` is a sibling of the workspace directories (``.checkpoints`` under
``DSH_WORKSPACE``), never inside a run's own tree: the agent must not be able to
edit or read its own checkpoints as ordinary files.

Rewind restores the snapshot exactly — files written back, files created after
the snapshot removed — and reports both, because "revert" that silently keeps
stray files is not a revert. Noise directories (``.git``, ``node_modules``,
virtualenvs, caches) are not captured; a tree that exceeds the caps raises
instead of writing a snapshot that would restore partially.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

CHECKPOINT_DIRNAME = ".checkpoints"
ROOT_ENV = "HARNESS_CHECKPOINT_ROOT"

MAX_FILES = 5000
MAX_BYTES = 64 * 1024 * 1024
SKIP_DIRS = frozenset(
    {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".run-tmp",
     ".mypy_cache", ".pytest_cache", ".ruff_cache", CHECKPOINT_DIRNAME}
)
# No spaces: the label becomes part of the checkpoint id, and the id is what
# `rewind` validates. "before attempt 1" as an id is rejected by that check.
_LABEL_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class CheckpointError(Exception):
    """A snapshot or restore that cannot be honoured as asked."""


class WorkspaceTooLarge(CheckpointError):
    """The tree exceeded the caps: no snapshot was written (partial is not offered)."""


def checkpoint_root(workspace_root: str | Path) -> Path:
    """Where checkpoints live for all runs under ``workspace_root``."""
    override = str(os.environ.get(ROOT_ENV, "")).strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(workspace_root).resolve() / CHECKPOINT_DIRNAME


def _clean_label(label: str) -> str:
    return _LABEL_RE.sub("-", label.strip())[:60] or "snapshot"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_dir(root: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", run_id or ""):
        raise CheckpointError("invalid run id for a checkpoint store")
    return root / run_id


def snapshot(workspace: str | Path, run_id: str, label: str = "snapshot",
             *, root: Path | None = None) -> dict[str, Any]:
    """Capture the workspace as a manifest; return it with its id.

    Raises :class:`WorkspaceTooLarge` rather than writing a manifest that would
    restore a partial tree — a checkpoint that quietly misses files is worse than
    no checkpoint, because rewind would then delete real work.
    """
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        raise CheckpointError(f"workspace is not a directory: {ws}")
    store = _run_dir(root or checkpoint_root(ws.parent), run_id)
    blobs = (root or checkpoint_root(ws.parent)) / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}
    dirs: list[str] = []
    total = 0
    for current, dirnames, filenames in os.walk(ws):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(current, ws)
        if rel_dir != ".":
            dirs.append(rel_dir)
        for name in sorted(filenames):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue  # a symlink is not content; restoring one would recreate the link
            data = path.read_bytes()
            total += len(data)
            if len(files) + 1 > MAX_FILES or total > MAX_BYTES:
                raise WorkspaceTooLarge(
                    f"workspace exceeds the checkpoint caps at {path.relative_to(ws)}"
                )
            digest = _sha(data)
            blob = blobs / digest
            if not blob.exists():
                tmp = blob.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(blob)  # atomic publish: a reader never sees a half blob
            files[path.relative_to(ws).as_posix()] = digest

    store.mkdir(parents=True, exist_ok=True)
    seq = len(list(store.glob("*.json"))) + 1
    manifest = {
        "runId": run_id,
        "seq": seq,
        "label": _clean_label(label),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": files,
        "dirs": sorted(dirs),
        "bytes": total,
    }
    target = store / f"{seq:04d}-{manifest['label']}.json"
    target.write_text(json.dumps(manifest, indent=1) + "\n")
    return {**manifest, "id": target.stem}


def list_snapshots(workspace: str | Path, run_id: str, *,
                   root: Path | None = None) -> list[dict[str, Any]]:
    """Newest last. An absent store is an empty list, not an error."""
    store = _run_dir(root or checkpoint_root(Path(workspace).resolve().parent), run_id)
    if not store.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(store.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        out.append({
            "id": path.stem,
            "seq": data.get("seq"),
            "label": data.get("label"),
            "at": data.get("at"),
            "files": len(data.get("files") or {}),
            "bytes": data.get("bytes"),
        })
    return out


def rewind(workspace: str | Path, run_id: str, checkpoint_id: str | None = None, *,
           root: Path | None = None) -> dict[str, Any]:
    """Restore the workspace to a snapshot, exactly.

    ``checkpoint_id`` defaults to the newest snapshot. Files are written back and
    files that did not exist at the snapshot are REMOVED — that is what reverting
    means; a rewind that leaves stray files behind would let a later resume read a
    tree that never existed at any checkpoint. Removal never follows symlinks and
    never leaves the workspace.
    """
    ws = Path(workspace).resolve()
    store_root = root or checkpoint_root(ws.parent)
    store = _run_dir(store_root, run_id)
    blobs = store_root / "blobs"
    if not store.is_dir():
        raise CheckpointError("this run has no checkpoints")
    if checkpoint_id is None:
        manifests = sorted(store.glob("*.json"))
        if not manifests:
            raise CheckpointError("this run has no checkpoints")
        target = manifests[-1]
    else:
        if not re.fullmatch(r"[0-9A-Za-z_.-]{1,120}", checkpoint_id):
            raise CheckpointError("invalid checkpoint id")
        target = store / f"{checkpoint_id}.json"
    if not target.is_file():
        raise CheckpointError(f"checkpoint not found: {checkpoint_id}")
    manifest = json.loads(target.read_text())
    wanted: dict[str, str] = manifest.get("files") or {}

    restored = 0
    for rel, digest in wanted.items():
        blob = blobs / digest
        if not blob.is_file():
            raise CheckpointError(f"missing blob for {rel}: the store is incomplete")
        dst = (ws / rel).resolve()
        if not str(dst).startswith(str(ws) + os.sep):
            raise CheckpointError(f"manifest entry escapes the workspace: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        current = dst.read_bytes() if dst.is_file() else None
        if current != blob.read_bytes():
            dst.write_bytes(blob.read_bytes())
            restored += 1

    for rel_dir in manifest.get("dirs") or []:
        (ws / rel_dir).mkdir(parents=True, exist_ok=True)

    removed: list[str] = []
    for current, dirnames, filenames in os.walk(ws, topdown=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(current) / name
            rel = path.relative_to(ws).as_posix()
            if rel in wanted or path.is_symlink():
                continue  # symlinks are never captured, so never removed
            path.unlink()
            removed.append(rel)
        if current != str(ws) and not os.listdir(current):
            try:
                os.rmdir(current)
            except OSError:
                pass  # not empty after all; leave it

    return {
        "runId": run_id,
        "rewoundTo": target.stem,
        "label": manifest.get("label"),
        "restoredFiles": restored,
        "removedFiles": sorted(removed)[:200],
        "removedCount": len(removed),
    }


def purge(workspace_root: str | Path, run_id: str) -> None:
    """Drop a run's manifests. Blobs are shared and left for the next reaper."""
    store = _run_dir(checkpoint_root(workspace_root), run_id)
    if store.is_dir():
        shutil.rmtree(store, ignore_errors=True)
