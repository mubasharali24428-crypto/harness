"""Run branch naming. Keeps the platform convention ``agent/<runId>`` so pr_open's
run_id_from_branch keeps binding PRs to runs."""

from __future__ import annotations

import re

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
BRANCH_PREFIX = "agent/"


class NamingError(ValueError):
    pass


def run_branch(run_id: str) -> str:
    if not RUN_ID_RE.match(run_id or ""):
        raise NamingError("run id must be 1-64 chars of [A-Za-z0-9_-] and start alphanumeric")
    return f"{BRANCH_PREFIX}{run_id}"


def check_ref(ref: str) -> str:
    """A ref that is safe to hand to git as a value (no option injection, no traversal)."""
    if (
        not REF_RE.match(ref or "")
        or ".." in ref
        or "//" in ref
        or ref.endswith(("/", ".lock", "."))
        or "@{" in ref
    ):
        raise NamingError(f"unsafe git ref {ref!r}")
    return ref
