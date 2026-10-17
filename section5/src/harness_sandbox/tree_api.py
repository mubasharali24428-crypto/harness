"""M12-G8 — read-only repo file tree over a project's sandbox workspace.

First slice of part3 gap #7 (N9 "Repo browser / file tree", the K3 BLOCKER
unblocker). Serves `GET /api/v1/sandbox/{projectId}/tree?path=` listing the
contents of one directory inside the workspace of any active task belonging to
`projectId`, with:

- server-side path-traversal guard: every candidate resolves under the task
  workspace via Path.resolve() + is_relative_to() (symlink-safe), matching the
  chokepoint discipline docs/clientela/raw-22-hardening.md asks for;
- team scoping through the same bearer principal dependency as api.py —
  reading is allowed for every role (`can(p, "read")` is True for all four),
  writes stay impossible because this router has no write routes at all;
- frozen-contract error envelopes ({"error": {code, message}}) wrapped in
  FastAPI HTTPException detail exactly like harness_sandbox.api.

Read-only by construction: nothing here opens files for writing, mutates the
workspace, or executes anything. Content viewing lands in W3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from harness_contracts.roles import Principal, can
from pydantic import BaseModel, Field

from harness_sandbox.api import _lookup_project_team, get_principal

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])


def get_tree_runner():
    """Return the LocalSandboxRunner whose workspaces are browsed.

    Module-level indirection (like api.py's _service) so tests and Section 10
    deploy wiring can re-point it; defaults to the live SandboxService runner.
    """
    from harness_sandbox.api import _service

    return _service.runner


def configure_tree_runner(runner) -> None:
    """Deploy-time/test wiring: swap the runner tree listings resolve against."""
    global _tree_runner_override
    _tree_runner_override = runner


_tree_runner_override = None


def _resolve_runner():
    if _tree_runner_override is not None:
        return _tree_runner_override
    return get_tree_runner()


class TreeEntry(BaseModel):
    name: str
    type: str = Field(pattern="^(file|dir)$")
    size: int = Field(ge=0)


class TreeListing(BaseModel):
    path: str
    entries: list[TreeEntry] = Field(default_factory=list)


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "PATH_NOT_FOUND", "message": message}},
    )


def list_workspace_dir(workspace: Path, rel_path: str) -> TreeListing:
    """Resolve `rel_path` under `workspace` behind the traversal guard and list it.

    Guard order: lexical normalization first (kills ../ chains before they
    touch disk), then filesystem resolve + ``relative_to`` so even symlinked
    entries cannot smuggle us out of the workspace. Raises PermissionError
    (escape attempt), FileNotFoundError / NotADirectoryError (bad target);
    the route maps all three onto the 404 envelope.
    """
    root = workspace.resolve()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise PermissionError(f"path escapes the project workspace: {rel_path}") from e
    if not candidate.exists() or not candidate.is_dir():
        raise FileNotFoundError(f"no such directory in this workspace: {rel_path}")
    return _list_dir(candidate, rel_path)


def _list_dir(target: Path, rel_path: str) -> TreeListing:
    """Directory scan: dirs-first then name sort. Symlinked directories are
    omitted from this slice (their targets may live outside the workspace);
    broken entries are skipped honestly instead of failing the listing."""
    dirs: list[TreeEntry] = []
    files: list[TreeEntry] = []
    for child in sorted(target.iterdir(), key=lambda p: p.name):
        try:
            if child.is_dir() and not child.is_symlink():
                # Directory sizes are not reported — stat.st_size on a dir is
                # filesystem bookkeeping, not payload, and would be a lie.
                dirs.append(TreeEntry(name=child.name, type="dir", size=0))
            elif child.is_file():
                files.append(TreeEntry(name=child.name, type="file", size=child.stat().st_size))
        except OSError:
            continue  # raced/permission-denied entry: omit, never fail the listing
    return TreeListing(path=rel_path, entries=dirs + files)


async def require_reader(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Team-scoped reads. `can(p, 'read')` is True for all contract roles, but
    the check stays explicit so a future role vocabulary fails closed here."""
    if not can(principal, "read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "ROLE_DENIED", "message": "role may not read"}},
        )
    return principal


Reader = Annotated[Principal, Depends(require_reader)]


@router.get("/{projectId}/tree", response_model=TreeListing)
async def get_project_tree(
    projectId: str,
    request: Request,
    reader: Reader,
    path: Annotated[str, Query()] = "",
) -> TreeListing:
    # S1-R01d: project must belong to the reader's team. Unknown and foreign
    # projects answer identically (lookup unwired → 503, fail closed).
    team = await _lookup_project_team(projectId, request)
    if team is None or team != reader.teamId:
        raise _not_found(f"no active sandbox task for project '{projectId}'")

    runner = _resolve_runner()
    tasks = getattr(runner, "active_tasks", None)
    if tasks is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "SANDBOX_NOT_READY",
                    "message": "sandbox runner unavailable",
                }
            },
        )

    matches = [
        t for t in tasks.values() if t.get("project_id") == projectId
    ]
    if not matches:
        raise _not_found(f"no active sandbox task for project '{projectId}'")
    task = matches[0]

    rel = path.strip().lstrip("/")
    if rel.startswith("~"):
        raise _not_found("invalid path")
    workspace = Path(task["workspace"])
    try:
        return list_workspace_dir(workspace, rel)
    except (PermissionError, FileNotFoundError, NotADirectoryError) as e:
        raise _not_found(str(e)) from e
