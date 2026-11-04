"""W3 — checkpoints for a run's own workspace.

The store is content-addressed files, not git: the orchestrator image has no
`git` binary (checked in the container), and adding one to the image to buy this
feature is a build dependency the feature does not need.

These tests pin the two properties that make "rewind" mean something rather than
merely not crashing: a snapshot captures what is on disk at that moment, and a
rewind puts the tree back to exactly that — including REMOVING files that did not
exist then, because a revert that leaves stray files lets a later resume read a
tree no checkpoint ever contained.
"""

from __future__ import annotations

import pytest

from harness_orchestrator import run_checkpoints as cp


def _ws(tmp_path):
    ws = tmp_path / "workspace" / "run-1"
    ws.mkdir(parents=True)
    (ws / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (ws / "README.md").write_text("fixture\n")
    return ws


def test_a_snapshot_records_the_files_that_existed(tmp_path):
    ws = _ws(tmp_path)
    snap = cp.snapshot(ws, "run-1", "before attempt 1")

    assert snap["seq"] == 1
    assert snap["label"] == "before-attempt-1"
    assert set(snap["files"]) == {"calc.py", "README.md"}
    assert snap["id"].startswith("0001-")


def test_rewind_restores_edited_content(tmp_path):
    ws = _ws(tmp_path)
    original = (ws / "calc.py").read_text()
    cp.snapshot(ws, "run-1", "before")
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")

    out = cp.rewind(ws, "run-1")

    assert (ws / "calc.py").read_text() == original
    assert out["restoredFiles"] == 1
    assert out["removedCount"] == 0


def test_rewind_removes_files_created_after_the_snapshot(tmp_path):
    """The property a "restore only what you know" implementation would miss."""
    ws = _ws(tmp_path)
    cp.snapshot(ws, "run-1", "before")
    (ws / "scratch.py").write_text("stray\n")
    (ws / "pkg").mkdir()
    (ws / "pkg" / "deep.py").write_text("deeper\n")

    out = cp.rewind(ws, "run-1")

    assert not (ws / "scratch.py").exists()
    assert not (ws / "pkg" / "deep.py").exists()
    assert out["removedFiles"] == ["pkg/deep.py", "scratch.py"]
    assert out["removedCount"] == 2


def test_a_second_snapshot_is_a_second_checkpoint(tmp_path):
    ws = _ws(tmp_path)
    cp.snapshot(ws, "run-1", "before attempt 1")
    (ws / "calc.py").write_text("fixed\n")
    cp.snapshot(ws, "run-1", "after attempt 1")

    items = cp.list_snapshots(ws, "run-1")

    assert [i["seq"] for i in items] == [1, 2]
    assert [i["label"] for i in items] == ["before-attempt-1", "after-attempt-1"]


def test_rewind_can_target_an_older_checkpoint(tmp_path):
    ws = _ws(tmp_path)
    first = cp.snapshot(ws, "run-1", "before")
    (ws / "calc.py").write_text("middle\n")
    cp.snapshot(ws, "run-1", "after")
    (ws / "calc.py").write_text("latest\n")

    out = cp.rewind(ws, "run-1", first["id"])

    assert (ws / "calc.py").read_text().endswith("a - b\n")
    assert out["rewoundTo"] == first["id"]


def test_a_symlink_is_never_captured_or_restored(tmp_path):
    ws = _ws(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    (ws / "link.txt").symlink_to(outside)

    snap = cp.snapshot(ws, "run-1", "with link")
    assert "link.txt" not in snap["files"]

    (ws / "link.txt").unlink()
    cp.rewind(ws, "run-1")
    assert not (ws / "link.txt").exists(), "a rewind must not recreate a link it never stored"


def test_a_tree_over_the_caps_refuses_instead_of_snapshotting_part_of_it(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    monkeypatch.setattr(cp, "MAX_FILES", 1)

    with pytest.raises(cp.WorkspaceTooLarge):
        cp.snapshot(ws, "run-1", "too big")


def test_listing_a_run_with_no_checkpoints_is_empty_not_an_error(tmp_path):
    ws = _ws(tmp_path)
    assert cp.list_snapshots(ws, "run-1") == []


def test_rewinding_a_run_with_no_checkpoints_says_so(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(cp.CheckpointError, match="no checkpoints"):
        cp.rewind(ws, "run-1")


def test_a_run_id_that_could_escape_the_store_is_rejected(tmp_path):
    ws = _ws(tmp_path)
    for bad in ("../other", "a/b", "", ".hidden"):
        with pytest.raises(cp.CheckpointError):
            cp.snapshot(ws, bad, "x")


def test_checkpoints_live_outside_the_run_workspace(tmp_path):
    """The agent must not be able to read or edit its own checkpoints as files."""
    ws = _ws(tmp_path)
    snap = cp.snapshot(ws, "run-1", "before")

    assert snap  # sanity
    assert not any(".checkpoints" in p.name for p in ws.iterdir())
    store = cp.checkpoint_root(ws.parent) / "run-1"
    assert store.is_dir()
