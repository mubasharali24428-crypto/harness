"""Plan-gate client: the ONLY capability connectors have.

Intake can propose a plan (Section 4 baseline: ``POST /api/v1/orchestrator/plans``
mints a run + plan and leaves it at AWAITING_PLAN_APPROVAL). Intake can NOT
approve, start, or execute — those methods deliberately do not exist here.
A human approves through the normal console; that boundary is the F126/F127
"no automatic execution before human approval" control, enforced by absence.
"""

from __future__ import annotations

import httpx


class PlanGateError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


class ProposePlanGate:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {bearer_token}"}
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def propose_goal(self, goal: str) -> str:
        """-> planId, or raises PlanGateError(PLAN_GATE_UNAVAILABLE/...)."""
        try:
            res = await self._client.post(
                f"{self._base}/api/v1/orchestrator/plans",
                json={"goal": goal},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise PlanGateError(
                "PLAN_GATE_UNAVAILABLE", f"plan gate unreachable: {exc}", 502
            ) from exc
        if res.status_code >= 400:
            raise PlanGateError(
                "PLAN_GATE_REJECTED",
                f"plan gate returned HTTP {res.status_code}: {res.text[:200]}",
                502,
            )
        body = res.json()
        plan_id = body.get("planId")
        if not plan_id:
            raise PlanGateError("PLAN_GATE_BAD_RESPONSE", "no planId in response", 502)
        return str(plan_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
