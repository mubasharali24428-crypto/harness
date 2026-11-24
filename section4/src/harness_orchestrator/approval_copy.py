"""X-07 Plain sentence + X-21 Decision-card severity grid for an approval request.

Translation, never replacement: both are pure functions of the facts already on the request
(action + paths + optional command). No model prose, no network, no preference lookup — the
member's rendering choice (section1 ``/api/v1/me/rendering``) only picks which of these
section7 shows first; the technical record is always returned alongside and stays one tap away.
Anything the facts do not establish is rendered as "unknown" (plan 5.8), never as safe.

Severity mapping is a fixed table (below), so a golden fixture pins every cell. Approval
envelopes (P-D6) are not enabled: the grid informs a human, it never approves anything.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from harness_orchestrator.confine import SECRET_FILE_PATTERNS

# X-21 rows, in display order.
ACCESS_FAMILIES = (
    "file_read", "file_write", "network", "shell", "credentials", "config", "merge_deploy",
    "scope_expansion",
)
BANDS = ("low", "moderate", "elevated", "critical")
FAMILY_LABEL = {
    "file_read": "File read", "file_write": "File write", "network": "Network / egress",
    "shell": "Shell / execute", "credentials": "Credentials / secrets",
    "config": "Configuration change", "merge_deploy": "Merge / deploy",
    "scope_expansion": "Scope expansion",
}
CONFIG_PATTERNS = (
    ".github/*", "*Dockerfile*", "*docker-compose*", "*.yml", "*.yaml", "*.toml", "*.ini",
    "package.json", "*/package.json", "Makefile", "*.cfg",
)
# action -> (family, band, verb phrase). Unknown actions fall through to "unknown".
ACTIONS = {
    "read": ("file_read", "low", "read"),
    "modify": ("file_write", "moderate", "change"),
    "write": ("file_write", "moderate", "change"),
    "delete": ("file_write", "elevated", "delete"),
    "shell": ("shell", "elevated", "run a shell command touching"),
    "network": ("network", "elevated", "send data over the network involving"),
    "mcp": ("network", "elevated", "call an external tool with"),
    "scope_change": ("scope_expansion", "critical", "widen its allowed scope to"),
    "merge": ("merge_deploy", "critical", "merge"),
}


def _match(path: str, patterns: tuple[str, ...]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def grid(action: str, paths: list[str]) -> dict[str, Any]:
    """Active cells {family, band} + dominant band (max column). Unknown action -> unknown."""
    base = ACTIONS.get(action)
    if base is None:
        return {"cells": [], "dominantBand": "unknown"}
    cells = {base[0]: base[1]}
    if any(_match(p, SECRET_FILE_PATTERNS) for p in paths):
        cells["credentials"] = "critical"
    if any(_match(p, CONFIG_PATTERNS) for p in paths):
        cells["config"] = "elevated"
    dominant = max(cells.values(), key=BANDS.index)
    return {
        "cells": [{"family": f, "band": cells[f], "label": FAMILY_LABEL[f]}
                  for f in ACCESS_FAMILIES if f in cells],
        "dominantBand": dominant,
    }


def plain(action: str, paths: list[str]) -> str:
    """One deterministic sentence. Never claims safety; unknowns say unknown."""
    verb = ACTIONS.get(action, (None, None, None))[2]
    if verb is None:
        return "The agent wants to do something this summary cannot classify (unknown action)."
    if not paths:
        target = "unknown files"
    else:
        shown = ", ".join(paths[:2]) + (f" and {len(paths) - 2} more" if len(paths) > 2 else "")
        target = f"{len(paths)} file{'s' if len(paths) != 1 else ''} ({shown})"
    extra = ""
    if any(_match(p, SECRET_FILE_PATTERNS) for p in paths):
        extra = " This touches a secrets or credentials file."
    return f"The agent wants to {verb} {target}.{extra}"


def render(action: str, paths: list[str]) -> dict[str, Any]:
    return {"plain": plain(action, paths), "decisionGrid": grid(action, paths)}
