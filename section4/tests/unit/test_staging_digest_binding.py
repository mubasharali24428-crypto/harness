"""Approval digest must bind the full staged bytes, not just the (truncated) display diff."""

import pytest

from harness_orchestrator import staging


def _staged(tmp_path, old: bytes, new: bytes):
    ws = tmp_path / "run"
    ws.mkdir()
    (ws / "data").write_bytes(old)
    staging.ensure_baseline("run", str(ws), str(tmp_path))
    (ws / "data").write_bytes(new)
    group = staging.stage("run", str(ws), str(tmp_path)).groups[0]
    return ws, group


@pytest.mark.parametrize(
    ("old", "new", "altered"),
    [
        (b"\xffold", b"\xffreviewed", b"\xffUNREVIEWED"),  # binary: diff is a placeholder
        (b"old\n" * 60, b"new\n" * 60000 + b"reviewed\n", b"new\n" * 60000 + b"UNREVIEWED\n"),
    ],
    ids=["binary", "truncated_text"],
)
def test_replaced_withheld_bytes_change_digest_and_are_refused(tmp_path, old, new, altered):
    ws, group = _staged(tmp_path, old, new)
    (tmp_path / ".staged/run/files/data").write_bytes(altered)
    assert staging.pending_groups("run", str(tmp_path))[0].digest != group.digest
    with pytest.raises(staging.StagingError, match="no longer matches"):
        staging.apply_group("run", str(ws), str(tmp_path), group.action, group.paths, group.digest)
    assert (ws / "data").read_bytes() == old  # nothing landed


def test_untouched_staged_bytes_still_apply(tmp_path):
    ws, group = _staged(tmp_path, b"\xffold", b"\xffreviewed")
    assert staging.pending_groups("run", str(tmp_path))[0].digest == group.digest
    staging.apply_group("run", str(ws), str(tmp_path), group.action, group.paths, group.digest)
    assert (ws / "data").read_bytes() == b"\xffreviewed"


def test_bytes_swapped_after_the_check_are_not_the_bytes_that_land(tmp_path, monkeypatch):
    """The landed bytes come from the same snapshot the digest was checked on."""
    ws, group = _staged(tmp_path, b"\xffold", b"\xffreviewed")
    staged_file = tmp_path / ".staged/run/files/data"
    real = staging._groups

    def swap_after_check(*a, **k):
        out = real(*a, **k)
        staged_file.write_bytes(b"\xffUNREVIEWED")  # writer wins the race after the check
        return out

    monkeypatch.setattr(staging, "_groups", swap_after_check)
    staging.apply_group("run", str(ws), str(tmp_path), group.action, group.paths, group.digest)
    assert (ws / "data").read_bytes() == b"\xffreviewed"
