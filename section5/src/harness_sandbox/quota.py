"""S3: resource quotas for sandboxed commands (CPU, memory, processes, disk, file size).

Two layers, because no single OS primitive works on both macOS and Linux:
* kernel rlimits in the child (CPU seconds, max file size) - enforced by the OS;
* a polling ResourceGuard over the child's process group (RSS, process count,
  workspace disk) that SIGKILLs the whole group on breach. RLIMIT_AS/NPROC are
  deliberately not used: macOS ignores AS and NPROC counts every process of the
  user, so a low value would break the child instead of bounding it.
Polling granularity is ``interval`` seconds; a breach inside one interval can
overshoot (documented ceiling: ``ponytail`` upgrade path = cgroups v2 on Linux).
"""
from __future__ import annotations

import asyncio
import os
import resource
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ResourceQuotaExceededError(Exception):
    """A command breached its quota and its process group was killed."""

    def __init__(self, resource_name: str, limit: float, observed: float) -> None:
        super().__init__(f"quota exceeded: {resource_name} {observed:g} > {limit:g}")
        self.resource_name = resource_name
        self.limit = limit
        self.observed = observed


@dataclass(frozen=True)
class ResourceQuota:
    cpu_seconds: int | None = None
    memory_mb: int | None = None
    max_processes: int | None = None
    disk_mb: int | None = None
    max_file_mb: int | None = None

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value is not None and value <= 0:
                raise ValueError(f"quota {name} must be positive")


def rlimit_preexec(quota: ResourceQuota):
    """preexec_fn applying the kernel-enforced subset of the quota."""

    def _apply() -> None:
        if quota.cpu_seconds:
            resource.setrlimit(resource.RLIMIT_CPU, (quota.cpu_seconds, quota.cpu_seconds + 1))
        if quota.max_file_mb:
            cap = quota.max_file_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))

    return _apply


def group_stats(pgid: int) -> tuple[int, int]:
    """(process count, total RSS KiB) of a process group via ps(1)."""
    out = subprocess.run(
        ["ps", "-A", "-o", "pgid=,rss="], capture_output=True, text=True, timeout=5
    ).stdout
    count = rss = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid):
            count += 1
            rss += int(parts[1])
    return count, rss


def dir_size_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


class ResourceGuard:
    """Watches one spawned command; kills its process group on breach."""

    def __init__(self, quota: ResourceQuota, workspace: Path, interval: float = 0.05) -> None:
        self.quota = quota
        self.workspace = workspace
        self.interval = interval
        self.violation: ResourceQuotaExceededError | None = None

    async def watch(self, proc, kill) -> None:
        q = self.quota
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        while proc.returncode is None:
            try:
                count, rss_kib = await asyncio.to_thread(group_stats, pgid)
                disk = (
                    await asyncio.to_thread(dir_size_bytes, self.workspace) if q.disk_mb else 0
                )
            except (subprocess.SubprocessError, OSError):
                return
            breach = None
            if q.max_processes and count > q.max_processes:
                breach = ("processes", q.max_processes, count)
            elif q.memory_mb and rss_kib / 1024 > q.memory_mb:
                breach = ("memory_mb", q.memory_mb, round(rss_kib / 1024, 1))
            elif q.disk_mb and disk / (1024 * 1024) > q.disk_mb:
                breach = ("disk_mb", q.disk_mb, round(disk / (1024 * 1024), 1))
            if breach:
                self.violation = ResourceQuotaExceededError(*breach)
                kill(proc)
                return
            await asyncio.sleep(self.interval)
