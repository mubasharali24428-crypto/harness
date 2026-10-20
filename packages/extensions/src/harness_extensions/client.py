"""Typed REST client for the extensions service (customer-facing SDK).

Usage::

    from harness_extensions.client import ExtensionsClient

    async with ExtensionsClient("https://harness.internal", token=os.environ["HARNESS_TOKEN"]) as c:
        server = await c.register_server(name="jira", url="https://mcp.example/jira")
        await c.create_grant(server_id=server["serverId"], tool_name="search")
        result = await c.call_tool(server_id=server["serverId"], tool_name="search",
                                   arguments={"jql": "project = HARNESS"})

Every non-2xx becomes :class:`ExtensionsApiError` carrying the frozen
``{code, message}`` envelope and the HTTP status, so callers branch on codes,
never on strings. The client is team-scoped by the bearer token alone — it
never sends teamId/userId in bodies.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_PREFIX = "/api/v1/extensions"


class ExtensionsApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"HTTP {status_code} {code}: {message}")


class ExtensionsClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        prefix: str = DEFAULT_PREFIX,
    ) -> None:
        self._prefix = prefix.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._owns_client = client is None
        self._headers = {"Authorization": f"Bearer {token}"}

    async def __aenter__(self) -> ExtensionsClient:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---------------------------------------------------------------- core

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        res = await self._client.request(
            method, f"{self._prefix}{path}", headers=self._headers, **kw
        )
        if res.status_code >= 400:
            try:
                err = res.json()["error"]
                raise ExtensionsApiError(res.status_code, err["code"], err["message"])
            except (ValueError, KeyError) as exc:
                raise ExtensionsApiError(
                    res.status_code, "UNKNOWN_ERROR", res.text[:500]
                ) from exc
        if res.status_code == 204 or not res.content:
            return None
        return res.json()

    # ------------------------------------------------------------- servers

    async def register_server(
        self, *, name: str, url: str, project_id: str | None = None
    ) -> dict:
        body: dict[str, Any] = {"name": name, "url": url}
        if project_id is not None:
            body["projectId"] = project_id
        return await self._request("POST", "/servers", json=body)

    async def list_servers(self) -> list[dict]:
        return (await self._request("GET", "/servers"))["items"]

    async def get_server(self, server_id: str) -> dict:
        return await self._request("GET", f"/servers/{server_id}")

    async def revoke_server(self, server_id: str) -> dict:
        return await self._request("DELETE", f"/servers/{server_id}")

    async def probe_server_health(self, server_id: str) -> dict:
        return await self._request("POST", f"/servers/{server_id}/health")

    async def list_server_tools(self, server_id: str) -> list[dict]:
        return (await self._request("GET", f"/servers/{server_id}/tools"))["items"]

    # -------------------------------------------------------------- grants

    async def create_grant(
        self,
        *,
        server_id: str,
        tool_name: str,
        project_id: str | None = None,
        run_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"serverId": server_id, "toolName": tool_name}
        if project_id is not None:
            body["projectId"] = project_id
        if run_id is not None:
            body["runId"] = run_id
        if expires_at is not None:
            body["expiresAt"] = expires_at
        return await self._request("POST", "/grants", json=body)

    async def list_grants(self) -> list[dict]:
        return (await self._request("GET", "/grants"))["items"]

    async def revoke_grant(self, grant_id: str) -> dict:
        return await self._request("DELETE", f"/grants/{grant_id}")

    # --------------------------------------------------------------- calls

    async def call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "serverId": server_id,
            "toolName": tool_name,
            "arguments": arguments or {},
        }
        if run_id is not None:
            body["runId"] = run_id
        if project_id is not None:
            body["projectId"] = project_id
        return await self._request("POST", "/calls", json=body)

    # -------------------------------------------------------- custom tools

    async def create_custom_tool(
        self,
        *,
        name: str,
        server_id: str,
        source_tool: str,
        allowed_arguments: list[str] | None = None,
        default_arguments: dict | None = None,
        description: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "name": name,
            "serverId": server_id,
            "sourceTool": source_tool,
        }
        if allowed_arguments is not None:
            body["allowedArguments"] = allowed_arguments
        if default_arguments is not None:
            body["defaultArguments"] = default_arguments
        if description is not None:
            body["description"] = description
        return await self._request("POST", "/custom-tools", json=body)

    async def call_custom_tool(self, custom_tool_id: str, arguments: dict | None = None) -> dict:
        return await self._request(
            "POST", f"/custom-tools/{custom_tool_id}/call", json={"arguments": arguments or {}}
        )

    # -------------------------------------------------------------- skills

    async def create_skill(self, *, name: str, content: str) -> dict:
        return await self._request("POST", "/skills", json={"name": name, "content": content})

    async def add_skill_version(self, skill_id: str, *, content: str) -> dict:
        return await self._request(
            "POST", f"/skills/{skill_id}/versions", json={"content": content}
        )

    async def get_skill(self, skill_id: str, version: int | None = None) -> dict:
        path = f"/skills/{skill_id}"
        if version is not None:
            path += f"?version={version}"
        return await self._request("GET", path)

    async def list_skills(self) -> list[dict]:
        return (await self._request("GET", "/skills"))["items"]

    # ------------------------------------------------------------ commands

    async def create_command(
        self,
        *,
        name: str,
        template: str,
        arguments: list[str] | None = None,
        description: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"name": name, "template": template, "arguments": arguments or []}
        if description is not None:
            body["description"] = description
        return await self._request("POST", "/commands", json=body)

    async def invoke_command(self, command_id: str, arguments: dict | None = None) -> dict:
        return await self._request(
            "POST", f"/commands/{command_id}/invoke", json={"arguments": arguments or {}}
        )

    async def list_commands(self) -> list[dict]:
        return (await self._request("GET", "/commands"))["items"]

    # --------------------------------------------------------------- hooks

    async def create_hook(self, *, name: str, event_type: str, target: dict) -> dict:
        return await self._request(
            "POST", "/hooks", json={"name": name, "eventType": event_type, "target": target}
        )

    async def list_hooks(self) -> list[dict]:
        return (await self._request("GET", "/hooks"))["items"]

    async def disable_hook(self, hook_id: str) -> dict:
        return await self._request("DELETE", f"/hooks/{hook_id}")

    async def list_hook_deliveries(self, hook_id: str, limit: int = 50) -> list[dict]:
        return (
            await self._request("GET", f"/hooks/{hook_id}/deliveries?limit={limit}")
        )["items"]

    # -------------------------------------------------------------- policy

    async def add_policy_rule(self, *, mode: str, pattern: str) -> dict:
        return await self._request("POST", "/policy", json={"mode": mode, "pattern": pattern})

    async def list_policy_rules(self) -> list[dict]:
        return (await self._request("GET", "/policy"))["items"]

    async def delete_policy_rule(self, policy_id: str) -> dict:
        return await self._request("DELETE", f"/policy/{policy_id}")

    # ----------------------------------------------------------- catalog

    async def list_catalog(self) -> list[dict]:
        return (await self._request("GET", "/catalog"))["items"]

    async def install_plugin(self, plugin_id: str) -> dict:
        return await self._request("POST", f"/catalog/{plugin_id}/install")

    async def uninstall_plugin(self, plugin_id: str) -> dict:
        return await self._request("POST", f"/plugins/{plugin_id}/uninstall")

    async def list_plugins(self) -> list[dict]:
        return (await self._request("GET", "/plugins"))["items"]
