"""A2 multi-agent runtime (standalone; integrates behind section4 per ledger)."""

from .board import TaskBoard
from .context import ContextManager, ContextWindow
from .delegation import Delegator
from .errors import RuntimeDenied
from .graph import TaskGraph, TaskNode, decompose
from .scheduler import FairScheduler, Quota
from .specialists import SpecialistRegistry, SpecialistSpec
from .swarm import run_swarm
from .synthesis import Claim, synthesize

__all__ = [
    "Claim",
    "ContextManager",
    "ContextWindow",
    "Delegator",
    "FairScheduler",
    "Quota",
    "RuntimeDenied",
    "SpecialistRegistry",
    "SpecialistSpec",
    "TaskBoard",
    "TaskGraph",
    "TaskNode",
    "decompose",
    "run_swarm",
    "synthesize",
]
