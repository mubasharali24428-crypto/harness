"""Lane-owned workspace management: per-session git checkout + file plumbing.

The lane service performs only FIXED git plumbing on its own checkouts
(init/clone/status/diff/log) under its own data dir. It never executes
user-supplied commands: those go through Section 5's sandbox exec API.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from harness_devworkflow.diffs import normalize_text, sha256_text
from harness_devworkflow.paths import normalize_rel_path

GIT_TIMEOUT_S = 30
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
MAX_LISTING_ENTRIES = 2000


class WorkspaceError(RuntimeError):
    pass


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "devworkflow-lane")
    env.setdefault("GIT_AUTHOR_EMAIL", "devworkflow@localhost")
    env.setdefault("GIT_COMMITTER_NAME", "devworkflow-lane")
    env.setdefault("GIT_COMMITTER_EMAIL", "devworkflow@localhost")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


class WorkspaceManager:
    """Owns per-session checkouts under `root/sessions/{session_id}/`."""

    def __init__(self, root: Path, repo_roots: tuple[str, ...] = ()):
        self.root = Path(root)
        self.repo_roots = tuple(Path(r).expanduser().resolve() for r in repo_roots)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- locations -----------------------------------------------------------

    def session_dir(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("..", "_")
        return self.root / "sessions" / safe

    def checkout(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "workspace"

    def synced_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "synced"

    # -- git plumbing --------------------------------------------------------

    def _git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
        if check and proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise WorkspaceError(f"git {' '.join(args[:2])} failed: {tail}")
        return proc

    def _validate_repo_ref(self, repo_ref: str) -> str:
        """Local repositories under an allow-listed root only; never remote, never arbitrary."""
        text = repo_ref.removeprefix("file://")
        if "://" in text or text.startswith(("git@", "-")):
            raise WorkspaceError("only local repository references are supported")
        path = Path(text).expanduser()
        if not path.exists():
            raise WorkspaceError("repository reference does not exist")
        real = path.resolve()
        if not any(real == r or r in real.parents for r in self.repo_roots):
            raise WorkspaceError("repository is outside the allowed repository roots")
        return str(real)

    def ensure_checkout(self, session_id: str, repo_ref: str | None, branch: str) -> dict[str, Any]:
        """Clone (or init) the session checkout idempotently."""
        if not _BRANCH_RE.match(branch or "") or ".." in branch or branch.endswith((".lock", "/")):
            raise WorkspaceError("invalid branch name")
        checkout = self.checkout(session_id)
        if (checkout / ".git").exists():
            head = self.head_sha(session_id)
            return {"created": False, "headSha": head}
        checkout.parent.mkdir(parents=True, exist_ok=True)
        if repo_ref:
            ref = self._validate_repo_ref(repo_ref)
            proc = self._git(
                checkout.parent,
                "clone",
                "--branch",
                branch,
                "--",
                ref,
                str(checkout),
                check=False,
            )
            if proc.returncode != 0:
                if checkout.exists():
                    shutil.rmtree(checkout, ignore_errors=True)
                proc = self._git(checkout.parent, "clone", "--", ref, str(checkout), check=False)
            if proc.returncode != 0:
                # Local clones of an empty repository fail; fall back to init.
                if checkout.exists():
                    shutil.rmtree(checkout, ignore_errors=True)
                self._init_repo(checkout, branch)
        else:
            self._init_repo(checkout, branch)
        self._git(checkout, "checkout", "-B", branch, check=False)
        return {"created": True, "headSha": self.head_sha(session_id)}

    def _init_repo(self, checkout: Path, branch: str) -> None:
        checkout.mkdir(parents=True, exist_ok=True)
        self._git(checkout, "init", "-b", branch, check=False)
        self._git(
            checkout, "commit", "--allow-empty", "-m", "devworkflow: session start", check=False
        )

    def head_sha(self, session_id: str) -> str:
        proc = self._git(self.checkout(session_id), "rev-parse", "HEAD", check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    # -- file operations -----------------------------------------------------

    def resolve(self, session_id: str, path: str) -> Path:
        checkout = self.checkout(session_id).resolve()
        target = (checkout / normalize_rel_path(path)).resolve()
        try:
            target.relative_to(checkout)
        except ValueError as exc:
            raise WorkspaceError("path escapes the session workspace") from exc
        return target

    def file_exists(self, session_id: str, path: str) -> bool:
        return self.resolve(session_id, path).is_file()

    def read_file(self, session_id: str, path: str) -> str:
        target = self.resolve(session_id, path)
        if not target.is_file():
            raise WorkspaceError(f"file not found: {path}")
        data = target.read_bytes()
        if b"\x00" in data:
            raise WorkspaceError("binary files are not supported in the code surface")
        try:
            return data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceError("file is not valid UTF-8 text") from exc

    def write_file(self, session_id: str, path: str, content: str) -> str:
        target = self.resolve(session_id, path)
        normalized = normalize_text(content)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(normalized, encoding="utf-8")
        return sha256_text(normalized)

    def delete_file(self, session_id: str, path: str) -> None:
        target = self.resolve(session_id, path)
        if target.is_file():
            target.unlink()

    def list_dir(self, session_id: str, dir_path: str) -> dict[str, Any]:
        base = self.resolve(session_id, dir_path) if dir_path else self.checkout(session_id)
        if not base.is_dir():
            raise WorkspaceError(f"directory not found: {dir_path or '(root)'}")
        entries: list[dict[str, Any]] = []
        for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            if child.name == ".git":
                continue
            if len(entries) >= MAX_LISTING_ENTRIES:
                break
            if child.is_dir():
                entries.append({"name": child.name, "type": "dir", "size": 0})
            elif child.is_file():
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append({"name": child.name, "type": "file", "size": size})
        rel = normalize_rel_path(dir_path) if dir_path else ""
        return {"path": rel, "entries": entries}

    # -- fingerprint ---------------------------------------------------------

    def changed_paths(self, session_id: str) -> list[str]:
        """Every modified/added path incl. files in new directories (-uall, NUL-separated)."""
        out = self._git(self.checkout(session_id), "status", "--porcelain", "-z", "-uall").stdout
        paths: list[str] = []
        fields = out.split("\0")
        i = 0
        while i < len(fields):
            entry = fields[i]
            i += 1
            if len(entry) < 4:
                continue
            code, rel = entry[:2], entry[3:]
            if "R" in code or "C" in code:
                i += 1  # next field is the rename/copy source
            paths.append(rel)
        return paths

    def fingerprint(self, session_id: str) -> str:
        """Deterministic hash of (HEAD, changed paths, tracked diff, changed file contents).

        Binds verification evidence to the workspace state at a level strict enough to
        invalidate it on any content change this service makes through its write path.
        """
        checkout = self.checkout(session_id)
        if not (checkout / ".git").exists():
            raise WorkspaceError("session workspace is not initialized")
        parts = [self.head_sha(session_id), self._git(checkout, "diff", "HEAD").stdout]
        for rel in sorted(self.changed_paths(session_id)):
            try:
                parts.append(f"{rel}:{sha256_text(self.read_file(session_id, rel))}")
            except (WorkspaceError, OSError):
                parts.append(f"{rel}:<missing>")
        return sha256_text("\n\x1f\n".join(parts))

    def log_line_range(self, session_id: str, path: str, start: int, end: int) -> str:
        """Fixed-argv `git log -L start,end:path` (blame-style action)."""
        proc = self._git(
            self.checkout(session_id),
            "log",
            "--oneline",
            f"-L{start},{end}:{path}",
            check=False,
        )
        if proc.returncode != 0:
            raise WorkspaceError((proc.stderr or "git log failed").strip()[-300:])
        return proc.stdout
