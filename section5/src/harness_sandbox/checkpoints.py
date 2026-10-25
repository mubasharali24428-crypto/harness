"""M12-G9 (first slice): git checkpoints for agent edit batches.

Every agent edit batch that flows through :meth:`SandboxService.apply_edit`
gets a git snapshot pair in the *workspace* repo (the sandbox task workspace,
NOT this monorepo — hard rule: no git writes to the harness repo itself):

* ``checkpoint: pre-edit <ts>``   — state before the batch is applied
* ``checkpoint: post-edit step-N (<ts>)`` — state after the batch landed

Mechanics: ``git add -A && git commit`` per snapshot. A dirty workspace needs
no stash-style dance — ``add -A`` stages everything and each batch simply
becomes a commit pair; when nothing changed between snapshots the previous
HEAD is reused instead of manufacturing an empty commit (except the very
first baseline, which is forced with ``--allow-empty`` so every diff endpoint
has a root to hang off).

Read surface (wired in ``harness_sandbox.api``):

* ``GET /api/v1/sandbox/{projectId}/checkpoints``           → list snapshots
* ``GET /api/v1/sandbox/{projectId}/checkpoints/{sha}/diff`` → raw unified diff

Path guards (mandatory):

* workspaces are only ever touched when they resolve INSIDE the manager's
  storage root;
* the ``sha`` parameter must match ``^[0-9a-f]{4,40}$`` before it reaches
  git argv — traversal (``../``), option-style injection (``--upload-pack``)
  and ref-syntax tricks (``:main``) are rejected client-side;
* all git invocations go through ``subprocess.run`` argv form (never a shell)
  with a scrubbed environment (``GIT_*`` dropped, ``GIT_CONFIG_NOSYSTEM=1``),
  and the repo carries a local commit identity so snapshots never inherit —
  or leak — the surrounding machine's git config.

Known first-slice limitations (wave notes, not bugs):
* checkpoint history lives inside the ephemeral task workspace, so
  ``destroy`` removes it with the sandbox; mirroring to durable storage is a
  later lane.
* ``git add -A`` trusts the sandbox's own edit-time path guards
  (``validate_path_edit`` / forbidden_paths) as the upstream defense.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from harness_sandbox.hunk_review import build_reverse_patch, parse_unified_diff

logger = logging.getLogger(__name__)

#: A git object name we are willing to hand to argv. Blocks traversal,
#: leading-dash options, and colon ref-syntax in one shot.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")

_IDENTITY_NAME = "harness-checkpoint"
_IDENTITY_EMAIL = "checkpoint@harness.invalid"


class CheckpointError(Exception):
    """Base class: checkpoint bookkeeping failed (never fatal to the edit)."""

    code = "CHECKPOINT_ERROR"


class CheckpointRepositoryNotFoundError(CheckpointError):
    """No checkpoint-capable workspace exists for this project."""

    code = "CHECKPOINT_REPOSITORY_NOT_FOUND"


class InvalidShaError(CheckpointError):
    """The requested sha failed the argv-safety guard."""

    code = "CHECKPOINT_INVALID_SHA"


class CheckpointNotFoundError(CheckpointError):
    """Well-formed sha, but no checkpointed workspace contains that commit."""

    code = "CHECKPOINT_NOT_FOUND"


class CheckpointConflictError(CheckpointError):
    """The requested revert/restore cannot be applied to the current tree."""

    code = "CHECKPOINT_CONFLICT"


class CheckpointSelectionError(CheckpointError):
    """The hunk selection is empty or does not match this checkpoint's diff."""

    code = "CHECKPOINT_BAD_SELECTION"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_sha(sha: str) -> str:
    """Raise :class:`InvalidShaError` unless ``sha`` is a plain hex object id."""
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise InvalidShaError(
            f"'{sha}' is not a valid checkpoint sha (hex object id expected)."
        )
    return sha


def _git_env() -> dict[str, str]:
    """Scrubbed env for git subprocesses: drop inherited GIT_* state."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _git(
    workspace: Path,
    *args: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one git command (argv form, no shell) inside ``workspace``."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=30,
            env=_git_env(),
            input=input_text,
        )
    except FileNotFoundError as exc:  # git binary missing
        raise CheckpointError("git binary unavailable; checkpoints disabled.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CheckpointError(f"git {' '.join(args[:2])} timed out.") from exc
    if check and proc.returncode != 0:
        raise CheckpointError(
            f"git {' '.join(args[:2])} failed: {(proc.stderr or proc.stdout).strip()[:300]}"
        )
    return proc


class CheckpointManager:
    """Owns git snapshot bookkeeping for sandbox task workspaces."""

    def __init__(self, root: Path | None = None) -> None:
        #: Guard rail: workspaces outside this resolved root are refused.
        from harness_sandbox.config import config

        self.root = (root or config.storage_root).resolve()
        self._git_available = shutil.which("git") is not None
        if not self._git_available:
            logger.warning("git not found on PATH; checkpoint snapshots disabled.")

    # ------------------------------------------------------------------ guards

    def _guard_workspace(self, task_id: str, workspace: Path) -> Path:
        """Resolve and confine the workspace under ``self.root``."""
        try:
            resolved = workspace.resolve()
        except OSError as exc:
            raise CheckpointError(f"workspace '{workspace}' cannot be resolved.") from exc
        if self.root != resolved and self.root not in resolved.parents:
            raise CheckpointError(
                f"refusing to checkpoint '{resolved}': outside storage root '{self.root}'."
            )
        if not resolved.is_dir():
            raise CheckpointError(f"workspace '{resolved}' does not exist.")
        if resolved.name != "workspace":
            raise CheckpointError(
                f"refusing to checkpoint '{resolved}': expected a '<task>/workspace' directory."
            )
        if resolved.parent.name != task_id:
            raise CheckpointError(
                f"refusing to checkpoint task '{task_id}' at '{resolved}': path mismatch."
            )
        return resolved

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _has_commits(workspace: Path) -> bool:
        probe = _git(
            workspace, "rev-parse", "--verify", "--quiet", "HEAD", check=False
        )
        return probe.returncode == 0

    def _stamp_meta(self, workspace: Path, task_id: str, project_id: str | None) -> None:
        """Record ownership in repo-local config so listing survives restarts
        (no dependence on in-memory active_tasks)."""
        _git(workspace, "config", "user.name", _IDENTITY_NAME)
        _git(workspace, "config", "user.email", _IDENTITY_EMAIL)
        _git(workspace, "config", "harness.taskId", task_id)
        if project_id:
            _git(workspace, "config", "harness.projectId", project_id)

    @staticmethod
    def _read_meta(workspace: Path, key: str) -> str | None:
        proc = _git(workspace, "config", "--get", key, check=False)
        value = (proc.stdout or "").strip()
        return value or None

    def ensure_repo(self, workspace: Path, task_id: str, project_id: str | None = None) -> None:
        """Idempotently turn ``workspace`` into a checkpoint-capable repo."""
        if not self._git_available:
            raise CheckpointError("git binary unavailable; checkpoints disabled.")
        if not (workspace / ".git").exists():
            _git(workspace, "init", "--initial-branch=main")
        self._stamp_meta(workspace, task_id, project_id)
        if not self._has_commits(workspace):
            # Baseline root commit so the first pre/post pair always has an ancestor.
            _git(workspace, "commit", "--allow-empty", "-m", f"checkpoint: init {_utc_now_iso()}")

    # --------------------------------------------------------------- snapshots

    def snapshot(
        self,
        task_id: str,
        workspace: Path,
        *,
        kind: str,
        step: int | None = None,
        project_id: str | None = None,
    ) -> str | None:
        """Stage everything and commit one checkpoint; returns the snapshot sha.

        Returns the existing HEAD (not None) when the tree is already exactly
        represented by a commit — the caller can treat the returned sha as the
        truthful state marker either way. Raises :class:`CheckpointError`
        subclasses; callers treat failures as non-fatal telemetry loss.
        """
        workspace = self._guard_workspace(task_id, workspace)
        self.ensure_repo(workspace, task_id=task_id, project_id=project_id)

        _git(workspace, "add", "-A")
        clean = _git(workspace, "diff", "--cached", "--quiet", check=False).returncode == 0
        if clean:
            head = _git(workspace, "rev-parse", "HEAD").stdout.strip()
            return head or None

        if kind == "pre-edit":
            message = f"checkpoint: pre-edit {_utc_now_iso()}"
        elif kind == "post-edit":
            step_label = step if step is not None else "?"
            message = f"checkpoint: post-edit step-{step_label} ({_utc_now_iso()})"
        else:
            message = f"checkpoint: {kind} {_utc_now_iso()}"
        _git(workspace, "commit", "-m", message)
        return _git(workspace, "rev-parse", "HEAD").stdout.strip()

    # ------------------------------------------------------------------- reads

    def _iter_workspaces(self) -> list[Path]:
        """All ``<task>/workspace`` directories under the guarded root."""
        if not self.root.is_dir():
            return []
        found: list[Path] = []
        for child in sorted(self.root.iterdir()):
            candidate = child / "workspace"
            if (candidate / ".git").exists():
                found.append(candidate)
        return found

    def _project_workspaces(self, project_id: str) -> list[Path]:
        matched: list[Path] = []
        for ws in self._iter_workspaces():
            if self._read_meta(ws, "harness.projectId") == project_id:
                matched.append(ws)
        if not matched:
            raise CheckpointRepositoryNotFoundError(
                f"No checkpointed workspace exists for project '{project_id}'. "
                "Snapshots appear once the sandbox records its first edit batch."
            )
        return matched

    def list_checkpoints(self, project_id: str) -> list[dict]:
        """Newest-first snapshot list across every workspace of ``project_id``."""
        items: list[dict] = []
        for ws in self._project_workspaces(project_id):
            task_id = self._read_meta(ws, "harness.taskId") or ws.parent.name
            proc = _git(ws, "log", "--pretty=%H%x1f%cI%x1f%s")
            for line in proc.stdout.splitlines():
                parts = line.split("\x1f")
                if len(parts) != 3:
                    continue
                sha, created_at, subject = parts
                items.append(
                    {"sha": sha, "message": subject, "createdAt": created_at, "taskId": task_id}
                )
        items.sort(key=lambda item: item["createdAt"], reverse=True)
        return items

    def _owner_workspace(self, project_id: str, sha: str) -> Path:
        """The workspace whose history contains ``sha``."""
        validate_sha(sha)
        for ws in self._project_workspaces(project_id):
            probe = _git(ws, "cat-file", "-e", f"{sha}^{{commit}}", check=False)
            if probe.returncode == 0:
                return ws
        raise CheckpointNotFoundError(
            f"No checkpoint '{sha}' found for project '{project_id}'."
        )

    def get_diff(self, project_id: str, sha: str) -> str:
        """Raw unified diff introduced by one checkpoint commit."""
        ws = self._owner_workspace(project_id, sha)
        show = _git(ws, "show", "--format=", "--no-color", sha)
        return show.stdout

    @contextmanager
    def _review_lock(self, workspace: Path) -> Iterator[None]:
        """Serialise revert/restore per workspace (multi-process safe)."""
        lock_path = workspace / ".git" / "harness-review.lock"
        with open(lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def list_hunks(self, project_id: str, sha: str) -> list[dict]:
        """Parsed files/hunks of one snapshot, for the review surface."""
        from harness_sandbox.hunk_review import summarize

        return summarize(parse_unified_diff(self.get_diff(project_id, sha)))

    def revert_hunks(
        self, project_id: str, sha: str, selections: list[tuple[str, int]]
    ) -> dict:
        """Reverse-apply the selected hunks onto the workspace, then snapshot.

        The reverse patch is rebuilt server-side from ``git show <sha>``: the
        caller only names (path, hunk index) pairs. ``git apply --check`` runs
        first, so a conflict leaves the tree untouched and answers 409.
        """
        if not selections:
            raise CheckpointSelectionError("select at least one hunk to revert")
        ws = self._owner_workspace(project_id, sha)
        files = parse_unified_diff(self.get_diff(project_id, sha))
        try:
            patch, count = build_reverse_patch(files, selections)
        except Exception as exc:  # noqa: BLE001 - every parse/selection error is a 422
            raise CheckpointSelectionError(str(exc)) from exc
        with self._review_lock(ws):
            check = _git(ws, "apply", "-R", "--check", "-", input_text=patch, check=False)
            if check.returncode != 0:
                raise CheckpointConflictError(
                    "the workspace changed since this snapshot; "
                    f"the selected hunks no longer apply: {(check.stderr or '').strip()[:300]}"
                )
            _git(ws, "apply", "-R", "-", input_text=patch)
            task_id = self._read_meta(ws, "harness.taskId") or ws.parent.name
            head = self.snapshot(
                task_id,
                ws,
                kind=f"hunk-revert {count} hunk(s) from {sha[:12]}",
                project_id=project_id,
            )
        return {"sha": head, "reverted": count}

    def restore(self, project_id: str, sha: str) -> dict:
        """Reset the workspace tree to one snapshot, then record the restore.

        Tracked files match ``sha`` exactly; untracked files are left alone
        (the reset refuses rather than deleting files it does not know).
        """
        ws = self._owner_workspace(project_id, sha)
        with self._review_lock(ws):
            reset = _git(ws, "read-tree", "--reset", "-u", sha, check=False)
            if reset.returncode != 0:
                raise CheckpointConflictError(
                    "the workspace cannot be reset to this snapshot without "
                    f"discarding unknown files: {(reset.stderr or '').strip()[:300]}"
                )
            task_id = self._read_meta(ws, "harness.taskId") or ws.parent.name
            head = self.snapshot(
                task_id,
                ws,
                kind=f"restore {sha[:12]}",
                project_id=project_id,
            )
        return {"sha": head, "restoredTo": sha}
