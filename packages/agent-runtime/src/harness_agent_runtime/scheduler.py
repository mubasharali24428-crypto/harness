"""F080/F024: fair, quota-bound scheduling across teams (no noisy neighbour)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .errors import RuntimeDenied


@dataclass
class Quota:
    max_running: int = 4
    max_queued: int = 100
    weight: int = 1


@dataclass
class _Team:
    quota: Quota
    queue: deque[str] = field(default_factory=deque)
    running: set[str] = field(default_factory=set)
    served: int = 0


class FairScheduler:
    def __init__(self, global_limit: int, default: Quota | None = None) -> None:
        self.global_limit = global_limit
        self.default = default or Quota()
        self._t: dict[str, _Team] = {}

    def set_quota(self, team_id: str, quota: Quota) -> None:
        self._team(team_id).quota = quota

    def _team(self, team_id: str) -> _Team:
        return self._t.setdefault(team_id, _Team(Quota(**vars(self.default))))

    def submit(self, team_id: str, job_id: str) -> None:
        t = self._team(team_id)
        if len(t.queue) >= t.quota.max_queued:
            raise RuntimeDenied("scheduler.queue_full", team_id)
        t.queue.append(job_id)

    def running_total(self) -> int:
        return sum(len(t.running) for t in self._t.values())

    def next(self) -> tuple[str, str] | None:
        """Pick the eligible team with the lowest served/weight (least-served-first)."""
        if self.running_total() >= self.global_limit:
            return None
        eligible = [
            (t.served / t.quota.weight, name)
            for name, t in sorted(self._t.items())
            if t.queue and len(t.running) < t.quota.max_running
        ]
        if not eligible:
            return None
        _, name = min(eligible)
        t = self._t[name]
        job = t.queue.popleft()
        t.running.add(job)
        t.served += 1
        return name, job

    def release(self, team_id: str, job_id: str) -> None:
        self._team(team_id).running.discard(job_id)
