"""The `harness` command line (W6).

`run`, `chat`, `status`, `diff` and credential handling live here. The CLI is a
thin front-end over `harness_sdk`: it adds no API surface and no event types, so
a run started here is the same run the web starts, with the same audit trail.
Chat adds no server session either — see `session.py` for what is remembered
between turns, and where.
"""

from .main import entry, main

__all__ = ["entry", "main"]
