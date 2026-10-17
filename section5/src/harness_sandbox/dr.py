"""S4: backup / restore verification ("disaster-recovery drill") for file artifacts.

``backup_tree`` writes a tar.gz plus a manifest of per-file sha256 to a ``BackupTarget``.
``restore_and_verify`` restores into an EMPTY directory with traversal / link / special-file
refusal and re-hashes everything. ``drill`` runs both and times the restore (RTO evidence).
Secret files are excluded from backups. The object-store target is intentionally a stub that
reports UNSUPPORTED: a real drill needs real credentials, which are external validation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import time
from pathlib import Path
from typing import Protocol

from harness_sandbox.policy import is_secret_file


class DrError(Exception):
    pass


class BackupTarget(Protocol):
    def put(self, name: str, data: bytes) -> None: ...
    def get(self, name: str) -> bytes: ...


class LocalDirTarget:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _p(self, name: str) -> Path:
        if "/" in name or name.startswith("."):
            raise DrError("invalid backup name")
        return self.root / name

    def put(self, name: str, data: bytes) -> None:
        self._p(name).write_bytes(data)

    def get(self, name: str) -> bytes:
        return self._p(name).read_bytes()


class ObjectStoreTarget:
    def __init__(self, *_a, **_k) -> None:
        raise DrError("UNSUPPORTED: object-store drill needs real credentials (external)")


def backup_tree(src: Path, target: BackupTarget, label: str) -> dict:
    src = src.resolve()
    files: dict[str, str] = {}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, _d, names in os.walk(src):
            for n in sorted(names):
                p = Path(root) / n
                rel = p.relative_to(src).as_posix()
                if p.is_symlink() or is_secret_file(rel):
                    continue
                files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
                tar.add(p, arcname=rel, recursive=False)
    data = buf.getvalue()
    bid = f"{label}-{int(time.time())}"
    manifest = {"id": bid, "files": files, "archive_sha256": hashlib.sha256(data).hexdigest()}
    target.put(f"{bid}.tar.gz", data)
    target.put(f"{bid}.manifest.json", json.dumps(manifest).encode())
    return manifest


def restore_and_verify(target: BackupTarget, backup_id: str, dest: Path) -> dict:
    t0 = time.perf_counter()
    dest = dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise DrError("restore destination must be empty")
    dest.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(target.get(f"{backup_id}.manifest.json"))
    data = target.get(f"{backup_id}.tar.gz")
    problems: list[str] = []
    if hashlib.sha256(data).hexdigest() != manifest["archive_sha256"]:
        problems.append("archive hash mismatch")
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for m in tar.getmembers():
                out = (dest / m.name).resolve()
                if not m.isfile() or not out.is_relative_to(dest):
                    problems.append(f"refused member {m.name!r}")
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(tar.extractfile(m).read())  # type: ignore[union-attr]
        for rel, sha in manifest["files"].items():
            f = dest / rel
            if not f.exists():
                problems.append(f"missing {rel}")
            elif hashlib.sha256(f.read_bytes()).hexdigest() != sha:
                problems.append(f"hash mismatch {rel}")
    return {"ok": not problems, "problems": problems, "files": len(manifest["files"]),
            "restoreSeconds": round(time.perf_counter() - t0, 4)}


def drill(src: Path, target: BackupTarget, scratch: Path, label: str = "drill") -> dict:
    m = backup_tree(src, target, label)
    report = restore_and_verify(target, m["id"], scratch)
    return {"backupId": m["id"], **report}
