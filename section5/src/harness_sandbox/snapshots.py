"""S3: full-workspace environment snapshots with safe restore.

A snapshot is a content copy of the task workspace (including untracked files and
.git), minus secret files and forbidden paths, plus a manifest of sha256 per file.
Restore never loses work silently:
* it refuses when unsaved changes exist (vs the newest snapshot) unless
  ``discard_dirty=True``;
* it ALWAYS takes a ``pre-restore`` safety snapshot first, so even a discarding
  restore is reversible;
* ``paths=[...]`` restores only those paths and leaves unrelated edits alone;
* forbidden / secret paths are never written or deleted by restore.
Storage is namespaced <root>/<team>/<task>/<snapshot>; another team's id is "not found".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from harness_sandbox.policy import SecurityPolicyViolationError, is_secret_file, validate_path_edit

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SnapshotError(Exception):
    code = "SNAPSHOT_ERROR"


class SnapshotNotFoundError(SnapshotError):
    code = "SNAPSHOT_NOT_FOUND"


class SnapshotQuotaError(SnapshotError):
    code = "SNAPSHOT_QUOTA_EXCEEDED"


class DirtyWorkspaceError(SnapshotError):
    code = "WORKSPACE_DIRTY"

    def __init__(self, paths: list[str]) -> None:
        super().__init__(
            f"{len(paths)} unsaved change(s); pass discard_dirty or restore specific paths"
        )
        self.paths = paths


@dataclass
class RestoreResult:
    restored: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped_protected: list[str] = field(default_factory=list)
    safety_snapshot: str = ""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class EnvironmentSnapshots:
    def __init__(self, root: Path, max_per_task: int = 20, max_team_bytes: int = 1 << 30) -> None:
        self.root = root.resolve()
        self.max_per_task = max_per_task
        self.max_team_bytes = max_team_bytes

    # -- helpers ---------------------------------------------------------------
    def _dir(self, team: str, task: str) -> Path:
        for part in (team, task):
            if not _ID.match(part):
                raise SnapshotError(f"invalid identifier '{part}'")
        return self.root / team / task

    @staticmethod
    def _protected(rel: str, forbidden: list[str]) -> bool:
        if is_secret_file(rel):
            return True
        try:
            validate_path_edit(rel, forbidden)
        except SecurityPolicyViolationError:
            return True
        return False

    def _scan(self, workspace: Path, forbidden: list[str]) -> dict[str, dict]:
        entries: dict[str, dict] = {}
        for root, dirs, files in os.walk(workspace):
            dirs.sort()
            for name in sorted(files):
                full = Path(root) / name
                rel = full.relative_to(workspace).as_posix()
                if self._protected(rel, forbidden):
                    continue
                if full.is_symlink():
                    entries[rel] = {"type": "link", "target": os.readlink(full)}
                else:
                    entries[rel] = {"type": "file", "sha": _sha(full),
                                    "mode": full.stat().st_mode & 0o777}
        return entries

    def _manifest(self, team: str, task: str, snap: str) -> dict:
        if not _ID.match(snap):
            raise SnapshotNotFoundError(snap)
        path = self._dir(team, task) / snap / "manifest.json"
        if not path.exists():
            raise SnapshotNotFoundError(snap)
        return json.loads(path.read_text())

    # -- api -------------------------------------------------------------------
    def list(self, team: str, task: str) -> list[dict]:
        base = self._dir(team, task)
        if not base.exists():
            return []
        items = [json.loads((d / "manifest.json").read_text()) for d in base.iterdir()
                 if (d / "manifest.json").exists()]
        return sorted(items, key=lambda m: m["created_at"], reverse=True)

    def create(self, team: str, task: str, workspace: Path, label: str = "",
               forbidden_paths: list[str] | None = None) -> dict:
        forbidden = forbidden_paths or []
        workspace = workspace.resolve()
        base = self._dir(team, task)
        entries = self._scan(workspace, forbidden)
        size = sum(workspace.joinpath(r).stat().st_size for r, e in entries.items()
                   if e["type"] == "file")
        if self._team_bytes(team) + size > self.max_team_bytes:
            raise SnapshotQuotaError(
                f"team snapshot storage would exceed {self.max_team_bytes} bytes"
            )
        snap = f"snap-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        dest = base / snap
        (dest / "files").mkdir(parents=True, mode=0o700)
        prev = self.list(team, task)
        for rel, e in entries.items():
            if e["type"] != "file":
                continue
            out = dest / "files" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace / rel, out)
        manifest = {"id": snap, "label": label, "created_at": time.time(), "bytes": size,
                    "entries": entries, "forbidden": forbidden, "taskId": task, "teamId": team}
        (dest / "manifest.json").write_text(json.dumps(manifest))
        for old in prev[self.max_per_task - 1:]:  # retention: keep newest N including this one
            shutil.rmtree(base / old["id"], ignore_errors=True)
        return {k: v for k, v in manifest.items() if k != "entries"} | {"files": len(entries)}

    def _safety_snapshot(self, team: str, task: str, workspace: Path, forbidden: list[str],
                         keep: str) -> str:
        """Take the mandatory pre-restore snapshot, evicting this task's OLDEST snapshots (never
        the one being restored) if the team quota would otherwise block recovery itself."""
        try:
            return self.create(team, task, workspace, "pre-restore", forbidden)["id"]
        except SnapshotQuotaError:
            for old in reversed(self.list(team, task)):  # oldest first
                if old["id"] == keep:
                    continue
                shutil.rmtree(self._dir(team, task) / old["id"], ignore_errors=True)
                try:
                    return self.create(team, task, workspace, "pre-restore", forbidden)["id"]
                except SnapshotQuotaError:
                    continue
            raise

    def _team_bytes(self, team: str) -> int:
        total = 0
        tdir = self.root / team
        if tdir.exists():
            for m in tdir.glob("*/*/manifest.json"):
                total += json.loads(m.read_text())["bytes"]
        return total

    def unsaved_changes(self, team: str, task: str, workspace: Path,
                        forbidden_paths: list[str] | None = None) -> list[str]:
        """Paths differing from the newest snapshot (empty list if there is no snapshot)."""
        snaps = self.list(team, task)
        if not snaps:
            return []
        newest = self._manifest(team, task, snaps[0]["id"])
        base = newest["entries"]
        forbidden = newest["forbidden"] if forbidden_paths is None else forbidden_paths
        now = self._scan(workspace.resolve(), forbidden)
        return sorted(p for p in set(base) | set(now) if base.get(p) != now.get(p))

    def restore(self, team: str, task: str, snapshot_id: str, workspace: Path, *,
                paths: list[str] | None = None, discard_dirty: bool = False,
                forbidden_paths: list[str] | None = None) -> RestoreResult:
        manifest = self._manifest(team, task, snapshot_id)
        workspace = workspace.resolve()
        forbidden = manifest["forbidden"] if forbidden_paths is None else forbidden_paths
        scope = None
        if paths is not None:
            scope = {p.strip("/") for p in paths}
            if any(".." in Path(p).parts or Path(p).is_absolute() for p in paths):
                raise SecurityPolicyViolationError("path traversal in restore paths")

        def in_scope(rel: str) -> bool:
            return scope is None or any(rel == s or rel.startswith(s + "/") for s in scope)

        dirty = [p for p in self.unsaved_changes(team, task, workspace, forbidden) if in_scope(p)]
        if dirty and not discard_dirty:
            raise DirtyWorkspaceError(dirty)

        result = RestoreResult()
        result.safety_snapshot = self._safety_snapshot(
            team, task, workspace, forbidden, keep=snapshot_id
        )
        entries: dict[str, dict] = manifest["entries"]
        src_root = self._dir(team, task) / snapshot_id / "files"

        for rel, e in entries.items():
            if not in_scope(rel):
                continue
            if self._protected(rel, forbidden):
                result.skipped_protected.append(rel)
                continue
            target = workspace / rel
            # The PARENT must resolve inside the workspace: an agent can swap a
            # directory for a symlink, and a missing leaf must not hide the escape.
            if not target.parent.resolve().is_relative_to(workspace):
                result.skipped_protected.append(rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if e["type"] == "link":
                dest = (target.parent / e["target"]).resolve()
                if os.path.isabs(e["target"]) or not dest.is_relative_to(workspace):
                    result.skipped_protected.append(rel)
                    continue
                tmp = target.with_name(target.name + ".restoring")
                tmp.unlink(missing_ok=True)
                os.symlink(e["target"], tmp)
                os.replace(tmp, target)
            else:
                tmp = target.with_name(target.name + ".restoring")
                shutil.copy2(src_root / rel, tmp)
                os.chmod(tmp, e["mode"])
                os.replace(tmp, target)
            result.restored.append(rel)

        # Files present now but absent from the snapshot go (in scope, never protected).
        for root, _dirs, files in os.walk(workspace):
            for name in files:
                rel = (Path(root) / name).relative_to(workspace).as_posix()
                if rel in entries or not in_scope(rel) or self._protected(rel, forbidden):
                    continue
                (Path(root) / name).unlink()
                result.deleted.append(rel)
        return result
