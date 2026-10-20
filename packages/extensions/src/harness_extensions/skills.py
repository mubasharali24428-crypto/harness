"""Versioned skills / playbooks.

A skill is a named, bounded piece of reusable operational text (a playbook):
prompt guidance, a checklist, a runbook the agent can follow. Every write is
a NEW immutable version with a sha256 digest — nothing is overwritten, so a
review can always say exactly which content a run used.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime

from .config import Settings
from .models import SkillSummary, SkillVersion
from .store import Store

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")


class SkillError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def validate_skill_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise SkillError(
            "SKILL_NAME_INVALID",
            "name must be lowercase [a-z0-9._/-], start alphanumeric, max 128 chars",
        )


def validate_skill_content(content: str, settings: Settings) -> None:
    if not content or not content.strip():
        raise SkillError("SKILL_CONTENT_EMPTY", "skill content must be non-empty")
    if len(content.encode("utf-8")) > settings.max_skill_bytes:
        raise SkillError(
            "SKILL_CONTENT_TOO_LARGE",
            f"skill content exceeds {settings.max_skill_bytes} bytes",
        )


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def create_skill(
    store: Store,
    settings: Settings,
    *,
    team_id: str,
    name: str,
    content: str,
    created_by: str,
    source: str = "user",
    source_ref: str | None = None,
) -> tuple[SkillSummary, SkillVersion]:
    validate_skill_name(name)
    validate_skill_content(content, settings)
    if store.find_skill_by_name(team_id, name) is not None:
        raise SkillError("SKILL_NAME_CONFLICT", f"skill {name!r} already exists in this team")
    now = _now_iso()
    skill = store.create_skill(
        SkillSummary(
            skillId=str(uuid.uuid4()),
            teamId=team_id,
            name=name,
            source=source,
            status="active",
            latestVersion=0,
            createdBy=created_by,
            createdAt=now,
            updatedAt=now,
        )
    )
    version = store_add_version(
        store,
        settings,
        skill=skill,
        content=content,
        created_by=created_by,
        source_ref=source_ref,
    )
    return store.get_skill(team_id, skill.skillId) or skill, version


def store_add_version(
    store: Store,
    settings: Settings,
    *,
    skill: SkillSummary,
    content: str,
    created_by: str,
    source_ref: str | None = None,
) -> SkillVersion:
    validate_skill_content(content, settings)
    if store.count_skill_versions(skill.skillId) >= settings.max_skill_versions:
        raise SkillError(
            "SKILL_VERSION_LIMIT",
            f"skill already has {settings.max_skill_versions} versions",
        )
    version = SkillVersion(
        skillId=skill.skillId,
        version=skill.latestVersion + 1,
        content=content,
        digest=content_digest(content),
        createdBy=created_by,
        createdAt=_now_iso(),
        sourceRef=source_ref,
    )
    store.add_skill_version(version, updated_at=version.createdAt)
    return version


def add_skill_version(
    store: Store,
    settings: Settings,
    *,
    team_id: str,
    skill_id: str,
    content: str,
    created_by: str,
) -> SkillVersion:
    skill = store.get_skill(team_id, skill_id)
    if skill is None:
        raise SkillError("SKILL_NOT_FOUND", "no such skill")
    if skill.status != "active":
        raise SkillError("SKILL_REMOVED", "skill has been removed")
    return store_add_version(
        store, settings, skill=skill, content=content, created_by=created_by
    )


def get_skill_version(
    store: Store, *, team_id: str, skill_id: str, version: int | None
) -> tuple[SkillSummary, SkillVersion]:
    skill = store.get_skill(team_id, skill_id)
    if skill is None:
        raise SkillError("SKILL_NOT_FOUND", "no such skill")
    if skill.status != "active":
        raise SkillError("SKILL_REMOVED", "skill has been removed")
    versions = store.list_skill_versions(skill.skillId)
    if version is not None:
        versions = [v for v in versions if v.version == version]
    if not versions:
        raise SkillError("SKILL_VERSION_NOT_FOUND", "no such skill version")
    return skill, versions[0]
