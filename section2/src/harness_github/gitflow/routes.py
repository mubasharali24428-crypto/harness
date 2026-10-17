"""HTTP adapter for A4-S2 under /api/v1/developer-workflow/projects/{id}/gitflow.

Identity/team come from the authenticated principal (never the body). There is
deliberately no merge route; PRs are only ever opened as drafts.
"""

import harness_contracts as contracts
import httpx
from fastapi import APIRouter, HTTPException
from harness_contracts import Principal

from ..client import MergeGuardViolation
from ..pr_open import record_pr_opened, run_id_from_branch
from ..store import get_repo_full_name
from .codeowners import route_reviewers
from .commits import Change, draft_commit_message
from .naming import NamingError, check_ref, run_branch
from .pr import NonDraftError, ensure_draft_pr, generate_pr_description

PREFIX = f"{contracts.API_PREFIX}/developer-workflow/projects/{{project_id}}/gitflow"

CODEOWNERS_LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
_WRITE_ROLES = {"admin", "engineer"}
MAX_CHANGES = 500


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})


def _require_writer(principal: Principal) -> None:
    role = str(getattr(principal, "role", "") or "").lower()
    if role not in _WRITE_ROLES:
        raise _err(403, "ROLE_DENIED", "your role cannot open branches or pull requests")


def _changes(raw: object) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > MAX_CHANGES:
        raise _err(422, "INVALID_REQUEST", f"changes must be a list of at most {MAX_CHANGES}")
    out = []
    for c in raw:
        if not isinstance(c, dict) or not isinstance(c.get("path"), str):
            raise _err(422, "INVALID_REQUEST", "each change needs a string path")
        out.append(
            {
                "path": c["path"],
                "status": str(c.get("status", "M"))[:1].upper() if c.get("status") else "M",
                "additions": max(0, int(c.get("additions", 0) or 0)),
                "deletions": max(0, int(c.get("deletions", 0) or 0)),
            }
        )
    return out


async def _codeowners(client, repo: str, ref: str) -> str:
    for loc in CODEOWNERS_LOCATIONS:
        try:
            return (await client.read_file(repo, ref, loc))["content"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            raise
    return ""


def _mapped(fn):
    """Existing section2 contract: unknown project -> 404; no GitHub link -> 409."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except LookupError as exc:
            raise _err(404, "NOT_FOUND", str(exc)) from exc
        except PermissionError as exc:
            raise _err(409, "GITHUB_NOT_CONNECTED", str(exc)) from exc

    return wrapper


def build_router(service_dep):
    """service_dep is app.service_dep; injected to avoid a circular import."""
    from typing import Annotated

    from fastapi import Depends

    ServiceDep = Annotated[tuple, Depends(service_dep)]
    router = APIRouter(prefix=PREFIX)  # per call: no shared route table across apps

    @router.post("/run-branch")
    @_mapped
    async def run_branch_create(project_id: str, body: dict, service: ServiceDep) -> dict:
        svc, principal = service
        _require_writer(principal)
        try:
            branch = run_branch(str(body.get("runId") or ""))
            base = check_ref(str(body.get("baseRef") or "main"))
        except NamingError as exc:
            raise _err(422, "INVALID_REQUEST", str(exc)) from exc
        repo = await get_repo_full_name(svc._db, project_id, principal.teamId)
        client, _ = await svc._client_for(principal.userId)
        try:
            await client.get_ref_sha(repo, branch)
            return {"branch": branch, "reused": True}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        try:
            sha = await client.create_branch(repo, base, branch)
        except httpx.HTTPStatusError as exc:
            raise _err(
                502, "PROVIDER_ERROR", f"could not create branch ({exc.response.status_code})"
            ) from exc
        return {"branch": branch, "baseRef": base, "sha": sha, "reused": False}

    @router.post("/commit-draft")
    @_mapped
    async def commit_draft(project_id: str, body: dict, service: ServiceDep) -> dict:
        svc, principal = service
        await get_repo_full_name(svc._db, project_id, principal.teamId)  # tenant check
        changes = [Change(**c) for c in _changes(body.get("changes"))]
        try:
            return draft_commit_message(
                run_id=str(body.get("runId") or ""),
                goal=str(body.get("goal") or ""),
                changes=changes,
                evidence=body.get("evidence") if isinstance(body.get("evidence"), list) else None,
            )
        except ValueError as exc:
            raise _err(422, "INVALID_REQUEST", str(exc)) from exc

    @router.post("/reviewers/route")
    @_mapped
    async def reviewers_route(project_id: str, body: dict, service: ServiceDep) -> dict:
        svc, principal = service
        repo = await get_repo_full_name(svc._db, project_id, principal.teamId)
        client, _ = await svc._client_for(principal.userId)
        try:
            base = check_ref(str(body.get("baseBranch") or "main"))
        except NamingError as exc:
            raise _err(422, "INVALID_REQUEST", str(exc)) from exc
        paths = [c["path"] for c in _changes(body.get("changes"))]
        text = await _codeowners(client, repo, base)
        author = (await client.get_user())["login"]
        return {"codeownersFound": bool(text), **route_reviewers(paths, text, author)}

    @router.post("/pull-requests")
    @_mapped
    async def open_pr(project_id: str, body: dict, service: ServiceDep) -> dict:
        svc, principal = service
        _require_writer(principal)
        try:
            run_id = str(body.get("runId") or "")
            branch = run_branch(run_id)
            base = check_ref(str(body.get("baseBranch") or "main"))
        except NamingError as exc:
            raise _err(422, "INVALID_REQUEST", str(exc)) from exc
        changes = _changes(body.get("changes"))
        evidence = body.get("evidence") if isinstance(body.get("evidence"), list) else None
        repo = await get_repo_full_name(svc._db, project_id, principal.teamId)
        client, _ = await svc._client_for(principal.userId)
        try:
            await client.get_ref_sha(repo, branch)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise _err(404, "BRANCH_NOT_FOUND", f"run branch {branch} does not exist") from exc
            raise
        text = await _codeowners(client, repo, base)
        author = (await client.get_user())["login"]
        routing = route_reviewers([c["path"] for c in changes], text, author)
        desc = generate_pr_description(
            run_id=run_id,
            goal=str(body.get("goal") or ""),
            changes=changes,
            evidence=evidence,
            commit_shas=[str(s) for s in body.get("commitShas", []) if isinstance(s, str)],
            reviewers=routing,
            notes=[str(n) for n in body.get("notes", []) if isinstance(n, str)],
        )
        try:
            pr = await ensure_draft_pr(client, repo, branch, base, desc["title"], desc["body"])
        except NonDraftError as exc:
            raise _err(
                502,
                "PROVIDER_NOT_DRAFT",
                f"the provider opened PR #{exc.number} as non-draft ({exc.url}); "
                "convert it to a draft manually",
            ) from exc
        except MergeGuardViolation as exc:
            raise _err(422, "MERGE_GUARD", str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise _err(
                502, "PROVIDER_ERROR", f"provider rejected the PR ({exc.response.status_code})"
            ) from exc
        reviewers_requested: dict = {"users": [], "teams": []}
        if not pr["reused"]:
            # Only on first open, so a retry never re-pings reviewers.
            if routing["reviewers"] or routing["teamReviewers"]:
                try:
                    res = await client.request_reviewers(
                        repo, pr["number"], routing["reviewers"], routing["teamReviewers"]
                    )
                    reviewers_requested = res["requested"]
                except httpx.HTTPStatusError:
                    reviewers_requested = {"users": [], "teams": [], "error": "request failed"}
            bound = run_id_from_branch(branch)
            if bound is not None:
                try:
                    emitted = await record_pr_opened(svc._db, svc._bus, project_id, bound, pr)
                    if emitted:
                        pr = {**pr, "prId": emitted["prId"]}
                except Exception:  # noqa: BLE001 - linkage is best effort, never fails the open
                    pass
        return {
            "pullRequest": pr,
            "reviewers": routing,
            "reviewersRequested": reviewers_requested,
            "title": desc["title"],
        }

    return router
