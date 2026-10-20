"""Namespaced slash-style commands: parameterized team playbook templates.

A command is a named template (``team/<slug>``) with declared arguments and
``{{placeholder}}`` substitution. Invocation renders the template, validates
arguments strictly (missing and unknown both rejected — no silent blanks),
and returns the rendered text plus a digest for audit.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime

from .models import Command
from .store import Store

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_-]+)\s*\}\}")


class CommandError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def create_command(
    store: Store,
    *,
    team_id: str,
    name: str,
    template: str,
    arguments: list[str] | None = None,
    description: str | None = None,
    created_by: str,
    source: str = "user",
    namespace: str = "team",
) -> Command:
    if not _SLUG_RE.match(name):
        raise CommandError(
            "COMMAND_NAME_INVALID",
            "name must start alphanumeric and contain only lowercase letters, digits, dashes",
        )
    declared = set(arguments or [])
    for arg in declared:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", arg):
            raise CommandError("COMMAND_ARGS_INVALID", f"argument name {arg!r} is invalid")
    used = set(_PLACEHOLDER_RE.findall(template))
    undeclared = sorted(used - declared)
    if undeclared:
        raise CommandError(
            "COMMAND_TEMPLATE_INVALID",
            f"template uses undeclared argument(s): {', '.join(undeclared)}",
        )
    full_name = f"{namespace}/{name}"
    if store.find_command_by_name(team_id, full_name) is not None:
        raise CommandError("COMMAND_NAME_CONFLICT", f"command {full_name!r} already exists")
    now = _now_iso()
    command = Command(
        commandId=str(uuid.uuid4()),
        teamId=team_id,
        namespace=namespace,
        name=full_name,
        description=description,
        template=template,
        arguments=sorted(declared),
        source=source,
        status="active",
        createdBy=created_by,
        createdAt=now,
        updatedAt=now,
    )
    return store.create_command(command)


def invoke_command(
    store: Store, *, team_id: str, command_id: str, arguments: dict
) -> tuple[Command, str]:
    command = store.get_command(team_id, command_id)
    if command is None or command.status != "active":
        raise CommandError("COMMAND_NOT_FOUND", "no such command")
    missing = [a for a in command.arguments if a not in arguments]
    if missing:
        raise CommandError("COMMAND_ARGS_MISSING", f"missing argument(s): {', '.join(missing)}")
    unknown = sorted(set(arguments) - set(command.arguments))
    if unknown:
        raise CommandError("COMMAND_ARGS_UNKNOWN", f"unknown argument(s): {', '.join(unknown)}")
    rendered = _PLACEHOLDER_RE.sub(
        lambda m: str(arguments.get(m.group(1), "")), command.template
    )
    return command, rendered


def rendered_digest(rendered: str) -> str:
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
