"""W8: the root launcher's ownership handover, and the ownership it must NOT take.

`confine._worker_user` refuses to run the worker as root, so it runs the container as
the run directory's owner — and for a root-owned directory as the image's own user
(10001), inside a root:root 0755 directory, where every `write` is answered
`EACCES … mkdir` and no container-mode run can produce a reviewable diff at all
(measured 2026-10-27 on the compose stack).

Taking *every* directory is the opposite failure, also measured the same day: the
orchestrator provisions these directories as its own uid and later stages, checkpoints
and diffs what the agent wrote, so re-owning them to the worker's uid makes the next
pass fail with `Permission denied` on the file the agent had just written. These tests
pin both halves without needing to be root.
"""

import os

from harness_orchestrator import confine


def test_a_non_root_launcher_has_nothing_to_hand_over(monkeypatch, tmp_path):
    monkeypatch.setattr(confine.os, "getuid", lambda: 1000)
    calls = []
    monkeypatch.setattr(confine.os, "lchown", lambda *a: calls.append(a))
    assert confine.claim_run_dirs_for_worker([str(tmp_path)]) == []
    assert calls == []


def test_a_root_owned_tree_is_handed_to_the_worker_uid(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    link = tmp_path / "link"
    link.symlink_to("src")  # lchown, not chown: a symlink target is not ours to take

    monkeypatch.setattr(confine.os, "getuid", lambda: 0)
    monkeypatch.setattr(confine, "_owner_uid", lambda path: 0)
    done = []
    monkeypatch.setattr(
        confine.os,
        "lchown",
        lambda path, uid, gid: done.append((os.path.basename(path), uid, gid)),
    )
    changed = confine.claim_run_dirs_for_worker([str(tmp_path)])
    assert len(changed) == len(done)
    assert len(changed) >= 4  # root, src, a.py, link
    assert {uid for _, uid, _ in done} == {confine.WORKER_UID}
    assert {gid for _, _, gid in done} == {confine.WORKER_GID}
    assert os.path.basename(changed[0]) == tmp_path.name


def test_a_directory_the_orchestrator_provisioned_is_left_alone(monkeypatch, tmp_path):
    """The regression that cost a run: the staging pass runs as the provisioning uid."""
    monkeypatch.setattr(confine.os, "getuid", lambda: 0)
    monkeypatch.setattr(confine, "_owner_uid", lambda path: 999)
    calls = []
    monkeypatch.setattr(confine.os, "lchown", lambda *a: calls.append(a))
    assert confine.claim_run_dirs_for_worker([str(tmp_path)]) == []
    assert calls == []


def test_a_denied_chown_stops_that_tree_and_not_the_next(monkeypatch, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()

    def refuse(path, uid, gid):
        raise PermissionError(1, "Operation not permitted", path)

    monkeypatch.setattr(confine.os, "getuid", lambda: 0)
    monkeypatch.setattr(confine, "_owner_uid", lambda path: 0)
    monkeypatch.setattr(confine.os, "lchown", refuse)
    # Not an exception out of the launcher: the worker's own EACCES is the honest
    # signal, and one unusable directory must not abandon the run's other dirs.
    assert confine.claim_run_dirs_for_worker([str(first), str(second)]) == []


def test_a_missing_directory_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(confine.os, "getuid", lambda: 0)
    monkeypatch.setattr(confine.os, "lchown", lambda *a: None)
    assert confine.claim_run_dirs_for_worker([str(tmp_path / "absent")]) == []


def test_the_worker_uid_fallback_matches_the_directory_the_launcher_leaves(monkeypatch, tmp_path):
    """`_worker_user` and `claim_run_dirs_for_worker` must agree, or the fix is a no-op."""
    monkeypatch.setattr(confine.os, "getuid", lambda: 0)

    class _St:
        st_uid = 0
        st_gid = 0

    monkeypatch.setattr(confine.os, "stat", lambda path: _St())
    assert confine._worker_user(str(tmp_path)) == f"{confine.WORKER_UID}:{confine.WORKER_GID}"
