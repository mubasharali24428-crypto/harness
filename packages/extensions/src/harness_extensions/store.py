"""Durable SQLite store for the extensions plane.

The registry must survive restarts (acceptance: "registry restart"), so every
record lives in a real on-disk database. Schema is private to this package
(prefixed ext_) — shared-Postgres persistence for team-wide deployments is a
separate integration (proposal in docs/agent-program/a5/session-1/) that must
go through Section 1's migration ownership.

One connection per operation keeps callers thread-safe without a pool; SQLite
handles the serialization and WAL keeps readers non-blocking.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import (
    Command,
    CustomTool,
    DiscoveredTool,
    HookDefinition,
    HookDelivery,
    PluginInstall,
    ServerRecord,
    SkillSummary,
    SkillVersion,
    ToolGrant,
    ToolPolicyRule,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ext_mcp_servers(
  server_id          TEXT PRIMARY KEY,
  team_id            TEXT NOT NULL,
  project_id         TEXT,
  name               TEXT NOT NULL,
  transport          TEXT NOT NULL DEFAULT 'http',
  url                TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'enabled',
  created_by         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  health             TEXT NOT NULL DEFAULT 'unknown',
  health_checked_at  TEXT,
  health_latency_ms  REAL,
  health_error       TEXT,
  tool_count         INTEGER NOT NULL DEFAULT 0,
  rejected_tool_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(team_id, name)
);
CREATE TABLE IF NOT EXISTS ext_mcp_tools(
  server_id        TEXT NOT NULL,
  name             TEXT NOT NULL,
  description      TEXT,
  input_schema     TEXT NOT NULL,
  callable         INTEGER NOT NULL DEFAULT 1,
  rejection_reason TEXT,
  discovered_at    TEXT NOT NULL,
  PRIMARY KEY(server_id, name),
  FOREIGN KEY(server_id) REFERENCES ext_mcp_servers(server_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS ext_tool_grants(
  grant_id   TEXT PRIMARY KEY,
  team_id    TEXT NOT NULL,
  server_id  TEXT NOT NULL,
  tool_name  TEXT NOT NULL,
  project_id TEXT,
  run_id     TEXT,
  expires_at TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active',
  revoked_at TEXT,
  revoked_by TEXT
);
CREATE TABLE IF NOT EXISTS ext_plugins(
  plugin_id    TEXT NOT NULL,
  team_id      TEXT NOT NULL,
  version      TEXT NOT NULL,
  digest       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'installed',
  installed_by TEXT NOT NULL,
  installed_at TEXT NOT NULL,
  updated_at   TEXT,
  PRIMARY KEY(plugin_id, team_id)
);
CREATE TABLE IF NOT EXISTS ext_custom_tools(
  custom_tool_id      TEXT PRIMARY KEY,
  team_id             TEXT NOT NULL,
  name                TEXT NOT NULL,
  description         TEXT,
  server_id           TEXT NOT NULL,
  source_tool         TEXT NOT NULL,
  allowed_arguments   TEXT,
  default_arguments   TEXT NOT NULL DEFAULT '{}',
  status              TEXT NOT NULL DEFAULT 'active',
  created_by          TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  UNIQUE(team_id, name)
);
CREATE TABLE IF NOT EXISTS ext_skills(
  skill_id       TEXT PRIMARY KEY,
  team_id        TEXT NOT NULL,
  name           TEXT NOT NULL,
  source         TEXT NOT NULL DEFAULT 'user',
  status         TEXT NOT NULL DEFAULT 'active',
  latest_version INTEGER NOT NULL DEFAULT 0,
  created_by     TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  UNIQUE(team_id, name)
);
CREATE TABLE IF NOT EXISTS ext_skill_versions(
  skill_id   TEXT NOT NULL,
  version    INTEGER NOT NULL,
  content    TEXT NOT NULL,
  digest     TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source_ref TEXT,
  PRIMARY KEY(skill_id, version),
  FOREIGN KEY(skill_id) REFERENCES ext_skills(skill_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS ext_commands(
  command_id  TEXT PRIMARY KEY,
  team_id     TEXT NOT NULL,
  namespace   TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT,
  template    TEXT NOT NULL,
  arguments   TEXT NOT NULL DEFAULT '[]',
  source      TEXT NOT NULL DEFAULT 'user',
  status      TEXT NOT NULL DEFAULT 'active',
  created_by  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE(team_id, name)
);
CREATE TABLE IF NOT EXISTS ext_hooks(
  hook_id    TEXT PRIMARY KEY,
  team_id    TEXT NOT NULL,
  name       TEXT NOT NULL,
  event_type TEXT NOT NULL,
  target     TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(team_id, name)
);
CREATE TABLE IF NOT EXISTS ext_hook_deliveries(
  delivery_id TEXT PRIMARY KEY,
  hook_id     TEXT NOT NULL,
  team_id     TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  status      TEXT NOT NULL,
  detail      TEXT,
  duration_ms REAL,
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ext_tool_policy(
  policy_id  TEXT PRIMARY KEY,
  team_id    TEXT NOT NULL,
  mode       TEXT NOT NULL,
  pattern    TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(team_id, mode, pattern)
);
"""


def _row_to_server(row: sqlite3.Row) -> ServerRecord:
    return ServerRecord(
        serverId=row["server_id"],
        teamId=row["team_id"],
        projectId=row["project_id"],
        name=row["name"],
        transport=row["transport"],
        url=row["url"],
        status=row["status"],
        createdBy=row["created_by"],
        createdAt=row["created_at"],
        health=row["health"],
        healthCheckedAt=row["health_checked_at"],
        healthLatencyMs=row["health_latency_ms"],
        healthError=row["health_error"],
        toolCount=row["tool_count"],
        rejectedToolCount=row["rejected_tool_count"],
    )


def _row_to_grant(row: sqlite3.Row) -> ToolGrant:
    return ToolGrant(
        grantId=row["grant_id"],
        teamId=row["team_id"],
        serverId=row["server_id"],
        toolName=row["tool_name"],
        projectId=row["project_id"],
        runId=row["run_id"],
        expiresAt=row["expires_at"],
        createdBy=row["created_by"],
        createdAt=row["created_at"],
        status=row["status"],
        revokedAt=row["revoked_at"],
        revokedBy=row["revoked_by"],
    )


class Store:
    """SQLite-backed registry store. Sync by design (sub-ms ops, no pool)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------- servers

    def create_server(self, record: ServerRecord) -> ServerRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_mcp_servers(
                     server_id, team_id, project_id, name, transport, url, status,
                     created_by, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    record.serverId,
                    record.teamId,
                    record.projectId,
                    record.name,
                    record.transport,
                    record.url,
                    record.status,
                    record.createdBy,
                    record.createdAt,
                ),
            )
        return record

    def get_server(self, team_id: str, server_id: str) -> ServerRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_mcp_servers WHERE server_id=? AND team_id=?",
                (server_id, team_id),
            ).fetchone()
        return _row_to_server(row) if row else None

    def get_server_any_team(self, server_id: str) -> ServerRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_mcp_servers WHERE server_id=?", (server_id,)
            ).fetchone()
        return _row_to_server(row) if row else None

    def find_server_by_name(self, team_id: str, name: str) -> ServerRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_mcp_servers WHERE team_id=? AND name=?",
                (team_id, name),
            ).fetchone()
        return _row_to_server(row) if row else None

    def list_servers(self, team_id: str) -> list[ServerRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_mcp_servers WHERE team_id=? ORDER BY created_at, server_id",
                (team_id,),
            ).fetchall()
        return [_row_to_server(r) for r in rows]

    def set_server_status(self, team_id: str, server_id: str, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ext_mcp_servers SET status=? WHERE server_id=? AND team_id=?",
                (status, server_id, team_id),
            )
        return cur.rowcount > 0

    def record_health(
        self,
        server_id: str,
        *,
        health: str,
        checked_at: str,
        latency_ms: float | None,
        error: str | None,
        tools: list[DiscoveredTool],
    ) -> None:
        """Replace the discovered tool set and health in one transaction.

        Discovery is a snapshot: tools the server no longer advertises are
        dropped, so a revoked/hijacked tool cannot linger in the registry.
        """
        callable_n = sum(1 for t in tools if t.callable)
        rejected_n = len(tools) - callable_n
        with self._connect() as conn:
            conn.execute("DELETE FROM ext_mcp_tools WHERE server_id=?", (server_id,))
            conn.executemany(
                """INSERT INTO ext_mcp_tools(
                     server_id, name, description, input_schema, callable,
                     rejection_reason, discovered_at)
                   VALUES(?,?,?,?,?,?,?)""",
                [
                    (
                        server_id,
                        t.name,
                        t.description,
                        json.dumps(t.inputSchema, separators=(",", ":")),
                        1 if t.callable else 0,
                        t.rejectionReason,
                        t.discoveredAt,
                    )
                    for t in tools
                ],
            )
            conn.execute(
                """UPDATE ext_mcp_servers SET health=?, health_checked_at=?,
                     health_latency_ms=?, health_error=?, tool_count=?,
                     rejected_tool_count=? WHERE server_id=?""",
                (health, checked_at, latency_ms, error, callable_n, rejected_n, server_id),
            )

    def list_tools(self, server_id: str) -> list[DiscoveredTool]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_mcp_tools WHERE server_id=? ORDER BY name",
                (server_id,),
            ).fetchall()
        return [
            DiscoveredTool(
                name=r["name"],
                description=r["description"],
                inputSchema=json.loads(r["input_schema"]),
                callable=bool(r["callable"]),
                rejectionReason=r["rejection_reason"],
                discoveredAt=r["discovered_at"],
            )
            for r in rows
        ]

    # -------------------------------------------------------------- grants

    def create_grant(self, grant: ToolGrant) -> ToolGrant:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_tool_grants(
                     grant_id, team_id, server_id, tool_name, project_id, run_id,
                     expires_at, created_by, created_at, status)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    grant.grantId,
                    grant.teamId,
                    grant.serverId,
                    grant.toolName,
                    grant.projectId,
                    grant.runId,
                    grant.expiresAt,
                    grant.createdBy,
                    grant.createdAt,
                    grant.status,
                ),
            )
        return grant

    def get_grant(self, team_id: str, grant_id: str) -> ToolGrant | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_tool_grants WHERE grant_id=? AND team_id=?",
                (grant_id, team_id),
            ).fetchone()
        return _row_to_grant(row) if row else None

    def list_grants(self, team_id: str) -> list[ToolGrant]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_tool_grants WHERE team_id=? ORDER BY created_at, grant_id",
                (team_id,),
            ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def find_active_grants(
        self, team_id: str, server_id: str, tool_name: str
    ) -> list[ToolGrant]:
        """Active grants matching server+tool (exact or wildcard), newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM ext_tool_grants
                   WHERE team_id=? AND server_id=? AND status='active'
                     AND (tool_name=? OR tool_name='*')
                   ORDER BY created_at DESC, grant_id DESC""",
                (team_id, server_id, tool_name),
            ).fetchall()
        return [_row_to_grant(r) for r in rows]

    def revoke_grant(self, team_id: str, grant_id: str, *, revoked_by: str, at: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE ext_tool_grants SET status='revoked', revoked_at=?, revoked_by=?
                   WHERE grant_id=? AND team_id=? AND status='active'""",
                (at, revoked_by, grant_id, team_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------- plugins

    def upsert_install(self, install: PluginInstall) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_plugins(
                     plugin_id, team_id, version, digest, status, installed_by,
                     installed_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(plugin_id, team_id) DO UPDATE SET
                     version=excluded.version, digest=excluded.digest,
                     status=excluded.status, installed_by=excluded.installed_by,
                     updated_at=excluded.updated_at""",
                (
                    install.pluginId,
                    install.teamId,
                    install.version,
                    install.digest,
                    install.status,
                    install.installedBy,
                    install.installedAt,
                    install.updatedAt,
                ),
            )

    def get_install(self, team_id: str, plugin_id: str) -> PluginInstall | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_plugins WHERE plugin_id=? AND team_id=?",
                (plugin_id, team_id),
            ).fetchone()
        if row is None:
            return None
        return PluginInstall(
            pluginId=row["plugin_id"],
            version=row["version"],
            digest=row["digest"],
            teamId=row["team_id"],
            status=row["status"],
            installedBy=row["installed_by"],
            installedAt=row["installed_at"],
            updatedAt=row["updated_at"],
        )

    def list_installs(self, team_id: str) -> list[PluginInstall]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_plugins WHERE team_id=? ORDER BY installed_at, plugin_id",
                (team_id,),
            ).fetchall()
        return [
            PluginInstall(
                pluginId=r["plugin_id"],
                version=r["version"],
                digest=r["digest"],
                teamId=r["team_id"],
                status=r["status"],
                installedBy=r["installed_by"],
                installedAt=r["installed_at"],
                updatedAt=r["updated_at"],
            )
            for r in rows
        ]

    def set_install_status(self, team_id: str, plugin_id: str, status: str, at: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ext_plugins SET status=?, updated_at=? WHERE plugin_id=? AND team_id=?",
                (status, at, plugin_id, team_id),
            )
        return cur.rowcount > 0

    # -------------------------------------------------------- custom tools

    def create_custom_tool(self, tool: CustomTool) -> CustomTool:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_custom_tools(
                     custom_tool_id, team_id, name, description, server_id,
                     source_tool, allowed_arguments, default_arguments, status,
                     created_by, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tool.customToolId,
                    tool.teamId,
                    tool.name,
                    tool.description,
                    tool.serverId,
                    tool.sourceTool,
                    json.dumps(tool.allowedArguments)
                    if tool.allowedArguments is not None
                    else None,
                    json.dumps(tool.defaultArguments, separators=(",", ":")),
                    tool.status,
                    tool.createdBy,
                    tool.createdAt,
                ),
            )
        return tool

    def get_custom_tool(self, team_id: str, custom_tool_id: str) -> CustomTool | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_custom_tools WHERE custom_tool_id=? AND team_id=?",
                (custom_tool_id, team_id),
            ).fetchone()
        return _row_to_custom_tool(row) if row else None

    def list_custom_tools(self, team_id: str) -> list[CustomTool]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_custom_tools WHERE team_id=? ORDER BY name",
                (team_id,),
            ).fetchall()
        return [_row_to_custom_tool(r) for r in rows]

    def set_custom_tool_status(self, team_id: str, custom_tool_id: str, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ext_custom_tools SET status=? WHERE custom_tool_id=? AND team_id=?",
                (status, custom_tool_id, team_id),
            )
        return cur.rowcount > 0

    # -------------------------------------------------------------- skills

    def create_skill(self, skill: SkillSummary) -> SkillSummary:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_skills(
                     skill_id, team_id, name, source, status, latest_version,
                     created_by, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    skill.skillId,
                    skill.teamId,
                    skill.name,
                    skill.source,
                    skill.status,
                    skill.latestVersion,
                    skill.createdBy,
                    skill.createdAt,
                    skill.updatedAt,
                ),
            )
        return skill

    def get_skill(self, team_id: str, skill_id: str) -> SkillSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_skills WHERE skill_id=? AND team_id=?",
                (skill_id, team_id),
            ).fetchone()
        if row is None:
            return None
        return _row_to_skill(row)

    def find_skill_by_name(self, team_id: str, name: str) -> SkillSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_skills WHERE team_id=? AND name=?",
                (team_id, name),
            ).fetchone()
        return _row_to_skill(row) if row else None

    def list_skills(self, team_id: str, *, include_removed: bool = False) -> list[SkillSummary]:
        q = "SELECT * FROM ext_skills WHERE team_id=?"
        if not include_removed:
            q += " AND status='active'"
        q += " ORDER BY name"
        with self._connect() as conn:
            rows = conn.execute(q, (team_id,)).fetchall()
        return [_row_to_skill(r) for r in rows]

    def add_skill_version(self, version: SkillVersion, *, updated_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_skill_versions(
                     skill_id, version, content, digest, created_by, created_at, source_ref)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    version.skillId,
                    version.version,
                    version.content,
                    version.digest,
                    version.createdBy,
                    version.createdAt,
                    version.sourceRef,
                ),
            )
            conn.execute(
                "UPDATE ext_skills SET latest_version=?, updated_at=? WHERE skill_id=?",
                (version.version, updated_at, version.skillId),
            )

    def list_skill_versions(self, skill_id: str) -> list[SkillVersion]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_skill_versions WHERE skill_id=? ORDER BY version DESC",
                (skill_id,),
            ).fetchall()
        return [
            SkillVersion(
                skillId=r["skill_id"],
                version=r["version"],
                content=r["content"],
                digest=r["digest"],
                createdBy=r["created_by"],
                createdAt=r["created_at"],
                sourceRef=r["source_ref"],
            )
            for r in rows
        ]

    def count_skill_versions(self, skill_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ext_skill_versions WHERE skill_id=?",
                (skill_id,),
            ).fetchone()
        return int(row["n"])

    def set_skill_status(self, skill_id: str, status: str, *, updated_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE ext_skills SET status=?, updated_at=? WHERE skill_id=?",
                (status, updated_at, skill_id),
            )

    def skills_by_source(self, team_id: str, source: str) -> list[SkillSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_skills WHERE team_id=? AND source=? ORDER BY name",
                (team_id, source),
            ).fetchall()
        return [_row_to_skill(r) for r in rows]

    # ------------------------------------------------------------ commands

    def create_command(self, command: Command) -> Command:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_commands(
                     command_id, team_id, namespace, name, description, template,
                     arguments, source, status, created_by, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    command.commandId,
                    command.teamId,
                    command.namespace,
                    command.name,
                    command.description,
                    command.template,
                    json.dumps(command.arguments),
                    command.source,
                    command.status,
                    command.createdBy,
                    command.createdAt,
                    command.updatedAt,
                ),
            )
        return command

    def get_command(self, team_id: str, command_id: str) -> Command | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_commands WHERE command_id=? AND team_id=?",
                (command_id, team_id),
            ).fetchone()
        return _row_to_command(row) if row else None

    def find_command_by_name(self, team_id: str, name: str) -> Command | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_commands WHERE team_id=? AND name=?",
                (team_id, name),
            ).fetchone()
        return _row_to_command(row) if row else None

    def list_commands(self, team_id: str, *, include_removed: bool = False) -> list[Command]:
        q = "SELECT * FROM ext_commands WHERE team_id=?"
        if not include_removed:
            q += " AND status='active'"
        q += " ORDER BY name"
        with self._connect() as conn:
            rows = conn.execute(q, (team_id,)).fetchall()
        return [_row_to_command(r) for r in rows]

    def set_command_status(self, team_id: str, command_id: str, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ext_commands SET status=? WHERE command_id=? AND team_id=?",
                (status, command_id, team_id),
            )
        return cur.rowcount > 0

    # --------------------------------------------------------------- hooks

    def create_hook(self, hook: HookDefinition) -> HookDefinition:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_hooks(
                     hook_id, team_id, name, event_type, target, status,
                     created_by, created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    hook.hookId,
                    hook.teamId,
                    hook.name,
                    hook.eventType,
                    json.dumps(hook.target),
                    hook.status,
                    hook.createdBy,
                    hook.createdAt,
                ),
            )
        return hook

    def get_hook(self, team_id: str, hook_id: str) -> HookDefinition | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_hooks WHERE hook_id=? AND team_id=?",
                (hook_id, team_id),
            ).fetchone()
        return _row_to_hook(row) if row else None

    def find_hook_by_name(self, team_id: str, name: str) -> HookDefinition | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ext_hooks WHERE team_id=? AND name=?",
                (team_id, name),
            ).fetchone()
        return _row_to_hook(row) if row else None

    def list_hooks(self, team_id: str, *, include_disabled: bool = False) -> list[HookDefinition]:
        q = "SELECT * FROM ext_hooks WHERE team_id=?"
        if not include_disabled:
            q += " AND status='active'"
        q += " ORDER BY name"
        with self._connect() as conn:
            rows = conn.execute(q, (team_id,)).fetchall()
        return [_row_to_hook(r) for r in rows]

    def set_hook_status(self, team_id: str, hook_id: str, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE ext_hooks SET status=? WHERE hook_id=? AND team_id=?",
                (status, hook_id, team_id),
            )
        return cur.rowcount > 0

    def add_delivery(self, delivery: HookDelivery) -> HookDelivery:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_hook_deliveries(
                     delivery_id, hook_id, team_id, event_type, status, detail,
                     duration_ms, created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    delivery.deliveryId,
                    delivery.hookId,
                    delivery.teamId,
                    delivery.eventType,
                    delivery.status,
                    delivery.detail,
                    delivery.durationMs,
                    delivery.createdAt,
                ),
            )
        return delivery

    def list_deliveries(self, team_id: str, hook_id: str, *, limit: int = 50) -> list[HookDelivery]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM ext_hook_deliveries
                   WHERE team_id=? AND hook_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (team_id, hook_id, limit),
            ).fetchall()
        return [_row_to_delivery(r) for r in rows]

    # -------------------------------------------------------------- policy

    def add_policy_rule(self, rule: ToolPolicyRule) -> ToolPolicyRule:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ext_tool_policy(
                     policy_id, team_id, mode, pattern, created_by, created_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    rule.policyId,
                    rule.teamId,
                    rule.mode,
                    rule.pattern,
                    rule.createdBy,
                    rule.createdAt,
                ),
            )
        return rule

    def list_policy_rules(self, team_id: str) -> list[ToolPolicyRule]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ext_tool_policy WHERE team_id=? ORDER BY mode, pattern",
                (team_id,),
            ).fetchall()
        return [_row_to_policy(r) for r in rows]

    def delete_policy_rule(self, team_id: str, policy_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM ext_tool_policy WHERE policy_id=? AND team_id=?",
                (policy_id, team_id),
            )
        return cur.rowcount > 0


def _row_to_command(row: sqlite3.Row) -> Command:
    return Command(
        commandId=row["command_id"],
        teamId=row["team_id"],
        namespace=row["namespace"],
        name=row["name"],
        description=row["description"],
        template=row["template"],
        arguments=json.loads(row["arguments"]),
        source=row["source"],
        status=row["status"],
        createdBy=row["created_by"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _row_to_hook(row: sqlite3.Row) -> HookDefinition:
    return HookDefinition(
        hookId=row["hook_id"],
        teamId=row["team_id"],
        name=row["name"],
        eventType=row["event_type"],
        target=json.loads(row["target"]),
        status=row["status"],
        createdBy=row["created_by"],
        createdAt=row["created_at"],
    )


def _row_to_delivery(row: sqlite3.Row) -> HookDelivery:
    return HookDelivery(
        deliveryId=row["delivery_id"],
        hookId=row["hook_id"],
        teamId=row["team_id"],
        eventType=row["event_type"],
        status=row["status"],
        detail=row["detail"],
        durationMs=row["duration_ms"],
        createdAt=row["created_at"],
    )


def _row_to_policy(row: sqlite3.Row) -> ToolPolicyRule:
    return ToolPolicyRule(
        policyId=row["policy_id"],
        teamId=row["team_id"],
        mode=row["mode"],
        pattern=row["pattern"],
        createdBy=row["created_by"],
        createdAt=row["created_at"],
    )


def _row_to_skill(row: sqlite3.Row) -> SkillSummary:
    return SkillSummary(
        skillId=row["skill_id"],
        teamId=row["team_id"],
        name=row["name"],
        source=row["source"],
        status=row["status"],
        latestVersion=row["latest_version"],
        createdBy=row["created_by"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _row_to_custom_tool(row: sqlite3.Row) -> CustomTool:
    allowed = row["allowed_arguments"]
    return CustomTool(
        customToolId=row["custom_tool_id"],
        teamId=row["team_id"],
        name=row["name"],
        description=row["description"],
        serverId=row["server_id"],
        sourceTool=row["source_tool"],
        allowedArguments=json.loads(allowed) if allowed is not None else None,
        defaultArguments=json.loads(row["default_arguments"]),
        status=row["status"],
        createdBy=row["created_by"],
        createdAt=row["created_at"],
    )
