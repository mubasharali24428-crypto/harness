"""S3: snapshot/restore safety and the tenant-segregated warm environment cache."""
import os
import subprocess
import time
from pathlib import Path

import pytest

from harness_sandbox.environments import EnvironmentManager, EnvironmentSpec
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import SecurityPolicyViolationError
from harness_sandbox.snapshots import (
    DirtyWorkspaceError,
    EnvironmentSnapshots,
    SnapshotNotFoundError,
    SnapshotQuotaError,
)

# ------------------------------- snapshots -------------------------------------------


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "src").mkdir(parents=True)
    (w / "src/a.py").write_text("a1")
    (w / "src/b.py").write_text("b1")
    (w / ".env").write_text("TOKEN=x")
    (w / "infra").mkdir()
    (w / "infra/prod.tf").write_text("prod1")
    return w


@pytest.fixture
def snaps(tmp_path):
    return EnvironmentSnapshots(tmp_path / "snaps")


def test_snapshot_excludes_secrets_and_forbidden(snaps, ws):
    info = snaps.create("t1", "task", ws, "base", forbidden_paths=["infra"])
    stored = {p.relative_to(snaps.root).parts[-1] for p in (snaps.root / "t1/task").rglob("*")
              if p.is_file()}
    assert ".env" not in stored and "prod.tf" not in stored
    assert info["files"] == 2


def test_restore_refuses_dirty_then_is_reversible(snaps, ws):
    base = snaps.create("t1", "task", ws, "base")["id"]
    (ws / "src/a.py").write_text("a2-unsaved")
    with pytest.raises(DirtyWorkspaceError) as e:
        snaps.restore("t1", "task", base, ws)
    assert e.value.paths == ["src/a.py"]
    assert (ws / "src/a.py").read_text() == "a2-unsaved"  # untouched on refusal

    res = snaps.restore("t1", "task", base, ws, discard_dirty=True)
    assert (ws / "src/a.py").read_text() == "a1"
    # the discarded work is recoverable from the automatic safety snapshot
    snaps.restore("t1", "task", res.safety_snapshot, ws, discard_dirty=True)
    assert (ws / "src/a.py").read_text() == "a2-unsaved"


def test_partial_restore_keeps_unrelated_edits(snaps, ws):
    base = snaps.create("t1", "task", ws, "base")["id"]
    (ws / "src/a.py").write_text("a-bad")
    (ws / "src/b.py").write_text("b-GOOD-WORK")
    (ws / "src/new.py").write_text("new")
    res = snaps.restore("t1", "task", base, ws, paths=["src/a.py"], discard_dirty=True)
    assert (ws / "src/a.py").read_text() == "a1"
    assert (ws / "src/b.py").read_text() == "b-GOOD-WORK"   # unrelated edit survives
    assert (ws / "src/new.py").exists()
    assert res.restored == ["src/a.py"] and res.deleted == []


def test_full_restore_deletes_added_files_but_never_secrets_or_forbidden(snaps, ws):
    base = snaps.create("t1", "task", ws, "base", forbidden_paths=["infra"])["id"]
    (ws / "extra.py").write_text("x")
    (ws / "infra/prod.tf").write_text("prod-changed-by-human")
    (ws / ".env").write_text("TOKEN=rotated")
    snaps.restore("t1", "task", base, ws, discard_dirty=True)
    assert not (ws / "extra.py").exists()
    assert (ws / "infra/prod.tf").read_text() == "prod-changed-by-human"
    assert (ws / ".env").read_text() == "TOKEN=rotated"


def test_restore_rejects_traversal_foreign_team_and_bad_ids(snaps, ws):
    base = snaps.create("t1", "task", ws)["id"]
    with pytest.raises(SecurityPolicyViolationError):
        snaps.restore("t1", "task", base, ws, paths=["../etc"], discard_dirty=True)
    with pytest.raises(SnapshotNotFoundError):
        snaps.restore("team-2", "task", base, ws)            # other tenant: not found
    with pytest.raises(SnapshotNotFoundError):
        snaps.restore("t1", "task", "../../x", ws)
    assert snaps.list("team-2", "task") == []


def test_restore_never_follows_escaping_symlink(snaps, ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("sensitive")
    os.symlink(str(outside), ws / "link")                     # absolute escape
    base = snaps.create("t1", "task", ws)["id"]
    (ws / "link").unlink()
    res = snaps.restore("t1", "task", base, ws, discard_dirty=True)
    assert "link" in res.skipped_protected and not (ws / "link").exists()
    assert outside.read_text() == "sensitive"


def test_snapshot_retention_and_team_quota(tmp_path, ws):
    s = EnvironmentSnapshots(tmp_path / "s", max_per_task=3)
    for i in range(6):
        (ws / "src/a.py").write_text(f"v{i}")
        s.create("t1", "task", ws, f"s{i}")
    assert len(s.list("t1", "task")) == 3
    tiny = EnvironmentSnapshots(tmp_path / "q", max_team_bytes=3)
    with pytest.raises(SnapshotQuotaError):
        tiny.create("t1", "task", ws)

def test_restore_at_team_quota_evicts_oldest_instead_of_blocking_recovery(tmp_path, ws):
    s = EnvironmentSnapshots(tmp_path / "q", max_team_bytes=24)  # clean snapshot 9 B, dirty 13 B
    old = s.create("t1", "task", ws, "old")["id"]
    (ws / "src/a.py").write_text("a2")
    keep = s.create("t1", "task", ws, "keep")["id"]
    with pytest.raises(SnapshotQuotaError):  # the team really is full
        s.create("t1", "task", ws, "third")
    (ws / "src/a.py").write_text("broken")
    res = s.restore("t1", "task", keep, ws, discard_dirty=True)
    assert (ws / "src/a.py").read_text() == "a2" and res.safety_snapshot
    ids = {m["id"] for m in s.list("t1", "task")}
    assert old not in ids and keep in ids and res.safety_snapshot in ids


def test_restore_still_fails_when_eviction_cannot_make_room(tmp_path, ws):
    s = EnvironmentSnapshots(tmp_path / "q2", max_team_bytes=10)
    keep = s.create("t1", "task", ws, "only")["id"]
    (ws / "src/a.py").write_text("changed")
    with pytest.raises(SnapshotQuotaError):
        s.restore("t1", "task", keep, ws, discard_dirty=True)
    assert s.list("t1", "task")[0]["id"] == keep  # the target snapshot is never evicted


# ------------------------------- environments ----------------------------------------


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    env = {**os.environ, **ident}
    run = lambda *a: subprocess.run(["git", *a], cwd=path, env=env, check=True,  # noqa: E731
                                    capture_output=True)
    run("init", "-q", "-b", "main")
    (path / "requirements.lock").write_text("libfoo==1.0\n")
    (path / "app.py").write_text("print('hi')\n")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return path


def provisioner(layer: Path, repo: Path) -> None:
    """Stand-in for an operator-registered dependency build: 300 files + real work."""
    time.sleep(0.4)
    for i in range(300):
        d = layer / "deps" / f"pkg{i % 10}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"m{i}.py").write_text(f"X={i}\n" * 20)


@pytest.fixture
def mgr(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    return EnvironmentManager(tmp_path / "cache", runner, {"deps": provisioner}), runner


def test_cold_then_warm_and_measured_gain(mgr, tmp_path):
    m, runner = mgr
    repo = _repo(tmp_path / "repo")
    spec = EnvironmentSpec(str(repo), "main", ("requirements.lock",), "deps")
    cold = m.create("t1", "e1", spec)
    warm = m.create("t1", "e2", spec)
    assert (cold.cache, warm.cache) == ("miss", "hit")
    assert cold.cache_key == warm.cache_key
    ws = runner.active_tasks["e2"]["workspace"]
    assert (ws / "app.py").exists() and (ws / "deps/pkg3").is_dir()
    assert warm.timings_ms["total"] < cold.timings_ms["total"]
    print(f"\nS3-BENCH cold_ms={cold.timings_ms['total']:.0f}"
          f" warm_ms={warm.timings_ms['total']:.0f}")
    # writing in a warm workspace must not poison the shared cache
    (ws / "deps/pkg3/m3.py").write_text("POISON")
    again = m.create("t1", "e3", spec)
    assert again.cache == "hit"
    assert "POISON" not in (runner.active_tasks["e3"]["workspace"] / "deps/pkg3/m3.py").read_text()


def test_cache_is_tenant_segregated_and_lockfile_invalidates(mgr, tmp_path):
    m, runner = mgr
    repo = _repo(tmp_path / "repo")
    spec = EnvironmentSpec(str(repo), "main", ("requirements.lock",), "deps")
    a = m.create("team-a", "a1", spec)
    b = m.create("team-b", "b1", spec)
    assert b.cache == "miss" and a.cache_key != b.cache_key        # same spec, other tenant
    (repo / "requirements.lock").write_text("libfoo==2.0\n")
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qam", "bump"], check=True)
    assert m.create("team-a", "a2", spec).cache == "miss"          # lockfile change => new key
    assert m.invalidate("team-a") == 2
    assert m.create("team-a", "a3", spec).cache == "miss"


def test_tampered_cache_entry_is_rejected_and_rebuilt(mgr, tmp_path):
    m, runner = mgr
    repo = _repo(tmp_path / "repo")
    spec = EnvironmentSpec(str(repo), "main", ("requirements.lock",), "deps")
    first = m.create("t1", "x1", spec)
    layer_file = tmp_path / "cache/t1" / first.cache_key / "layer/deps/pkg0/m0.py"
    layer_file.write_text("MALICIOUS")
    # integrity check failed -> rebuilt
    assert m.create("t1", "x2", spec).cache == "miss"
    assert "MALICIOUS" not in (
        runner.active_tasks["x2"]["workspace"] / "deps/pkg0/m0.py").read_text()


def test_unknown_provisioner_and_secret_layer_refused(mgr, tmp_path):
    m, runner = mgr
    repo = _repo(tmp_path / "repo")
    with pytest.raises(SecurityPolicyViolationError):
        m.create("t1", "u1", EnvironmentSpec(str(repo), "main", (), "curl-bash"))
    m.provisioners["leaky"] = lambda layer, r: (layer / ".env").write_text("TOKEN=1")
    with pytest.raises(SecurityPolicyViolationError, match="secret"):
        m.create("t1", "u2", EnvironmentSpec(str(repo), "main", (), "leaky"))
    assert "u2" not in runner.active_tasks                          # failed create leaves nothing


def test_restore_never_writes_through_a_swapped_symlink_dir(tmp_path):
    """Regression (A3 audit): agent replaces a snapshotted dir with a symlink out."""
    import os
    import shutil

    from harness_sandbox.snapshots import EnvironmentSnapshots

    ws, outside = tmp_path / "ws", tmp_path / "outside"
    (ws / "sub").mkdir(parents=True)
    outside.mkdir()
    (ws / "sub" / "f.txt").write_text("orig")
    snaps = EnvironmentSnapshots(tmp_path / "snaps")
    sid = snaps.create("team1", "task1", ws)["id"]
    shutil.rmtree(ws / "sub")
    os.symlink(outside, ws / "sub")
    res = snaps.restore("team1", "task1", sid, ws, discard_dirty=True)
    assert list(outside.iterdir()) == []  # nothing escaped
    assert "sub/f.txt" in res.skipped_protected
