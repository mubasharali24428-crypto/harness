"""F012 git-history retrieval proofs.

Safety-first history search: argv-array only (no shell — user text reaches
git as a literal --grep token or a validated trailing pathspec), typed empty
answers for non-git/empty repos, and name-status parsing that surfaces
add/modify/delete/rename. The -z name-status byte format was pinned
empirically before this suite was written (schema changes here are contract
changes, not cosmetic).
"""

import asyncio
import subprocess

import pytest
from conftest import AUTH_HEADERS, seed_project

from harness_context.git_history import (
    InvalidRepoPathError,
    is_git_repo,
    search_history,
    validate_relative_path,
)


def _git(root, *args) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _history_repo(tmp_path):
    root = tmp_path / "histrepo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tester@example.com")
    _git(root, "config", "user.name", "Tester")
    (root / "README.md").write_text("hello\n")
    (root / "app.py").write_text("print('v1')\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial import")
    (root / "app.py").write_text("print('v2')\n")
    (root / "lib.py").write_text("X = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add widget quota logic")
    _git(root, "mv", "lib.py", "mod.py")
    _git(root, "commit", "-q", "-m", "rename helper module")
    _git(root, "rm", "-q", "mod.py")
    _git(root, "commit", "-q", "-m", "drop dead module")
    return root


def test_validate_relative_path_accepts_clean_and_rejects_flags():
    assert validate_relative_path("pkg/a.py") == "pkg/a.py"
    assert validate_relative_path("./pkg/a.py") == "pkg/a.py"
    for bad in ("/abs/x", "../x", "a/../b", "-flag", ":magic", ""):
        with pytest.raises(InvalidRepoPathError):
            validate_relative_path(bad)


def test_search_matches_message_and_reports_name_status(tmp_path):
    root = _history_repo(tmp_path)
    commits, reason = search_history(root, "widget quota")
    assert reason is None
    assert len(commits) == 1
    c = commits[0]
    assert c.subject == "add widget quota logic"
    assert len(c.sha) == 40
    assert "T" in c.date_iso  # %aI ISO-8601
    assert c.author == "Tester"
    statuses = {f.path: f.status for f in c.files}
    assert statuses == {"app.py": "M", "lib.py": "A"}


def test_query_is_case_insensitive_and_never_regex(tmp_path):
    root = _history_repo(tmp_path)
    # Case-insensitive match...
    upper, _ = search_history(root, "WIDGET")
    assert len(upper) == 1
    # ...but FIXED-STRING: a regex that would otherwise match finds nothing.
    none, _ = search_history(root, "w.dget")
    assert none == []


def test_empty_query_returns_recent_first_with_limit(tmp_path):
    root = _history_repo(tmp_path)
    commits, reason = search_history(root, None, limit=2)
    assert reason is None
    assert [c.subject for c in commits] == [
        "drop dead module",
        "rename helper module",
    ]


def test_rename_and_delete_statuses(tmp_path):
    root = _history_repo(tmp_path)
    renamed, _ = search_history(root, "rename helper")
    assert [(f.status, f.path) for f in renamed[0].files] == [("R", "mod.py")]
    dropped, _ = search_history(root, "drop dead")
    assert [(f.status, f.path) for f in dropped[0].files] == [("D", "mod.py")]


def test_path_filter_is_trailing_pathspec(tmp_path):
    root = _history_repo(tmp_path)
    commits, reason = search_history(root, None, path="lib.py")
    assert reason is None
    # The pathspec matches BOTH history touches of lib.py: its add and its
    # rename-away (the rename's old side is lib.py) — and nothing else.
    assert [c.subject for c in commits] == [
        "rename helper module",
        "add widget quota logic",
    ]

    # Invalid pathspec never reaches git — raises before spawn.
    with pytest.raises(InvalidRepoPathError):
        search_history(root, None, path="../secrets")


def test_non_git_directory_is_typed_empty(tmp_path):
    plain = tmp_path / "notgit"
    plain.mkdir()
    assert is_git_repo(plain) is False
    commits, reason = search_history(plain, "anything")
    assert commits == [] and reason == "not_a_git_repo"


def test_repo_with_no_commits_is_typed_empty(tmp_path):
    root = tmp_path / "emptyrepo"
    root.mkdir()
    _git(root, "init", "-q")
    commits, reason = search_history(root, "x")
    assert commits == [] and reason == "empty_history"


# --------------------------------------------------------------- HTTP level


async def _index(client, project: str, repo) -> None:
    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": str(repo), "projectId": project},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    job_id = res.json()["jobId"]
    for _ in range(200):
        st = (
            await client.get(f"/api/v1/context/index/{job_id}", headers=AUTH_HEADERS)
        ).json()
        if st["status"] in ("completed", "failed"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("index job did not finish")


async def test_git_search_over_http(client, db, tmp_path):
    """Root is resolved server-side from the project's index job — the
    request body has no repoPath field to point somewhere else."""
    await seed_project(db, "p-git")
    root = _history_repo(tmp_path)
    await _index(client, "p-git", root)

    res = await client.post(
        "/api/v1/context/git-search",
        json={"projectId": "p-git", "query": "widget"},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reason"] is None
    assert [c["subject"] for c in body["commits"]] == ["add widget quota logic"]
    assert sorted(
        (f["status"], f["path"]) for f in body["commits"][0]["files"]
    ) == [("A", "lib.py"), ("M", "app.py")]


async def test_git_search_rejects_flag_like_path(client, db, tmp_path):
    await seed_project(db, "p-git")
    root = _history_repo(tmp_path)
    await _index(client, "p-git", root)

    res = await client.post(
        "/api/v1/context/git-search",
        json={"projectId": "p-git", "path": "--upload-pack=/bin/sh"},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 422


async def test_git_search_without_indexed_root_is_404(client, db):
    await seed_project(db, "p-git")
    res = await client.post(
        "/api/v1/context/git-search",
        json={"projectId": "p-git", "query": "x"},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NO_INDEXED_REPO"


async def test_git_search_foreign_project_is_no_oracle_404(client, db):
    await seed_project(db, "p-foreign", team_id="team-other")
    res = await client.post(
        "/api/v1/context/git-search",
        json={"projectId": "p-foreign", "query": "x"},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
