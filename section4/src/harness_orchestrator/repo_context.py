"""W2 (N02) — the baseline a model needs before its first tool call.

The runtime ships a context injector and we run it switched off
(`includeRuntimeContext:false` in the SDK profile). That key is upstream's, so the
lever this product actually holds is the goal text it hands the engine. This module
builds that baseline: the shape of the tree, what the README claims the project is,
what git thinks is dirty, and which dependency manifests exist.

Deliberately bounded and deliberately dumb. No embeddings, no ranking, no index —
that is B2's job and it is not this week's. What it must never do is grow with the
repository: a 500-file project would otherwise become a 500-file prompt on every
run, paid for every time, with the interesting lines buried in the middle.

The one behaviour worth calling out is the empty case. A workspace with nothing
recognisable returns ``""`` rather than a page of headings, because an empty
scaffold in the prompt is worse than no scaffold: it teaches the model that the
repo is empty.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: Directories whose contents are never context: generated, vendored or enormous.
NOISE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        "dist",
        "build",
        "target",
        "vendor",
        ".idea",
        ".vscode",
    }
)

#: Presence of one of these is a strong hint about how the project is built.
MANIFESTS = (
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
    "Makefile",
    "Dockerfile",
)

README_NAMES = ("README.md", "README.rst", "README.txt", "readme.md")

TREE_LIMIT = 200
README_CHARS = 800
GIT_LINES = 20
GIT_TIMEOUT_S = 3


def _tree(root: Path, limit: int = TREE_LIMIT) -> list[str]:
    """Depth-first file list, noise pruned, capped.

    Iterative rather than ``rglob`` so pruning happens before descent: walking
    ``node_modules`` first and filtering after costs seconds on a real checkout
    and buys nothing.
    """
    out: list[str] = []
    stack = [root]
    while stack and len(out) < limit:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        files, dirs = [], []
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in NOISE_DIRS:
                    dirs.append(entry)
            elif entry.is_file(follow_symlinks=False):
                files.append(entry)
        for entry in files:
            if len(out) >= limit:
                break
            out.append(str(Path(entry.path).relative_to(root)))
        # Reversed so the first directory in sorted order is visited first.
        stack.extend(reversed(dirs))
    return out


def _readme(root: Path) -> str:
    for name in README_NAMES:
        candidate = root / name
        if candidate.is_file():
            try:
                return candidate.read_text(errors="replace")[:README_CHARS].strip()
            except OSError:
                return ""
    return ""


def _git_status(root: Path) -> list[str]:
    """Dirty paths, or nothing.

    ``check=False`` and a short timeout on purpose: a workspace that is not a repo,
    or a host without git, must cost the prompt nothing and the run even less.
    """
    try:
        inside = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return []
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if status.returncode != 0:
        return []
    return [ln for ln in status.stdout.splitlines() if ln.strip()][:GIT_LINES]


def _manifests(root: Path) -> list[str]:
    found = [name for name in MANIFESTS if (root / name).is_file()]
    # A poetry/pip project also lists requirements in pyproject; both are worth
    # naming because the model reads them differently.
    return found


def build_repo_context(root: os.PathLike[str] | str, *, max_chars: int = 6000) -> str:
    """A bounded text block describing the workspace, or ``""`` if it says nothing."""
    base = Path(root)
    if not base.is_dir():
        return ""

    files = _tree(base)
    readme = _readme(base)
    dirty = _git_status(base)
    manifests = _manifests(base)

    if not files and not readme and not dirty and not manifests:
        return ""

    sections: list[str] = ["## Repository context"]
    if manifests:
        sections.append("Dependency manifests present: " + ", ".join(manifests))
    if files:
        shown = "\n".join(files)
        more = "" if len(files) < TREE_LIMIT else f"\n… (capped at {TREE_LIMIT} entries)"
        sections.append(f"### Files\n{shown}{more}")
    if dirty:
        sections.append("### Git status (dirty paths)\n" + "\n".join(dirty))
    if readme:
        sections.append(f"### README (excerpt)\n{readme}")

    block = "\n\n".join(sections)
    if len(block) > max_chars:
        # Cut at the cap rather than returning an over-long prompt; the marker
        # says it was cut so a model does not read the edge as the end of the repo.
        block = block[: max(0, max_chars - 14)].rstrip() + "\n… (truncated)"
    return block
