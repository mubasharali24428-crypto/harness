#!/usr/bin/env python3
"""Container-free probe for ``scripts/e2e/w8_overlap_drill.py``.

Two runs that started from the same bytes both produce a result for the same
path. The claim under test is the apply step: the first result lands, the second
must refuse instead of silently overwriting it, and a refusal must write
nothing else either.

Runs with an interpreter that can import ``harness_sdk`` (the drill finds one);
prints one JSON object.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from harness_sdk.cli.local import ApplyConflict, apply_back


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def scenario_shared_file(base: Path) -> dict:
    """Run A and run B both edit ``shared.txt`` from the same base."""
    workspace = base / "shared"
    workspace.mkdir()
    (workspace / "shared.txt").write_text("base\n", encoding="utf-8")
    (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    out_a = base / "out-a"
    out_a.mkdir()
    (out_a / "shared.txt").write_text("from a\n", encoding="utf-8")
    (out_a / "keep.txt").write_text("keep a\n", encoding="utf-8")
    out_b = base / "out-b"
    out_b.mkdir()
    (out_b / "shared.txt").write_text("from b\n", encoding="utf-8")
    (out_b / "keep.txt").write_text("keep b\n", encoding="utf-8")
    expected = {"shared.txt": sha("base\n"), "keep.txt": sha("keep\n")}

    first = apply_back(workspace, out_a, ["shared.txt", "keep.txt"], [], expected)
    second: dict
    try:
        second = {"refused": False, **apply_back(
            workspace, out_b, ["shared.txt", "keep.txt"], [], expected)}
    except ApplyConflict as exc:
        second = {"refused": True, "conflicts": exc.conflicts,
                  "code": type(exc).__name__}

    third: dict
    try:
        third = {"refused": False, **apply_back(
            workspace, out_a, ["shared.txt", "keep.txt"], [], expected)}
    except ApplyConflict as exc:
        third = {"refused": True, "conflicts": exc.conflicts}

    return {
        "first": first,
        "second": second,
        "reapply": third,
        "finalShared": (workspace / "shared.txt").read_text(encoding="utf-8"),
        "finalKeep": (workspace / "keep.txt").read_text(encoding="utf-8"),
        "finalSharedSha": sha_file(workspace / "shared.txt"),
        "expected": expected,
    }


def scenario_new_file(base: Path) -> dict:
    """Both runs create the same new path; the first creation is the anchor."""
    workspace = base / "created"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
    out_a = base / "new-a"
    out_a.mkdir()
    (out_a / "created.txt").write_text("from a\n", encoding="utf-8")
    (out_a / "keep.txt").write_text("keep a\n", encoding="utf-8")
    out_b = base / "new-b"
    out_b.mkdir()
    (out_b / "created.txt").write_text("from b\n", encoding="utf-8")
    (out_b / "keep.txt").write_text("keep b\n", encoding="utf-8")
    expected = {"created.txt": None, "keep.txt": sha("keep\n")}

    first = apply_back(workspace, out_a, ["created.txt", "keep.txt"], [], expected)
    second: dict
    try:
        second = {"refused": False, **apply_back(
            workspace, out_b, ["created.txt", "keep.txt"], [], expected)}
    except ApplyConflict as exc:
        second = {"refused": True, "conflicts": exc.conflicts}
    return {
        "first": first,
        "second": second,
        "finalCreated": (workspace / "created.txt").read_text(encoding="utf-8"),
        "finalKeep": (workspace / "keep.txt").read_text(encoding="utf-8"),
    }


def scenario_stale_deletion(base: Path) -> dict:
    """The run deleted a file; the user recreated it before the apply."""
    workspace = base / "deleted"
    workspace.mkdir()
    (workspace / "gone.txt").write_text("edited since\n", encoding="utf-8")
    empty_out = base / "del-out"
    empty_out.mkdir()
    try:
        result = {"refused": False, **apply_back(
            workspace, empty_out, [], ["gone.txt"], {"gone.txt": sha("original\n")})}
    except ApplyConflict as exc:
        result = {"refused": True, "conflicts": exc.conflicts}
    return {
        "result": result,
        "stillThere": (workspace / "gone.txt").exists(),
        "text": (workspace / "gone.txt").read_text(encoding="utf-8"),
    }


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="w8-overlap-"))
    try:
        out = {
            "sharedFile": scenario_shared_file(base),
            "newFile": scenario_new_file(base),
            "staleDeletion": scenario_stale_deletion(base),
        }
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
