"""W8 slice 3 — the shared blob store under concurrent writers.

Blobs are deduplicated across runs on purpose (three runs with the same
`README.md` store one copy). That sharing is exactly what makes two properties
worth pinning: publishing a blob must be safe while another writer publishes the
SAME digest, and freeing a blob must not delete content a run is writing right
now. Both defects were found by auditing the concurrency claim rather than by a
failure: the first version shared one temp name, and `purge` left every blob
forever while its docstring blamed a reaper that did not exist.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from harness_orchestrator import run_checkpoints as cp


def _ws(tmp_path, name="run-1"):
    ws = tmp_path / "workspace" / name
    ws.mkdir(parents=True)
    (ws / "shared.txt").write_text("same bytes\n" * 500)
    return ws


def _blobs(store: Path) -> Path:
    blobs = store / cp.BLOBS_DIRNAME
    blobs.mkdir(parents=True, exist_ok=True)
    return blobs


def test_concurrent_publish_of_the_same_digest_never_leaves_a_partial_blob(tmp_path):
    """The race this replaces: two writers used one `<digest>.tmp` name, so the
    first `replace` could publish the other's half-written file under a digest
    that promises the full content."""
    store = cp.checkpoint_root(tmp_path / "workspace")
    blobs = _blobs(store)
    data = b"payload-" * 40_000  # ~400 KB: big enough that a partial write is visible
    digest = cp._sha(data)

    errors: list[BaseException] = []

    def publish():
        try:
            for _ in range(6):
                cp._publish_blob(blobs, digest, data)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=publish) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert (blobs / digest).read_bytes() == data
    assert not list(blobs.glob("*.tmp")), "a failed or finished writer leaves no temp file"


def test_a_blob_is_never_published_before_its_bytes_are_whole(tmp_path):
    """`replace` is the publish point; before it, nothing at the digest exists."""
    store = cp.checkpoint_root(tmp_path / "workspace")
    blobs = _blobs(store)
    data = b"complete-or-nothing\n"
    digest = cp._sha(data)

    cp._publish_blob(blobs, digest, data)

    assert (blobs / digest).is_file()
    assert len(list(blobs.glob(f"{digest}.*.tmp"))) == 0


def test_a_failed_write_removes_its_own_temp_and_keeps_the_store_clean(tmp_path, monkeypatch):
    blobs = _blobs(cp.checkpoint_root(tmp_path / "workspace"))
    real_replace = os.replace

    def boom(src, dst, *args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", boom)
    try:
        cp._publish_blob(blobs, "deadbeef", b"whatever")
    except OSError:
        pass
    monkeypatch.setattr(os, "replace", real_replace)

    assert not list(blobs.glob("*.tmp"))
    assert not (blobs / "deadbeef").exists()


def test_sweep_keeps_every_blob_a_manifest_still_references(tmp_path):
    store = cp.checkpoint_root(tmp_path / "workspace")
    ws = _ws(tmp_path, "run-keep")
    snap = cp.snapshot(ws, "run-keep", "before", root=store)
    referenced = set(snap["files"].values())
    blobs = store / cp.BLOBS_DIRNAME
    orphan = blobs / cp._sha(b"nobody points at me\n")
    orphan.write_bytes(b"nobody points at me\n")
    old = time.time() - 2 * cp.DEFAULT_BLOB_GRACE_SECONDS
    os.utime(orphan, (old, old))

    result = cp.sweep_blobs(tmp_path / "workspace", root=store)

    assert result["deleted"] == 1
    assert not orphan.exists()
    for digest in referenced:
        assert (blobs / digest).exists(), "a referenced blob is never collectable"


def test_sweep_spares_a_young_unreferenced_blob_because_a_snapshot_may_be_in_flight(tmp_path):
    """A snapshot writes its blobs BEFORE the manifest that references them, so
    "unreferenced right now" is not yet evidence of "orphaned"."""
    store = cp.checkpoint_root(tmp_path / "workspace")
    blobs = _blobs(store)
    in_flight = blobs / cp._sha(b"being written now\n")
    in_flight.write_bytes(b"being written now\n")

    result = cp.sweep_blobs(tmp_path / "workspace", root=store)

    assert result["keptRecent"] == 1
    assert in_flight.exists()


def test_sweep_collects_abandoned_temp_files_only_after_the_grace_window(tmp_path):
    store = cp.checkpoint_root(tmp_path / "workspace")
    blobs = _blobs(store)
    fresh = blobs / ("a" * 64 + ".1.tmp")
    stale = blobs / ("b" * 64 + ".2.tmp")
    fresh.write_bytes(b"")
    stale.write_bytes(b"")
    old = time.time() - 2 * cp.DEFAULT_BLOB_GRACE_SECONDS
    os.utime(stale, (old, old))

    cp.sweep_blobs(tmp_path / "workspace", root=store)

    assert fresh.exists() and not stale.exists()


def test_an_unreadable_manifest_frees_nothing(tmp_path):
    """Deleting content because a manifest could not be parsed is the wrong
    direction of failure: the sweep keeps everything it cannot prove."""
    store = cp.checkpoint_root(tmp_path / "workspace")
    run_dir = store / "run-broken"
    run_dir.mkdir(parents=True)
    (run_dir / "0001-before.json").write_text("{not json")
    blobs = _blobs(store)
    blob = blobs / ("c" * 64)
    blob.write_bytes(b"kept because the manifest is unreadable\n")
    old = time.time() - 2 * cp.DEFAULT_BLOB_GRACE_SECONDS
    os.utime(blob, (old, old))

    result = cp.sweep_blobs(tmp_path / "workspace", root=store)

    assert result["deleted"] == 0
    assert blob.exists()


def test_purging_a_run_and_then_sweeping_frees_its_unique_blobs(tmp_path):
    store = cp.checkpoint_root(tmp_path / "workspace")
    ws = tmp_path / "workspace" / "run-doomed"
    ws.mkdir(parents=True)
    (ws / "only.txt").write_text("unique content\n")
    snap = cp.snapshot(ws, "run-doomed", "before", root=store)
    unique = cp._sha(b"unique content\n")
    assert unique in set(snap["files"].values())

    cp.purge(tmp_path / "workspace", "run-doomed")
    old = time.time() - 2 * cp.DEFAULT_BLOB_GRACE_SECONDS
    for entry in (store / cp.BLOBS_DIRNAME).iterdir():
        os.utime(entry, (old, old))
    result = cp.sweep_blobs(tmp_path / "workspace", root=store)

    assert not (store / cp.BLOBS_DIRNAME / unique).exists()
    assert result["deleted"] >= 1
    assert json.dumps(result)  # evidence-shaped, not a bare count
