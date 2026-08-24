"""M12-G8 — route tests for the read-only repo tree API.

Seeds a temp workspace through the REAL LocalSandboxRunner (create_task with
project_id), mounts the tree router with a stubbed principal (the same
_app_with_auth protocol as test_sandbox_conformance.py) and exercises:
listing shape + dirs-first order, lazy subdirectory listing, the traversal
guard (../ chains, absolute paths, symlink escape, ~), 404 envelopes for
unknown project / missing dir / file-as-dir, role matrix (readers allowed,
unwired auth fails closed), and the read-only guarantee.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_sandbox.api import get_principal
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.tree_api import list_workspace_dir
from harness_sandbox.tree_api import router as tree_router

PROJECT_ID = "proj-tree-1"
OTHER_PROJECT = "proj-tree-other"


def _principal(role="admin", user_id="u-tree", team_id="team-tree"):
    from types import SimpleNamespace

    return SimpleNamespace(userId=user_id, teamId=team_id, role=role)


def _app(principal=None):
    app = FastAPI()
    app.include_router(tree_router)
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return app, TestClient(app)


@pytest.fixture()
def seeded(tmp_path):
    """Real runner + one active task per project, files written on disk."""
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks")
    runner.create_task(
        task_id="t-tree",
        repo_ref="org/repo",
        branch="main",
        forbidden_paths=[],
        project_id=PROJECT_ID,
    )
    ws: Path = runner.active_tasks["t-tree"]["workspace"]
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("print('hi')\n")  # 12 bytes
    (ws / "README.md").write_text("hello")  # 5 bytes
    (ws / "z.log").write_text("x" * 3)
    other = runner.create_task(
        task_id="t-other",
        repo_ref="org/other",
        branch="main",
        forbidden_paths=[],
        project_id=OTHER_PROJECT,
    )
    assert Path(other.containerId.replace("local-", "")).name == "t-other"
    return runner


def _client_for(runner, principal=None):
    from harness_sandbox import tree_api

    previous = tree_api._tree_runner_override
    tree_api.configure_tree_runner(runner)
    try:
        app, client = _app(principal if principal is not None else _principal())
        yield client
    finally:
        tree_api._tree_runner_override = previous


@pytest.fixture()
def client(seeded):
    yield from _client_for(seeded)


class TestTreeListing:
    def test_root_lists_entries_dirs_first(self, client):
        res = client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree")
        assert res.status_code == 200
        data = res.json()
        assert data["path"] == ""
        names = [e["name"] for e in data["entries"]]
        # dirs-first ordering: src before any file, files alphabetical after it
        assert names.index("src") < names.index("README.md") < names.index("z.log")
        src = next(e for e in data["entries"] if e["name"] == "src")
        readme = next(e for e in data["entries"] if e["name"] == "README.md")
        assert src == {"name": "src", "type": "dir", "size": 0}
        assert readme == {"name": "README.md", "type": "file", "size": 5}

    def test_subdirectory_lazy_listing(self, client):
        res = client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree", params={"path": "src"})
        assert res.status_code == 200
        data = res.json()
        assert data["path"] == "src"
        assert data["entries"] == [{"name": "main.py", "type": "file", "size": 12}]

    def test_unknown_project_is_contract_404(self, client):
        res = client.get("/api/v1/sandbox/proj-nobody/tree")
        assert res.status_code == 404
        body = res.json()
        assert body["detail"]["error"]["code"] == "PATH_NOT_FOUND"

    def test_missing_dir_is_404_envelope(self, client):
        res = client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree", params={"path": "nope"})
        assert res.status_code == 404
        assert res.json()["detail"]["error"]["code"] == "PATH_NOT_FOUND"

    def test_file_used_as_directory_is_404(self, client):
        res = client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree", params={"path": "README.md"})
        assert res.status_code == 404
        assert res.json()["detail"]["error"]["code"] == "PATH_NOT_FOUND"


class TestTraversalGuard:
    @pytest.mark.parametrize(
        "attempt",
        [
            "../escape.txt",
            "../../etc/passwd",
            "src/../../../../etc/passwd",
            "/etc/passwd",
            "~root",
            "src/../..",
        ],
    )
    def test_escape_attempts_rejected_404(self, client, attempt):
        res = client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree", params={"path": attempt})
        assert res.status_code == 404, f"{attempt!r} must never list outside the workspace"
        assert res.json()["detail"]["error"]["code"] == "PATH_NOT_FOUND"

    def test_symlink_escape_blocked_at_guard_level(self, seeded, tmp_path):
        """A symlink inside the workspace pointing outside must be rejected by
        the resolve()+relative_to guard itself — not just by HTTP mapping."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        ws: Path = seeded.active_tasks["t-tree"]["workspace"]
        (ws / "innocent").symlink_to(outside)
        with pytest.raises(PermissionError):
            list_workspace_dir(ws, "innocent")

    def test_unit_guard_rejects_lexical_escape(self, seeded):
        ws: Path = seeded.active_tasks["t-tree"]["workspace"]
        with pytest.raises(PermissionError):
            list_workspace_dir(ws, "../other-task-workspace")


class TestTeamScopingAndAuth:
    def test_projects_do_not_leak_into_each_other(self, seeded):
        gen = _client_for(seeded)
        client = next(gen)
        res = client.get(f"/api/v1/sandbox/{OTHER_PROJECT}/tree")
        assert res.status_code == 200
        assert res.json()["entries"] == []  # other project's fresh workspace

    @pytest.mark.parametrize("role", ["viewer", "guest"])
    def test_read_roles_may_read(self, seeded, role):
        gen = _client_for(seeded, _principal(role))
        client = next(gen)
        res = client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree")
        assert res.status_code == 200
        assert any(e["name"] == "src" for e in res.json()["entries"])

    def test_unwired_auth_fails_closed(self, seeded):
        _, client = _app()  # no principal override → get_principal raises 503
        res = client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree")
        assert res.status_code in (401, 403, 503)


class TestReadOnlyGuarantee:
    def test_no_write_methods_on_tree_route(self):
        from starlette.routing import Route

        tree_routes = [
            sorted(r.methods or [])
            for r in tree_router.routes
            if isinstance(r, Route) and r.path.endswith("/tree")
        ]
        assert tree_routes and all(m == ["GET"] for m in tree_routes)

    def test_listing_does_not_mutate_workspace(self, client, seeded):
        ws: Path = seeded.active_tasks["t-tree"]["workspace"]
        snapshot = lambda: sorted(  # noqa: E731 — tiny test-local helper
            str(p.relative_to(ws)) for p in ws.rglob("*")
        )
        before = snapshot()
        client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree")
        client.get(f"/api/v1/sandbox/{PROJECT_ID}/tree", params={"path": "src"})
        after = snapshot()
        assert before == after
