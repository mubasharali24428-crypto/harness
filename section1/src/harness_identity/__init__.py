"""Section 1: Data & Identity Layer. Owns persistence, auth, audit, event bus delivery."""

from .app import make_app, router
from .audit import write_audit
from .config import Settings
from .crypto import KeyVault
from .eventbus import EventBus

__all__ = ["Settings", "KeyVault", "EventBus", "write_audit", "make_app", "router"]
