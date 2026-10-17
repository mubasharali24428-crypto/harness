"""A4-S2 worktree/commit tests on disposable real git repositories (no network)."""

import os
import subprocess
from pathlib import Path

import pytest

from harness_github.gitflow.commits import commit_paths
from harness_github.gitflow.worktrees import WorktreeError, WorktreeManager

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": os.environ["PATH"],
    "HOME": "/nonexistent",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def sh(cwd, *args, env=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env or ENV, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repos" / "origin"
    r.mkdir(parents=True)
    sh(r, "init", "-b", "main")
    (r / "a.txt").write_text("a\n")
    (r / "secrets").mkdir()
    (r / "secrets" / "k").write_text("k\n")
    sh(r, "add", "-A")
    sh(r, "commit", "-m", "init")
    return r


@pytest.fixture
def mgr(repo, tmp_path):
    return WorktreeManager(repo, tmp_path / "wt", repo_roots=(str(tmp_path / "repos"),))


def test_two_runs_are_isolated(mgr):
    a = mgr.create("run-a", "main")
    b = mgr.create("run-b", "main")
    assert a.branch == "agent/run-a" and b.branch == "agent/run-b" and a.path != b.path
    (Path(a.path) / "a.txt").write_text("changed in A\n")
    assert (Path(b.path) / "a.txt").read_text() == "a\n"
    assert mgr.status("run-a")["dirty"] and not mgr.status("run-b")["dirty"]


def test_create_is_idempotent_and_branch_cannot_be_double_checked_out(mgr):
    first = mgr.create("run-a", "main")
    again = mgr.create("run-a", "main")
    assert again.reused and again.path == first.path


def test_run_id_escape_and_bad_refs_rejected(mgr):
    for bad in ["../x", "a/b", "", "-f", "x" * 70]:
        with pytest.raises(WorktreeError):
            mgr.create(bad, "main")
    for bad in ["--upload-pack=x", "a..b", "nonexistent-ref"]:
        with pytest.raises(WorktreeError):
            mgr.create("run-x", bad)


def test_repo_outside_allowed_roots_or_root_inside_repo_refused(repo, tmp_path):
    with pytest.raises(WorktreeError) as e:
        WorktreeManager(repo, tmp_path / "wt", repo_roots=(str(tmp_path / "other"),))
    assert e.value.code == "REPO_NOT_ALLOWED"
    with pytest.raises(WorktreeError):
        WorktreeManager(repo, repo / "wt", repo_roots=(str(tmp_path),))
    with pytest.raises(WorktreeError):
        WorktreeManager(repo, "relative/dir", repo_roots=(str(tmp_path),))


def test_dirty_worktree_is_never_discarded_implicitly(mgr):
    info = mgr.create("run-a", "main")
    (Path(info.path) / "new" / "deep").mkdir(parents=True)
    (Path(info.path) / "new" / "deep" / "f.txt").write_text("work\n")
    st = mgr.status("run-a")
    assert st["dirty"] and "new/deep/f.txt" in st["changed"]  # -uall sees files in new dirs
    with pytest.raises(WorktreeError) as e:
        mgr.remove("run-a")
    assert e.value.code == "DIRTY" and (Path(info.path) / "new" / "deep" / "f.txt").exists()
    assert mgr.remove("run-a", discard=True)["removed"] is True
    assert not Path(info.path).exists()
    sh(mgr.repo, "rev-parse", "--verify", "refs/heads/agent/run-a")  # branch (and commits) kept


def test_clean_remove_and_recover_after_manual_deletion_keeps_commits(mgr):
    info = mgr.create("run-a", "main")
    (Path(info.path) / "a.txt").write_text("committed work\n")
    commit_paths(Path(info.path), ["a.txt"], "work\n", author_name="Dev", author_email="d@e.com")
    import shutil

    shutil.rmtree(info.path)  # crash/cleanup removed the directory but git still registers it
    st = mgr.status("run-a")
    assert st["registered"] and not st["exists"]
    res = mgr.recover("run-a")
    assert res["action"] == "recreated"
    assert (Path(info.path) / "a.txt").read_text() == "committed work\n"
    assert mgr.recover("run-a")["action"] == "none"  # idempotent


def test_recover_fails_clearly_when_branch_is_gone(mgr):
    assert mgr.status("run-z")["exists"] is False
    with pytest.raises(WorktreeError) as e:
        mgr.recover("run-z")
    assert e.value.code == "BRANCH_GONE"


def test_hostile_repo_config_and_hooks_never_execute(repo, tmp_path):
    marker = tmp_path / "PWNED"
    hooks = repo / ".git" / "hooks"
    for name in ("post-checkout", "pre-commit", "commit-msg", "reference-transaction"):
        h = hooks / name
        h.write_text(f"#!/bin/sh\ntouch {marker}\n")
        h.chmod(0o755)
    sh(repo, "config", "core.fsmonitor", f"touch {marker}; echo")
    sh(repo, "config", "core.hooksPath", str(hooks))
    sh(repo, "config", "core.sshCommand", f"touch {marker}")
    m = WorktreeManager(repo, tmp_path / "wt", repo_roots=(str(tmp_path / "repos"),))
    info = m.create("run-h", "main")
    (Path(info.path) / "a.txt").write_text("x\n")
    m.status("run-h")
    commit_paths(Path(info.path), ["a.txt"], "m\n", author_name="D", author_email="d@e.com")
    m.remove("run-h", discard=True)
    assert not marker.exists(), "repo-controlled config/hook executed on the host"


def test_commit_only_named_paths_with_attribution(mgr):
    info = mgr.create("run-a", "main")
    wt = Path(info.path)
    (wt / "a.txt").write_text("one\n")
    (wt / "other.txt").write_text("not mine\n")
    res = commit_paths(wt, ["a.txt"], "msg\n", author_name="Real Dev", author_email="dev@ex.com")
    assert res["files"] == ["a.txt"]
    log = sh(wt, "log", "-1", "--format=%an <%ae>|%cn <%ce>")
    assert log.strip() == "Real Dev <dev@ex.com>|Harness <harness@users.noreply.invalid>"
    assert "other.txt" in mgr.status("run-a")["changed"]  # left untouched, unstaged
    assert "other.txt" not in sh(wt, "show", "--name-only", "--format=", "HEAD")


def test_commit_rejects_forbidden_git_traversal_and_empty(mgr):
    info = mgr.create("run-a", "main")
    wt = Path(info.path)
    (wt / "secrets" / "k").write_text("tampered\n")
    for bad, forbidden in [
        ("secrets/k", ["secrets"]),
        ("SECRETS/k", ["secrets"]),
        (".git/config", []),
        ("../x", []),
        ("/etc/passwd", []),
        ("-A", []),
    ]:
        with pytest.raises(WorktreeError) as e:
            commit_paths(
                wt, [bad], "m\n", author_name="D", author_email="d@e.com", forbidden=forbidden
            )
        assert e.value.code == "PATH_REJECTED", bad
    with pytest.raises(WorktreeError) as e:
        commit_paths(wt, ["a.txt"], "m\n", author_name="D", author_email="d@e.com")
    assert e.value.code == "NOTHING_TO_COMMIT"
