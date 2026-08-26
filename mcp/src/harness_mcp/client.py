"""Thin async HTTP client over the harness sections' existing authenticated APIs.

One shared httpx.AsyncClient; every call carries the service token as a
bearer. Responses are returned RAW (parsed JSON or the error envelope) — this
module adds zero business logic, mirroring api-conventions.md shapes only.
"""

from __future__ import annotations

from typing import Any

import httpx


class UpstreamError(Exception):
    """An upstream section answered non-2xx (or transport failed).

    ``status`` is the HTTP status (0 for transport-level failures);
    ``envelope`` is the parsed error body when one arrived, else synthesized
    from the exception text. Tool code converts this into an isError result —
    it must never escape as a protocol-level crash.
    """

    def __init__(self, status: int, message: str, envelope: dict[str, Any] | None = None) -> None:
        self.status = status
        self.message = message
        self.envelope = envelope if envelope is not None else {"error": {"code": "UPSTREAM_ERROR", "message": message}}
        super().__init__(f"upstream {status}: {message}")


class HarnessApiClient:
    """Bearer-authenticated client over S1/S4/S11 REST surfaces."""

    def __init__(self, settings) -> None:
        self._settings = settings
        headers = {"Authorization": f"Bearer {settings.service_token}"} if settings.service_token else {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.upstream_timeout_s),
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Low-level verb with honest error mapping
    # ------------------------------------------------------------------

    async def _request(self, method: str, url: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            res = await self._client.request(method, url, json=json_body)
        except httpx.HTTPError as exc:
            raise UpstreamError(0, f"harness API unreachable at {url}: {exc}") from exc
        if res.status_code >= 400:
            try:
                body: Any = res.json()
            except ValueError:
                body = None
            # Sections answer the frozen envelope; keep it verbatim when present
            # so the LLM sees the exact machine code the section produced.
            detail = body.get("detail") if isinstance(body, dict) else None
            envelope: dict[str, Any]
            if isinstance(detail, dict) and "error" in detail:
                envelope = detail
            elif isinstance(detail, str):
                envelope = {"error": {"code": "UPSTREAM_ERROR", "message": detail}}
            elif isinstance(body, dict) and "error" in body:
                envelope = body
            else:
                envelope = {"error": {"code": "UPSTREAM_ERROR", "message": res.text[:500]}}
            raise UpstreamError(res.status_code, envelope["error"].get("message", ""), envelope)
        try:
            return res.json()
        except ValueError as exc:
            raise UpstreamError(res.status_code, "upstream returned non-JSON body") from exc

    # ------------------------------------------------------------------
    # list_projects — GET /api/v1/projects (Section 1 identity, port 8001)
    # ------------------------------------------------------------------

    async def list_projects(self) -> dict[str, Any]:
        return await self._request("GET", f"{self._settings.identity_base_url}/api/v1/projects")

    # ------------------------------------------------------------------
    # start_run walk — POST /plans → /plans/{id}/approve → /runs/{id}/start
    # (Section 4 orchestrator plan-gate, port 8004)
    # ------------------------------------------------------------------

    async def propose_plan(self, goal: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"{self._settings.orchestrator_base_url}/api/v1/orchestrator/plans", json_body={"goal": goal}
        )

    async def approve_plan(self, plan_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"{self._settings.orchestrator_base_url}/api/v1/orchestrator/plans/{plan_id}/approve",
        )

    async def start_run(self, run_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"{self._settings.orchestrator_base_url}/api/v1/orchestrator/runs/{run_id}/start",
        )

    # ------------------------------------------------------------------
    # get_run_status — GET /runs/{runId}/status (Section 4, port 8004)
    # ------------------------------------------------------------------

    async def run_status(self, run_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{self._settings.orchestrator_base_url}/api/v1/orchestrator/runs/{run_id}/status"
        )

    # ------------------------------------------------------------------
    # search_context — POST /api/v1/context/search (Section 11 context)
    # ------------------------------------------------------------------

    async def search_context(self, query: str, project_id: str, top_k: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"{self._settings.context_base_url}/api/v1/context/search",
            json_body={"query": query, "projectId": project_id, "topK": top_k},
        )
