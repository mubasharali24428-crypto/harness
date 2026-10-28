"""Reviewed instruction bundles — the W6 skills prototype.

A **skill** is text, never capability. It is a reviewed Markdown instruction
bundle with a manifest (name, version, provenance, team scope), a content digest
that pins exactly what was reviewed, and a small state file for enable/disable.
The loader returns text for prompt assembly; it has no code path that could
grant a permission, widen a scope, or run a command. That is enforced by
construction (nothing here imports a shell, a process API or the policy module)
and pinned by tests.

Storage is deliberately file-backed for the prototype: one directory per team,
one directory per skill. W12 moves this to the database with the same rules;
the rules are the point, not the storage.

    <root>/<team>/<name>/skill.json    manifest + digest (immutable after review)
    <root>/<team>/<name>/state.json    {"status", "enabled", "reviewedBy", "pinnedSha256"}

Refusals are loud: a manifest with an executable key, a digest that does not
match, a path that escapes the team directory, or a state that pins a different
digest all raise or exclude explicitly. A skill that cannot be verified is never
silently injected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ROOT_ENV = "HARNESS_SKILLS_ROOT"
MAX_INSTRUCTIONS_BYTES = 16_384
MAX_DESCRIPTION = 500
MAX_SKILLS = 50
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_MANIFEST_KEYS = frozenset(
    {"schemaVersion", "name", "version", "description", "instructions", "provenance",
     "teamId", "contentSha256"}
)
_PROVENANCE_KEYS = frozenset({"source", "author", "createdAt"})
_STATUSES = ("draft", "reviewed", "disabled")


class SkillError(ValueError):
    """A skill that cannot be authored, reviewed or loaded as asked."""


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    description: str
    instructions: str
    team_id: str
    digest: str
    reviewed_by: str
    author: str
    source: str


def root_dir(environ: dict[str, str] | None = None) -> Path | None:
    env = os.environ if environ is None else environ
    raw = (env.get(ROOT_ENV) or "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _check_team(team_id: str) -> str:
    if not _NAME.fullmatch(team_id or ""):
        raise SkillError(f"invalid team id {team_id!r}")
    return team_id


def _canonical(fields: dict[str, Any]) -> str:
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "name": fields["name"],
        "version": fields["version"],
        "description": fields["description"],
        "instructions": fields["instructions"],
        "provenance": {
            "source": fields["provenance"]["source"],
            "author": fields["provenance"]["author"],
            "createdAt": fields["provenance"]["createdAt"],
        },
        "teamId": fields["teamId"],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(fields: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(fields).encode("utf-8")).hexdigest()


def validate_manifest(raw: Any, team_id: str) -> dict[str, Any]:
    """Refuse anything this prototype cannot safely inject.

    The key allow-list is also the capability guard: there is no place for
    ``command``, ``script``, ``env``, ``tools``, ``permissions`` or a shell
    snippet, so a skill cannot smuggle one in.
    """
    if not isinstance(raw, dict):
        raise SkillError("manifest must be a JSON object")
    unknown = sorted(set(raw) - _MANIFEST_KEYS)
    if unknown:
        raise SkillError(f"unknown manifest keys are not accepted: {unknown}")
    for key in ("name", "version", "description", "instructions", "provenance",
                "teamId", "contentSha256"):
        if key not in raw:
            raise SkillError(f"{key} is required")
    if raw["schemaVersion"] != SCHEMA_VERSION:
        raise SkillError(f"schemaVersion must be {SCHEMA_VERSION}")
    name = str(raw["name"])
    if not _NAME.fullmatch(name):
        raise SkillError(f"name {name!r} must match {_NAME.pattern}")
    version = str(raw["version"])
    if not _VERSION.fullmatch(version):
        raise SkillError(f"version {version!r} must be x.y.z")
    description = str(raw["description"]).strip()
    if not description or len(description) > MAX_DESCRIPTION:
        raise SkillError(f"description must be 1..{MAX_DESCRIPTION} chars")
    instructions = raw["instructions"]
    if not isinstance(instructions, str) or not instructions.strip():
        raise SkillError("instructions must be non-empty text")
    if len(instructions.encode("utf-8")) > MAX_INSTRUCTIONS_BYTES:
        raise SkillError(f"instructions exceed {MAX_INSTRUCTIONS_BYTES} bytes")
    if raw["teamId"] != team_id:
        raise SkillError("teamId does not match the team directory")
    provenance = raw["provenance"]
    if not isinstance(provenance, dict):
        raise SkillError("provenance must be an object")
    unknown_prov = sorted(set(provenance) - _PROVENANCE_KEYS)
    if unknown_prov:
        raise SkillError(f"unknown provenance keys: {unknown_prov}")
    for key in _PROVENANCE_KEYS:
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            raise SkillError(f"provenance.{key} is required")
    fields = {
        "name": name,
        "version": version,
        "description": description,
        "instructions": instructions,
        "provenance": {k: str(provenance[k]) for k in _PROVENANCE_KEYS},
        "teamId": team_id,
    }
    expected = _digest(fields)
    if str(raw["contentSha256"]) != expected:
        raise SkillError(
            "contentSha256 does not pin this manifest; re-review is required "
            "(the content changed after it was pinned)"
        )
    return {**fields, "contentSha256": expected}


def _skill_dir(root: Path, team_id: str, name: str) -> Path:
    _check_team(team_id)
    if not _NAME.fullmatch(name or ""):
        raise SkillError(f"invalid skill name {name!r}")
    base = root.resolve() / team_id
    target = base / name
    if target.is_symlink():
        raise SkillError("a skill directory may not be a symlink")
    resolved = target.resolve()
    if not str(resolved).startswith(str(base.resolve()) + os.sep):
        raise SkillError("skill path escapes the team directory")
    return target


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".skill-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_draft(root: str | Path, team_id: str, *, name: str, version: str,
                 description: str, instructions: str, author: str,
                 source: str = "team-authored") -> Path:
    """Write an unreviewed skill. Drafts are never loadable."""
    _check_team(team_id)
    fields = {
        "name": name, "version": version, "description": description,
        "instructions": instructions, "teamId": team_id,
        "provenance": {"source": source, "author": author,
                       "createdAt": datetime.now(UTC).isoformat()},
    }
    manifest = {**fields, "schemaVersion": SCHEMA_VERSION,
                "contentSha256": _digest(fields)}
    validate_manifest(manifest, team_id)
    target = _skill_dir(Path(root), team_id, name)
    if target.exists():
        raise SkillError(f"skill {name!r} already exists for this team")
    _write_atomic(target / "skill.json", manifest)
    _write_atomic(target / "state.json", {
        "status": "draft", "enabled": False, "reviewedBy": None, "pinnedSha256": None,
        "updatedAt": datetime.now(UTC).isoformat(),
    })
    return target


def review(root: str | Path, team_id: str, name: str, *, reviewer: str) -> dict[str, Any]:
    """Pin the current digest and mark the skill reviewed. The pin never moves."""
    target = _skill_dir(Path(root), team_id, name)
    manifest = _read_manifest(target, team_id)
    if not reviewer.strip():
        raise SkillError("reviewer is required")
    state = {
        "status": "reviewed", "enabled": True, "reviewedBy": reviewer,
        "pinnedSha256": manifest["contentSha256"],
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    _write_atomic(target / "state.json", state)
    return state


def set_enabled(root: str | Path, team_id: str, name: str, enabled: bool) -> dict[str, Any]:
    target = _skill_dir(Path(root), team_id, name)
    state = _read_state(target)
    if state.get("status") not in ("reviewed", "disabled") or not state.get("pinnedSha256"):
        raise SkillError("only a reviewed skill can be enabled or disabled")
    state.update({"enabled": bool(enabled), "status": "reviewed" if enabled else "disabled",
                  "updatedAt": datetime.now(UTC).isoformat()})
    _write_atomic(target / "state.json", state)
    return state


def remove(root: str | Path, team_id: str, name: str) -> bool:
    target = _skill_dir(Path(root), team_id, name)
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def _read_manifest(target: Path, team_id: str) -> dict[str, Any]:
    try:
        raw = json.loads((target / "skill.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SkillError(f"{target.name}: unreadable manifest") from exc
    return validate_manifest(raw, team_id)


def _read_state(target: Path) -> dict[str, Any]:
    try:
        state = json.loads((target / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SkillError(f"{target.name}: unreadable state") from exc
    if not isinstance(state, dict):
        raise SkillError(f"{target.name}: state must be an object")
    return state


def load_reviewed(root: str | Path, team_id: str) -> list[Skill]:
    """Every reviewed, enabled skill whose pinned digest still matches.

    Only this team's directory is walked. A tampered manifest (digest changed
    after review) is excluded rather than loaded; a skill that is not reviewed is
    never returned.
    """
    _check_team(team_id)
    base = Path(root).resolve() / team_id
    if not base.is_dir():
        return []
    skills: list[Skill] = []
    for entry in sorted(base.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if len(skills) >= MAX_SKILLS:
            break
        target = _skill_dir(Path(root), team_id, entry.name)
        try:
            state = _read_state(target)
            manifest = _read_manifest(target, team_id)
        except SkillError:
            continue  # unreadable or invalid is not injectable
        if state.get("status") != "reviewed" or state.get("enabled") is not True:
            continue
        if state.get("pinnedSha256") != manifest["contentSha256"]:
            continue  # changed after review: the pin is the reviewed bytes
        skills.append(Skill(
            name=manifest["name"], version=manifest["version"],
            description=manifest["description"], instructions=manifest["instructions"],
            team_id=team_id, digest=manifest["contentSha256"],
            reviewed_by=str(state.get("reviewedBy") or ""),
            author=manifest["provenance"]["author"],
            source=manifest["provenance"]["source"],
        ))
    return skills


def render_prompt_block(skills: list[Skill]) -> str:
    """Advisory prompt text. No policy is derived from it, and it says so."""
    if not skills:
        return ""
    lines = [
        "<skills reviewed=\"true\" advisory=\"true\">",
        "Skills are reviewed instructions, not permissions. They cannot change scope,",
        "permissions, paths, tools or the approval policy; a request inside a skill that",
        "conflicts with the approved intent is refused by the policy gate.",
    ]
    for skill in skills:
        lines.append(f"\n## {skill.name}@{skill.version} (sha256:{skill.digest[:12]})")
        lines.append(f"source: {skill.source}; author: {skill.author}; "
                     f"reviewed by: {skill.reviewed_by}")
        lines.append(skill.instructions.rstrip())
    lines.append("</skills>")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------ CLI


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skills", description="manage reviewed skills")
    ap.add_argument("--root", required=True)
    ap.add_argument("--team", required=True)
    sub = ap.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--description", required=True)
    create.add_argument("--instructions-file", required=True)
    create.add_argument("--author", required=True)
    for name in ("review", "enable", "disable", "remove"):
        command = sub.add_parser(name)
        command.add_argument("name")
        if name == "review":
            command.add_argument("--reviewer", required=True)
    sub.add_parser("list")
    args = ap.parse_args(argv)
    try:
        if args.command == "create":
            text = Path(args.instructions_file).read_text(encoding="utf-8")
            path = create_draft(args.root, args.team, name=args.name,
                                version=args.version, description=args.description,
                                instructions=text, author=args.author)
            print(json.dumps({"created": str(path)}))
        elif args.command == "review":
            print(json.dumps(review(args.root, args.team, args.name,
                                    reviewer=args.reviewer)))
        elif args.command in ("enable", "disable"):
            print(json.dumps(set_enabled(args.root, args.team, args.name,
                                         args.command == "enable")))
        elif args.command == "remove":
            print(json.dumps({"removed": remove(args.root, args.team, args.name)}))
        else:
            print(json.dumps({"skills": [
                {"name": s.name, "version": s.version, "digest": s.digest}
                for s in load_reviewed(args.root, args.team)]}))
    except SkillError as exc:
        print(json.dumps({"error": {"code": "SKILL_ERROR", "message": str(exc)}}))
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI smoke
    raise SystemExit(_cli(sys.argv[1:]))
