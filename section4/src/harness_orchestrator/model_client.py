"""ModelRouter over the models service (section 3), called as the requesting user.

Section 3 owns provider calls, metering and the team budget, so the reviewer goes through its
HTTP API with the caller's own bearer token: the call is made with the caller's role, rate limit
and budget, and it is metered like any other model call. Sections import only contracts, so this
speaks HTTP and does not import section 3.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from harness_contracts.signatures import ModelCallOpts, ModelResult, ModelRole

DEFAULT_MODELS_URL = "http://models:8003"


class ModelsServiceRouter:
    def __init__(self, bearer: str, base_url: str | None = None, timeout: float = 170.0) -> None:
        self.bearer = bearer
        self.base_url = (
            base_url or os.environ.get("HARNESS_MODELS_URL", DEFAULT_MODELS_URL)
        ).rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = {"authorization": f"Bearer {self.bearer}"}
        data = None
        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def call_model(
        self,
        agent_id: str,
        role: ModelRole,
        messages: list[dict[str, str]],
        opts: ModelCallOpts | None = None,
    ) -> ModelResult:
        body = {
            "agentId": agent_id,
            "role": role.value,
            "messages": messages,
            "opts": opts.model_dump(exclude_none=True) if opts else None,
        }
        return ModelResult(**self._request("/api/v1/models/call", body))

    def estimate_cost(self, role: ModelRole, input_tokens: int, output_tokens: int) -> float:
        body = {"role": role.value, "inputTokens": input_tokens, "outputTokens": output_tokens}
        return float(self._request("/api/v1/models/estimate-cost", body)["estimatedCostUsd"])

    def list_available_models(self) -> list[str]:
        return list(self._request("/api/v1/models"))
