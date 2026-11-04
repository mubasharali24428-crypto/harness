"""Compatibility shim: the factory now lives in the contract package.

Sections may not import each other, and three services independently needed the
same bracketed-IPv6 ``NO_PROXY`` protection. Keeping one implementation in
``harness_contracts`` is the only version that does not drift, so this module
re-exports it and Section 1's call sites and tests stay unchanged.
"""

from __future__ import annotations

from harness_contracts.http_client import env_client

__all__ = ["env_client"]
