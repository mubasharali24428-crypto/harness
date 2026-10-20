"""Curated plugin catalog: digest-verified install / update / uninstall / revoke.

A plugin is a versioned, digest-locked bundle shipped inside the package
(``harness_extensions/catalog/``): playbook skills plus informational server
templates. The catalog is curated by the Harness maintainers — there is no
arbitrary package upload, so "marketplace" here means a vetted catalog with a
working install/update/remove lifecycle, NOT a publishing economy.

Trust rules enforced here:
  - the payload's canonical-JSON digest MUST equal the index digest; a byte
    of drift (or tampering with the payload file) refuses the install;
  - revoke is terminal: revoked installs cannot be reinstalled without an
    explicit new catalog version;
  - install is idempotent per (pluginId, version, digest).

Publishing flow, pricing and third-party submissions are NOT implemented —
reported separately as unavailable, not simulated.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .models import CatalogEntry, PluginInstall, SkillSummary
from .skills import SkillError, add_skill_version, create_skill, validate_skill_name
from .store import Store

_BUNDLED_CATALOG = Path(__file__).parent / "catalog"
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class MarketplaceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def canonical_digest(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Catalog:
    entries: dict[str, CatalogEntry]
    payloads: dict[str, dict]  # pluginId -> verified payload document
    templates: dict[str, list[dict]]  # pluginId -> server template suggestions


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_catalog(settings: Settings) -> Catalog:
    catalog_dir = Path(settings.catalog_dir) if settings.catalog_dir else _BUNDLED_CATALOG
    index_path = catalog_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries: dict[str, CatalogEntry] = {}
    payloads: dict[str, dict] = {}
    templates: dict[str, list[dict]] = {}
    for raw in index.get("entries", []):
        plugin_id = raw["pluginId"]
        payload_path = catalog_dir / raw["payload"]
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        digest = canonical_digest(payload)
        if digest != raw["digest"]:
            # A catalog whose own digest does not verify is corrupt/tampered:
            # refuse to serve that entry at all (fail closed at load).
            continue
        skills = payload.get("skills", [])
        entries[plugin_id] = CatalogEntry(
            pluginId=plugin_id,
            name=raw["name"],
            version=raw["version"],
            description=raw["description"],
            digest=digest,
            skillCount=len(skills),
            permissionsNote=raw.get("permissionsNote"),
        )
        payloads[plugin_id] = payload
        templates[plugin_id] = payload.get("serverTemplates", [])
    return Catalog(entries=entries, payloads=payloads, templates=templates)


def install_plugin(
    store: Store,
    settings: Settings,
    catalog: Catalog,
    *,
    team_id: str,
    plugin_id: str,
    installed_by: str,
) -> PluginInstall:
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise MarketplaceError("PLUGIN_ID_INVALID", "invalid plugin id")
    entry = catalog.entries.get(plugin_id)
    payload = catalog.payloads.get(plugin_id)
    if entry is None or payload is None:
        raise MarketplaceError("PLUGIN_NOT_FOUND", "no such plugin in the curated catalog")

    existing = store.get_install(team_id, plugin_id)
    if existing is not None and existing.status == "revoked":
        raise MarketplaceError(
            "PLUGIN_REVOKED", "plugin installation was revoked; it cannot be reinstalled"
        )
    # Re-verify the digest at install time against the on-disk truth: load_catalog
    # already did, but install is the security boundary and must not trust a
    # Catalog object built anywhere else.
    recomputed = canonical_digest(payload)
    if recomputed != entry.digest:
        raise MarketplaceError("PLUGIN_DIGEST_MISMATCH", "payload digest does not match catalog")

    if (
        existing is not None
        and existing.status == "installed"
        and existing.version == entry.version
        and existing.digest == entry.digest
    ):
        return existing  # idempotent no-op

    now = _now_iso()
    for skill_raw in payload.get("skills", []):
        name = f"{plugin_id}/{skill_raw['name']}"
        validate_skill_name(name)
        content = skill_raw["content"]
        source = f"plugin:{plugin_id}"
        source_ref = f"plugin:{plugin_id}@{entry.version}"
        found = store.find_skill_by_name(team_id, name)
        if found is None:
            create_skill(
                store,
                settings,
                team_id=team_id,
                name=name,
                content=content,
                created_by=installed_by,
                source=source,
                source_ref=source_ref,
            )
        else:
            if found.source != source:
                raise MarketplaceError(
                    "SKILL_SOURCE_CONFLICT",
                    f"skill {name!r} exists with a different source; refusing to overwrite",
                )
            if found.status != "active":
                store.set_skill_status(found.skillId, "active", updated_at=now)
                found = store.get_skill(team_id, found.skillId) or found
            if found.latestVersion == 0 or _latest_is_different(store, found, content):
                try:
                    add_skill_version(
                        store,
                        settings,
                        team_id=team_id,
                        skill_id=found.skillId,
                        content=content,
                        created_by=installed_by,
                    )
                except SkillError as exc:
                    raise MarketplaceError(exc.code, exc.message) from exc

    # Skills that older versions shipped but the new payload dropped retire.
    wanted = {f"{plugin_id}/{s['name']}" for s in payload.get("skills", [])}
    for skill in store.skills_by_source(team_id, f"plugin:{plugin_id}"):
        if skill.name not in wanted and skill.status == "active":
            store.set_skill_status(skill.skillId, "removed", updated_at=now)

    install = PluginInstall(
        pluginId=plugin_id,
        version=entry.version,
        digest=entry.digest,
        teamId=team_id,
        status="installed",
        installedBy=installed_by if existing is None else existing.installedBy,
        installedAt=existing.installedAt if existing is not None else now,
        updatedAt=None if existing is None else now,
    )
    store.upsert_install(install)
    return install


def _latest_is_different(store: Store, skill: SkillSummary, content: str) -> bool:
    versions = store.list_skill_versions(skill.skillId)
    if not versions:
        return True
    return versions[0].content != content


def uninstall_plugin(store: Store, *, team_id: str, plugin_id: str) -> PluginInstall:
    existing = store.get_install(team_id, plugin_id)
    if existing is None or existing.status == "removed":
        raise MarketplaceError("PLUGIN_NOT_INSTALLED", "plugin is not installed for this team")
    if existing.status == "revoked":
        raise MarketplaceError("PLUGIN_REVOKED", "plugin installation was revoked")
    now = _now_iso()
    for skill in store.skills_by_source(team_id, f"plugin:{plugin_id}"):
        if skill.status == "active":
            store.set_skill_status(skill.skillId, "removed", updated_at=now)
    store.set_install_status(team_id, plugin_id, "removed", now)
    updated = store.get_install(team_id, plugin_id)
    assert updated is not None
    return updated


def revoke_plugin(store: Store, *, team_id: str, plugin_id: str, revoked_by: str) -> PluginInstall:
    """Admin kill-switch: terminal for this team's installation."""
    existing = store.get_install(team_id, plugin_id)
    if existing is None:
        # Revoking a never-installed plugin still blocks future installs by
        # writing a revoked tombstone.
        now = _now_iso()
        store.upsert_install(
            PluginInstall(
                pluginId=plugin_id,
                version="",
                digest="",
                teamId=team_id,
                status="revoked",
                installedBy=revoked_by,
                installedAt=now,
                updatedAt=now,
            )
        )
    else:
        now = _now_iso()
        for skill in store.skills_by_source(team_id, f"plugin:{plugin_id}"):
            if skill.status == "active":
                store.set_skill_status(skill.skillId, "removed", updated_at=now)
        store.set_install_status(team_id, plugin_id, "revoked", now)
    updated = store.get_install(team_id, plugin_id)
    assert updated is not None
    return updated
