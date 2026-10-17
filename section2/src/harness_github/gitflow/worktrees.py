"""F031/F032: one git worktree + branch per run, confined under a root.

Every git call is fixed-argv with global/system config disabled and hooks/fsmonitor
neutralised on the command line, so a registered repository's own config cannot make
these calls execute code. Dirty worktrees are never reset or deleted implicitly.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .naming import NamingError, check_ref, run_branch

GIT_TIMEOUT_S = 60


class WorktreeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class WorktreeInfo:
    run_id: str
    branch: str
    path: str
    head: str
    reused: bool = False


def git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/nonexistent",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }
    env.update(extra or {})
    return env


_HARDEN = (
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "protocol.ext.allow=never",
)


def run_git(
    cwd: Path, *args: str, env: dict[str, str] | None = None, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *_HARDEN, *args],
        cwd=str(cwd),
        env=git_env(env),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=False,
    )


class WorktreeManager:
    def __init__(self, repo: str | Path, root: str | Path, repo_roots: tuple[str, ...] = ()):
        self.repo = Path(repo).expanduser().resolve()
        roots = tuple(Path(r).expanduser().resolve() for r in repo_roots)
        if not any(self.repo == r or r in self.repo.parents for r in roots):
            raise WorktreeError("REPO_NOT_ALLOWED", "repository is outside the allowed roots")
        if not (self.repo / ".git").exists():
            raise WorktreeError("NOT_A_REPO", "not a git repository")
        root_p = Path(root).expanduser()
        if not root_p.is_absolute() or root_p.parent == root_p:
            raise WorktreeError("ROOT_INVALID", "worktree root must be an absolute non-root dir")
        root_p.mkdir(parents=True, exist_ok=True)
        self.root = root_p.resolve()
        if self.root == self.repo or self.repo in self.root.parents:
            raise WorktreeError("ROOT_INVALID", "worktree root must be outside the repository")

    # -- helpers -------------------------------------------------------------

    def path_for(self, run_id: str) -> Path:
        try:
            run_branch(run_id)
        except NamingError as exc:
            raise WorktreeError("INVALID_RUN_ID", str(exc)) from exc
        target = (self.root / run_id).resolve()
        if self.root not in target.parents:
            raise WorktreeError("INVALID_RUN_ID", "worktree path escapes the root")
        return target

    def _git(self, cwd: Path, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        proc = run_git(cwd, *args)
        if ok and proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-300:]
            raise WorktreeError("GIT_FAILED", f"git {args[0]} failed: {tail}")
        return proc

    def _registered(self) -> dict[str, str]:
        """{resolved worktree path: branch} from `git worktree list --porcelain`."""
        out = self._git(self.repo, "worktree", "list", "--porcelain").stdout
        result: dict[str, str] = {}
        path = None
        for line in out.splitlines():
            if line.startswith("worktree "):
                path = str(Path(line[9:]).resolve())
            elif line.startswith("branch ") and path:
                result[path] = line[7:].removeprefix("refs/heads/")
        return result

    def _branch_exists(self, branch: str) -> bool:
        return (
            self._git(
                self.repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", ok=False
            ).returncode
            == 0
        )

    def _head(self, path: Path) -> str:
        return self._git(path, "rev-parse", "HEAD").stdout.strip()

    # -- lifecycle -----------------------------------------------------------

    def create(self, run_id: str, base_ref: str = "HEAD") -> WorktreeInfo:
        path = self.path_for(run_id)  # validates the run id (WorktreeError)
        branch = run_branch(run_id)
        try:
            if base_ref != "HEAD":
                check_ref(base_ref)
        except NamingError as exc:
            raise WorktreeError("INVALID_REF", str(exc)) from exc
        registered = self._registered()
        if str(path) in registered:
            if registered[str(path)] != branch:
                raise WorktreeError("BRANCH_MISMATCH", "worktree exists on a different branch")
            return WorktreeInfo(run_id, branch, str(path), self._head(path), reused=True)
        if path.exists():
            raise WorktreeError("PATH_OCCUPIED", "worktree path exists but is not registered")
        if self._git(
            self.repo, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}", ok=False
        ).returncode:
            raise WorktreeError("INVALID_REF", f"base ref {base_ref!r} does not resolve")
        if self._branch_exists(branch):
            if branch in registered.values():
                raise WorktreeError("BRANCH_IN_USE", "branch is checked out in another worktree")
            self._git(self.repo, "worktree", "add", "--", str(path), branch)
        else:
            self._git(self.repo, "worktree", "add", "-b", branch, "--", str(path), base_ref)
        return WorktreeInfo(run_id, branch, str(path), self._head(path))

    def status(self, run_id: str) -> dict:
        path = self.path_for(run_id)
        registered = self._registered()
        if str(path) not in registered:
            return {"exists": False, "registered": False, "dirty": False, "changed": []}
        if not path.exists():
            return {"exists": False, "registered": True, "dirty": False, "changed": []}
        out = self._git(path, "status", "--porcelain", "-z", "-uall").stdout
        changed = [e[3:] for e in out.split("\0") if len(e) > 3]
        return {
            "exists": True,
            "registered": True,
            "branch": registered[str(path)],
            "head": self._head(path),
            "dirty": bool(changed),
            "changed": changed,
        }

    def recover(self, run_id: str) -> dict:
        """Re-materialise a registered-but-missing worktree from its branch (commits kept).

        A dirty worktree is reported, never reset. Nothing is deleted.
        """
        st = self.status(run_id)
        if st["exists"]:
            return {"action": "none", **st}
        branch = run_branch(run_id)
        path = self.path_for(run_id)
        self._git(self.repo, "worktree", "prune")
        if not self._branch_exists(branch):
            raise WorktreeError("BRANCH_GONE", "run branch no longer exists; nothing to recover")
        self._git(self.repo, "worktree", "add", "--", str(path), branch)
        return {"action": "recreated", **self.status(run_id)}

    def remove(self, run_id: str, *, discard: bool = False) -> dict:
        path = self.path_for(run_id)
        st = self.status(run_id)
        if not st["exists"]:
            self._git(self.repo, "worktree", "prune")
            return {"removed": False, "reason": "not present"}
        if st["dirty"] and not discard:
            raise WorktreeError("DIRTY", "worktree has uncommitted changes; pass discard=True")
        args = ["worktree", "remove", *(["--force"] if discard else []), "--", str(path)]
        self._git(self.repo, *args)
        return {"removed": True, "branchKept": True}
