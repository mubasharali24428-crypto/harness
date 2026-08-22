"""Section 2: GitHub Integration Service."""

from .client import GitHubClient, MergeGuardViolation
from .config import Settings
from .service import GitHubService

__all__ = ["GitHubClient", "GitHubService", "MergeGuardViolation", "Settings"]
