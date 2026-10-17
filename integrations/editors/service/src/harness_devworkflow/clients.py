"""HTTP clients for baseline services (identity, sandbox, orchestrator, context).

Every call forwards the caller's bearer token — authorization decisions stay
with the owning service; this lane only adds checks it can make locally
(project ownership via identity's team-scoped project list, path policy).
"""

from __future__ import annotations

from typing import Any

import httpx


class UpstreamError(RuntimeError):
    """A baseline service answered with a non-2xx or was unreachable."""

    def __init__(self, service: str, status: int, code: str, message: str):
        super().__init__(f"{service}: {code}: {message}")
        self.service = service
        self.status = status
        self.code = code
        self.message = message


class UpstreamUnreachable(UpstreamError):
    def __init__(self, service: str, detail: str):
        super().__init__(service, 503, "UPSTREAM_UNREACHABLE", detail)


def _error_from_body(body: Any) -> tuple[str, str]:
    """Extract (code, message) from the frozen error envelope or a bare body."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code", "UNKNOWN")), str(err.get("message", "upstream error"))
        detail = body.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
            inner = detail["error"]
            return str(inner.get("code", "UNKNOWN")), str(inner.get("message", "upstream error"))
    return "UNKNOWN", "upstream error"


class _BaseClient:
    def __init__(self, base: str, http: httpx.AsyncClient, service_name: str):
        self.base = base.rstrip("/")
        self.http = http
        self.service_name = service_name

    async def _request(
        self,
        method: str,
        path: str,
        token: str | None,
        json_body: Any | None = None,
        expect_json: bool = True,
    ) -> Any:
        headers = {}
        if token:
            headers["authorization"] = f"Bearer {token}"
        try:
            res = await self.http.request(
                method, f"{self.base}{path}", headers=headers, json=json_body
            )
        except httpx.HTTPError as exc:  # connection refused, timeout, ...
            raise UpstreamUnreachable(self.service_name, str(exc)) from exc
        body: Any = None
        if expect_json:
            try:
                body = res.json() if res.content else None
            except ValueError:
                body = None
        if res.status_code >= 400:
            code, message = _error_from_body(body)
            raise UpstreamError(self.service_name, res.status_code, code, message)
        return body if expect_json else res.text


class IdentityClient(_BaseClient):
    """Section 1 identity: session introspection + team-scoped project list."""

    def __init__(self, base: str, http: httpx.AsyncClient):
        super().__init__(base, http, "identity")

    async def me(self, token: str) -> dict[str, Any] | None:
        """Resolve a bearer token to {userId, teamId, role}; None when invalid."""
        try:
            body = await self._request("GET", "/api/v1/me", token)
        except UpstreamError as exc:
            if exc.status == 401:
                return None
            raise
        if not isinstance(body, dict) or not body.get("userId") or not body.get("teamId"):
            return None
        return body

    async def list_projects(self, token: str) -> list[dict[str, Any]]:
        body = await self._request("GET", "/api/v1/projects?limit=100", token)
        if isinstance(body, dict) and isinstance(body.get("items"), list):
            return [p for p in body["items"] if isinstance(p, dict)]
        return []


class SandboxClient(_BaseClient):
    """Section 5 sandbox: tasks, exec, edits, checkpoints, tree (baseline API)."""

    def __init__(self, base: str, http: httpx.AsyncClient):
        super().__init__(base, http, "sandbox")

    async def create_task(
        self,
        token: str,
        task_id: str,
        repo_ref: str,
        branch: str,
        forbidden_paths: list[str],
        project_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/sandbox/tasks",
            token,
            {
                "taskId": task_id,
                "repoRef": repo_ref,
                "branch": branch,
                "forbiddenPaths": forbidden_paths,
                "projectId": project_id,
            },
        )

    async def destroy_task(self, token: str, task_id: str) -> None:
        await self._request("POST", f"/api/v1/sandbox/tasks/{task_id}/destroy", token, {})

    async def exec_command(self, token: str, task_id: str, command: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v1/sandbox/tasks/{task_id}/exec",
            token,
            {"command": command},
        )

    async def apply_edit(self, token: str, task_id: str, path: str, diff_text: str) -> None:
        await self._request(
            "POST",
            f"/api/v1/sandbox/tasks/{task_id}/edits",
            token,
            {"path": path, "diff": diff_text},
        )

    async def list_checkpoints(self, token: str, project_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/sandbox/{project_id}/checkpoints", token)


class OrchestratorClient(_BaseClient):
    """Section 4 orchestrator: minimal read used to verify run visibility."""

    def __init__(self, base: str, http: httpx.AsyncClient):
        super().__init__(base, http, "orchestrator")

    async def run_status(self, token: str, run_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/orchestrator/runs/{run_id}/status", token)


class ContextClient(_BaseClient):
    """Section 11 context index: frozen W21-C1 search (consumed, never edited)."""

    def __init__(self, base: str, http: httpx.AsyncClient):
        super().__init__(base, http, "context")

    async def search(
        self, token: str, project_id: str, query: str, top_k: int = 8
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/context/search",
            token,
            {"query": query, "projectId": project_id, "topK": top_k},
        )
