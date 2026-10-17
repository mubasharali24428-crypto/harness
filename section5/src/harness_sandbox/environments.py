"""S3: one-call ephemeral development environments with a tenant-segregated warm cache.

``create`` clones the repository ref into a fresh task workspace and overlays a
dependency layer. The layer is produced by a *named, operator-registered
provisioner* (never by caller-supplied commands) and cached under
<root>/<team>/<key>, where key = sha256(team, repo, backend, provisioner,
lock-file digest). Changing a lock file changes the key (automatic invalidation);
another team can never hit this team's entry. Each layer carries a manifest of
file hashes that is re-verified on every hit (cache-poison / corruption check),
and a layer containing secret files or escaping symlinks is refused.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from harness_contracts.signatures import TaskHandle

from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import SecurityPolicyViolationError, is_secret_file

Provisioner = Callable[[Path, Path], None]  # (layer_dir, repo_checkout) -> populate layer_dir


@dataclass(frozen=True)
class EnvironmentSpec:
    repo_ref: str            # local git repository path (remote providers are A4's lane)
    branch: str = "main"
    lock_files: tuple[str, ...] = ()
    provisioner: str = "none"


@dataclass
class EnvironmentResult:
    handle: TaskHandle
    cache: str                       # "hit" | "miss" | "none"
    cache_key: str | None
    timings_ms: dict[str, float] = field(default_factory=dict)


def _git(*args: str, cwd: Path | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    r = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def _clone(src: Path, dest: Path) -> None:
    flag = ["-c"] if sys.platform == "darwin" else ["--reflink=auto"]
    subprocess.run(["cp", "-R", *flag, f"{src}/.", str(dest)], check=True, capture_output=True)


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dp, _d, files in os.walk(root):
        for n in sorted(files):
            p = Path(dp) / n
            if p.is_symlink():
                tgt = (p.parent / os.readlink(p)).resolve()
                if os.path.isabs(os.readlink(p)) or not tgt.is_relative_to(root.resolve()):
                    raise SecurityPolicyViolationError(f"layer symlink escapes: {p.name}")
                out[p.relative_to(root).as_posix()] = "link:" + os.readlink(p)
            else:
                out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class EnvironmentManager:
    def __init__(self, cache_root: Path, runner: LocalSandboxRunner,
                 provisioners: dict[str, Provisioner] | None = None,
                 backend_name: str = "local") -> None:
        self.cache_root = cache_root.resolve()
        self.runner = runner
        self.provisioners = {"none": lambda layer, repo: None, **(provisioners or {})}
        self.backend_name = backend_name

    def _key(self, team: str, spec: EnvironmentSpec, checkout: Path) -> str:
        h = hashlib.sha256()
        for part in (team, spec.repo_ref, self.backend_name, spec.provisioner):
            h.update(part.encode() + b"\0")
        for lf in sorted(spec.lock_files):
            f = checkout / lf
            data = f.read_bytes() if f.is_file() else b"<missing>"
            h.update(lf.encode() + b"\0" + data + b"\0")
        return h.hexdigest()[:32]

    def create(self, team_id: str, task_id: str, spec: EnvironmentSpec, *,
               forbidden_paths: list[str] | None = None, project_id: str | None = None,
               ) -> EnvironmentResult:
        if spec.provisioner not in self.provisioners:
            raise SecurityPolicyViolationError(f"unknown provisioner '{spec.provisioner}'")
        t0 = time.perf_counter()
        handle = self.runner.create_task(task_id, spec.repo_ref, spec.branch, forbidden_paths,
                                         project_id=project_id, team_id=team_id)
        workspace: Path = self.runner.active_tasks[task_id]["workspace"]
        try:
            _git("clone", "--no-hardlinks", "--quiet", "--branch", spec.branch, spec.repo_ref,
                 str(workspace))
            t_clone = time.perf_counter()
            key = self._key(team_id, spec, workspace)
            entry = self.cache_root / team_id / key
            status = "none"
            if spec.provisioner != "none":
                if self._valid(entry):
                    status = "hit"
                else:
                    shutil.rmtree(entry, ignore_errors=True)  # corrupt/poisoned entry is dropped
                    self._build(entry, spec, workspace)
                    status = "miss"
                _clone(entry / "layer", workspace)
            t_end = time.perf_counter()
        except Exception:
            self.runner.destroy(handle)
            raise
        return EnvironmentResult(
            handle, status, key if spec.provisioner != "none" else None,
            {"clone": (t_clone - t0) * 1000, "layer": (t_end - t_clone) * 1000,
             "total": (t_end - t0) * 1000},
        )

    def _build(self, entry: Path, spec: EnvironmentSpec, checkout: Path) -> None:
        tmp = entry.with_name(entry.name + f".building-{os.getpid()}")
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / "layer").mkdir(parents=True)
        self.provisioners[spec.provisioner](tmp / "layer", checkout)
        files = _hash_tree(tmp / "layer")
        for rel in files:
            if is_secret_file(rel):
                shutil.rmtree(tmp, ignore_errors=True)
                raise SecurityPolicyViolationError(f"refusing to cache secret file '{rel}'")
        (tmp / "manifest.json").write_text(json.dumps(files))
        entry.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(tmp, entry)

    @staticmethod
    def _valid(entry: Path) -> bool:
        m = entry / "manifest.json"
        if not m.exists() or not (entry / "layer").is_dir():
            return False
        try:
            return json.loads(m.read_text()) == _hash_tree(entry / "layer")
        except (OSError, ValueError, SecurityPolicyViolationError):
            return False

    def invalidate(self, team_id: str) -> int:
        d = self.cache_root / team_id
        n = len(list(d.iterdir())) if d.exists() else 0
        shutil.rmtree(d, ignore_errors=True)
        return n
