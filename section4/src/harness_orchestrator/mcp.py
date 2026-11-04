"""Reviewed MCP servers for the engine worker (W4 slice 3).

An MCP server is a process we spawn (or a URL we call) inside the confined
worker, so it gets the same treatment as the shell gate: the list is
**operator-reviewed configuration, never plan or model input**. The run's
``shell_policy`` carries no MCP keys and nothing here reads the plan — the only
channel is ``DSH_MCP_SERVERS``, a path to a JSON file that ships with the image
(or is mounted by the operator), validated entry by entry before it can reach
the runtime.

Credential model, chosen per docs/week-6-plan.md's predecessor
(docs/week-4-plan.md decision 3): **spawned servers get no credentials at all.**
`env` must be empty and `authorization`/`cookie`-style headers are refused, so a
server's tool result cannot be a route for a token back into the model context.
A broker that injects at call time is the later design; until one exists, the
safe default is the one enforced here.

Nothing is silently skipped: a declared-but-unreadable list fails the run, and a
malformed entry fails the run, because a partially applied boundary is the kind
of drift this file exists to prevent.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SERVERS_ENV = "DSH_MCP_SERVERS"
PLUGIN = "@deepseek-ai/dsh-mcp-client"
MAX_SERVERS = 8

STDIO_KEYS = {
    "transport", "serverName", "command", "args", "cwd",
    "toolCallTimeoutMs", "failOnStartupError", "maxInstructionBytes", "reconnect", "env",
}
HTTP_KEYS = {
    "transport", "serverName", "url", "headers",
    "toolCallTimeoutMs", "failOnStartupError", "maxInstructionBytes", "reconnect",
}
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
_CREDENTIAL_HEADER = re.compile(r"authorization|cookie|token|secret|api[-_]?key", re.I)


class McpConfigError(ValueError):
    """The reviewed list is absent-but-declared, unreadable, or malformed."""


def _entry_error(entry: Any, index: int) -> ValueError:
    return ValueError(f"mcp server #{index}: {entry}")


def validate_server(entry: Any, index: int) -> dict[str, Any]:
    """Return the runtime config for one reviewed entry, or refuse it."""
    if not isinstance(entry, dict):
        raise _entry_error("must be an object", index)
    transport = entry.get("transport")
    if transport == "stdio":
        allowed, required = STDIO_KEYS, ("serverName", "command")
    elif transport == "streamable-http":
        allowed, required = HTTP_KEYS, ("serverName", "url")
    else:
        raise _entry_error(
            f"transport must be stdio or streamable-http, got {transport!r}", index
        )
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise _entry_error(f"unknown keys are not accepted: {unknown}", index)
    for key in required:
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise _entry_error(f"{key} is required", index)
    name = entry["serverName"]
    if not _NAME_RE.fullmatch(name):
        raise _entry_error(f"serverName {name!r} must match {_NAME_RE.pattern}", index)

    config: dict[str, Any] = {"transport": transport, "serverName": name}
    if transport == "stdio":
        command = entry["command"]
        args = entry.get("args") or []
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise _entry_error("args must be a list of strings", index)
        config["command"] = command
        config["args"] = args
        if isinstance(entry.get("cwd"), str):
            config["cwd"] = entry["cwd"]
        env = entry.get("env")
        if env not in (None, {}, ""):
            # the credential rule, enforced rather than documented
            raise _entry_error(
                "env must be empty: a spawned MCP server gets no credentials", index
            )
    else:
        url = entry["url"]
        if not url.startswith("https://"):
            raise _entry_error(
                f"url must be https (plain HTTP is denied by egress): {url!r}", index
            )
        config["url"] = url
        headers = entry.get("headers") or {}
        if not isinstance(headers, dict):
            raise _entry_error("headers must be an object", index)
        secret = [k for k in headers if _CREDENTIAL_HEADER.search(str(k))]
        if secret:
            raise _entry_error(
                f"credential-bearing headers are refused: {sorted(secret)}", index
            )
        if headers:
            config["headers"] = {str(k): str(v) for k, v in headers.items()}

    # Fail loud at startup: a server the operator listed but that does not come
    # up must end the run, not leave the agent silently tool-poor.
    config["failOnStartupError"] = True
    timeout = entry.get("toolCallTimeoutMs")
    if isinstance(timeout, int) and 0 < timeout <= 300_000:
        config["toolCallTimeoutMs"] = timeout
    max_bytes = entry.get("maxInstructionBytes")
    if isinstance(max_bytes, int) and 0 < max_bytes <= 1_048_576:
        config["maxInstructionBytes"] = max_bytes
    reconnect = entry.get("reconnect")
    if isinstance(reconnect, dict):
        config["reconnect"] = reconnect
    return config


def reviewed_servers(environ: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The validated runtime configs, or an empty list when none are declared."""
    env = os.environ if environ is None else environ
    path = (env.get(SERVERS_ENV) or "").strip()
    if not path:
        return []
    target = Path(path)
    if not target.is_file():
        raise McpConfigError(f"{SERVERS_ENV}={path!r} is set but the file does not exist")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise McpConfigError(f"{SERVERS_ENV}={path!r} is not valid JSON: {exc}") from exc
    servers = raw.get("servers") if isinstance(raw, dict) else raw
    if not isinstance(servers, list):
        raise McpConfigError(f"{SERVERS_ENV}: expected a list of servers")
    if len(servers) > MAX_SERVERS:
        raise McpConfigError(
            f"{SERVERS_ENV}: {len(servers)} servers exceeds the {MAX_SERVERS} limit"
        )
    seen: set[str] = set()
    configs: list[dict[str, Any]] = []
    for index, entry in enumerate(servers):
        config = validate_server(entry, index)
        if config["serverName"] in seen:
            raise McpConfigError(f"{SERVERS_ENV}: duplicate serverName {config['serverName']!r}")
        seen.add(config["serverName"])
        configs.append(config)
    return configs
