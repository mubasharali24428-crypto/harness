"""Staging change-set gate for isolated runs (B3).

The run workspace is a STAGING AREA. Before the first engine call a baseline
copy is taken (outside the confined view). After each engine call the workspace
is diffed against that baseline:

* low-impact changes (added files, small edits) stay in the workspace;
* HIGH-IMPACT changes - deletions, substantial edits, any symlink - are
  WITHHELD: the workspace is restored to baseline for those paths and the
  agent's version is kept under ``<root>/.staged/<run_id>/``. Nothing high-impact
  can reach the workspace (and anything promoted from it, e.g. a PR) until a
  leader-approved, digest-matching approval is applied via :func:`apply_group`.

This is enforced at the filesystem boundary, so it does not depend on the
DeepSeek runtime exposing per-tool hooks. Limits: the baseline is a full copy
(capped), ``.git`` is ignored, and file-level granularity only.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness_orchestrator.collab import MAX_DIFF, proposal_digest

SUBSTANTIAL_LINES_ENV = "HARNESS_SUBSTANTIAL_LINES"
BASELINE_CAP_BYTES = 200 * 1024 * 1024
IGNORED = {".git"}


class StagingError(RuntimeError):
    """Staging cannot be trusted; callers must fail the run closed."""


def substantial_lines() -> int:
    try:
        return max(1, int(os.environ.get(SUBSTANTIAL_LINES_ENV, "50")))
    except ValueError:
        return 50


@dataclass
class Change:
    path: str
    kind: str  # added | modified | deleted
    lines: int = 0
    link: bool = False

    @property
    def high_impact(self) -> bool:
        return self.link or self.kind == "deleted" or (
            self.kind == "modified" and self.lines >= substantial_lines()
        )


@dataclass
class Group:
    action: str  # delete | modify
    paths: list[str]
    summary: str
    diff: str
    digest: str


@dataclass
class StageReport:
    low_impact: list[str] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)


def _paths(run_id: str, root: str) -> tuple[Path, Path]:
    r = Path(os.path.realpath(root))
    return r / ".baseline" / run_id, r / ".staged" / run_id


def _walk(base: Path) -> dict[str, tuple[str, Any]]:
    """relpath -> ('file', bytes) | ('link', target)."""
    out: dict[str, tuple[str, Any]] = {}
    if not base.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in IGNORED]
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            rel = str(full.relative_to(base))
            if full.is_symlink():
                out[rel] = ("link", os.readlink(full))
            elif full.is_file():
                out[rel] = ("file", full.read_bytes())
    return out


def _text(b: bytes) -> list[str] | None:
    try:
        return b.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def _udiff(path: str, old: tuple[str, Any] | None, new: tuple[str, Any] | None) -> tuple[str, int]:
    def lines(side: tuple[str, Any] | None) -> list[str] | None:
        if side is None:
            return []
        if side[0] == "link":
            return [f"symlink -> {side[1]}\n"]
        return _text(side[1])

    a, b = lines(old), lines(new)
    if a is None or b is None:
        return f"Binary file {path} differs\n", substantial_lines()
    d = list(difflib.unified_diff(a, b, f"a/{path}", f"b/{path}"))
    changed = sum(1 for ln in d if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
    return "".join(d), changed


def ensure_baseline(run_id: str, workspace: str, root: str) -> None:
    """Take the baseline once per run (resumes keep the original)."""
    baseline, _ = _paths(run_id, root)
    if baseline.exists():
        return
    ws = Path(workspace)
    total = sum(f.stat().st_size for f in ws.rglob("*") if f.is_file() and ".git" not in f.parts)
    if total > BASELINE_CAP_BYTES:
        raise StagingError(f"workspace too large to baseline ({total} bytes)")
    baseline.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ws, baseline, symlinks=True, ignore=shutil.ignore_patterns(*IGNORED)
    )


def _changes(baseline: Path, workspace: Path) -> list[tuple[Change, str]]:
    old, new = _walk(baseline), _walk(workspace)
    out: list[tuple[Change, str]] = []
    for rel in sorted(set(old) | set(new)):
        o, n = old.get(rel), new.get(rel)
        if o == n:
            continue
        kind = "added" if o is None else "deleted" if n is None else "modified"
        diff, lines = _udiff(rel, o, n)
        link = bool((n and n[0] == "link") or (o and o[0] == "link" and n is not None))
        out.append((Change(rel, kind, lines, link), diff))
    return out


def _load_manifest(staged: Path) -> dict[str, dict[str, Any]]:
    f = staged / "manifest.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _groups(baseline: Path, staged: Path, manifest: dict[str, dict[str, Any]]) -> list[Group]:
    groups: list[Group] = []
    for action in ("delete", "modify"):
        paths = sorted(p for p, m in manifest.items() if m["action"] == action)
        if not paths:
            continue
        old = _walk(baseline)
        diffs = []
        for p in paths:
            if action == "delete":
                new = None
            else:
                sp = staged / "files" / p
                new = ("link", os.readlink(sp)) if sp.is_symlink() else ("file", sp.read_bytes())
            diffs.append(_udiff(p, old.get(p), new)[0])
        diff = "".join(diffs)[:MAX_DIFF]
        verb = "Delete" if action == "delete" else "Substantially modify"
        groups.append(
            Group(action, paths, f"{verb} {len(paths)} file(s): {', '.join(paths[:5])}",
                  diff, proposal_digest(action, paths, diff))
        )
    return groups


def pending_groups(run_id: str, root: str) -> list[Group]:
    """Groups currently withheld for this run (from disk)."""
    baseline, staged = _paths(run_id, root)
    return _groups(baseline, staged, _load_manifest(staged))


def stage(run_id: str, workspace: str, root: str) -> StageReport:
    """Withhold high-impact changes; return what remains and what needs approval."""
    baseline, staged = _paths(run_id, root)
    if not baseline.exists():
        raise StagingError("no baseline: refusing to release unreviewed output")
    ws = Path(workspace)
    manifest = _load_manifest(staged)
    report = StageReport()
    for change, _diff in _changes(baseline, ws):
        if not change.high_impact:
            report.low_impact.append(change.path)
            continue
        target = ws / change.path
        stage_file = staged / "files" / change.path
        stage_file.parent.mkdir(parents=True, exist_ok=True)
        if stage_file.is_symlink() or stage_file.exists():
            stage_file.unlink()
        if change.kind != "deleted":  # keep the agent's version aside
            if target.is_symlink():
                os.symlink(os.readlink(target), stage_file)
            else:
                shutil.copy2(target, stage_file)
        # restore the workspace to baseline for this path
        if target.is_symlink() or target.is_file():
            target.unlink()
        src = baseline / change.path
        if src.is_symlink():
            os.symlink(os.readlink(src), target)
        elif src.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        manifest[change.path] = {
            "action": "delete" if change.kind == "deleted" else "modify",
            "kind": change.kind,
        }
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    report.groups = _groups(baseline, staged, manifest)
    return report


def apply_group(
    run_id: str, workspace: str, root: str, action: str, paths: list[str], digest: str
) -> list[str]:
    """Land an APPROVED group. Recomputes the digest from the staged files, so
    tampering with the withheld copies (or a different path set) is refused."""
    baseline, staged = _paths(run_id, root)
    manifest = _load_manifest(staged)
    if any(manifest.get(p, {}).get("action") != action for p in paths):
        raise StagingError("approved paths are not staged for this action")
    current = [g for g in _groups(baseline, staged, manifest) if g.action == action]
    if not current or sorted(paths) != current[0].paths or current[0].digest != digest:
        raise StagingError("staged content no longer matches the approved proposal")
    ws = Path(os.path.realpath(workspace))
    for p in paths:  # validate everything BEFORE touching anything
        tpath = ws / p
        parent = Path(os.path.realpath(tpath.parent))
        if os.path.isabs(p) or ".." in Path(p).parts or os.path.commonpath([ws, parent]) != str(ws):
            raise StagingError(f"path escapes the workspace: {p}")
        src = staged / "files" / p
        if action == "modify" and src.is_symlink():
            link = os.readlink(src)
            resolved = os.path.normpath(os.path.join(os.path.dirname(tpath), link))
            if os.path.isabs(link) or os.path.commonpath([ws, Path(resolved)]) != str(ws):
                raise StagingError(f"symlink target escapes the workspace: {p}")
    for p in paths:
        tpath, src, base = ws / p, staged / "files" / p, baseline / p
        if tpath.is_symlink() or tpath.exists():
            tpath.unlink()
        if action == "modify":
            tpath.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                os.symlink(os.readlink(src), tpath)
            else:
                shutil.copy2(src, tpath)
        # the landed state becomes the new baseline so it is not re-withheld
        if base.is_symlink() or base.exists():
            base.unlink()
        if action == "modify":
            base.parent.mkdir(parents=True, exist_ok=True)
            if tpath.is_symlink():
                os.symlink(os.readlink(tpath), base)
            else:
                shutil.copy2(tpath, base)
        manifest.pop(p, None)
        if src.is_symlink() or src.exists():
            src.unlink()
    (staged / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    return paths
