"""Harness extensions plane (A5).

External MCP client/registry, deny-by-default tool grants, schema-hardened
discovery, versioned skills/playbooks and a digest-verified curated plugin
catalog — served under /api/v1/extensions.
"""

from .config import Settings
from .store import Store

__all__ = ["Settings", "Store"]
