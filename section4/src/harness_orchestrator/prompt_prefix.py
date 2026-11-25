"""S-02 / X-09: the stable prompt prefix — frozen at run start, volatile last.

Provider prompt caches hit only on a byte-identical prefix. The executor goal
is assembled as::

    [ frozen prefix                        ] [ per-run ] [ per-attempt, volatile ]
      rules -> skills -> memory -> @refs       plan.goal   guidance, repo context

The frozen prefix is the advisory context loaded while planning (M12-G10 rules,
M2-W12 skills and project memory, M21-X2 @ref bundle). This module pins two
properties the 10X plan (§3, Wave C) and FUTURE PLAN 2.0 X-09 ask for:

1. **Frozen at run start.** The prefix is rendered ONCE — at the run's first
   execution attempt — and every later attempt of the same run (resume after a
   pause, retry after a leader approval, a guidance turn) reuses those exact
   bytes, even if someone edits rules, skills or memory mid-run. A new plan
   (``propose_plan``) is a new start and clears the snapshot.
2. **Volatile data last.** Only the four prefix sources below are read; the
   per-attempt values (human guidance, the repo-context baseline the engine
   appends, goal images, routing, budgets, ids, clocks) are never read here,
   so they cannot drift into the prefix. ``tests/unit/test_prompt_prefix.py``
   is the CI guard: it fails if a volatile value reaches the prefix or if the
   prefix bytes change between attempts of one run.

Boundaries (CD-02, unchanged): this module only orders and fingerprints text
that the existing loaders already produced. It adds no write path, no new
source of prompt content, and cannot grant any capability. No geometry or
coordinates are ever part of the prefix (10X plan §3). The digest is
observability only: it rides on the engine result as ``promptPrefix`` so
``scripts/eval/bench_stats.py`` can report prefix stability next to the S-01
cache-read split.

Known limit, stated rather than papered over: the snapshot lives on the
in-process run dict, like the blocks it is built from. A resume that lands on
a different replica has neither, and runs with whatever that replica holds.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

#: The ONLY run_data keys the prefix may read, in prompt order: instructions
#: (rules, then skills), then reviewed memory, then curated evidence (@refs).
PREFIX_SOURCES: tuple[str, ...] = (
    "rules_prompt_block",
    "skills_prompt_block",
    "memory_prompt_block",
    "context_bundle",
)

#: Where the frozen snapshot is kept on the run dict.
SNAPSHOT_KEY = "prompt_prefix_snapshot"

SEPARATOR = "\n\n"


@dataclass(frozen=True)
class PrefixSnapshot:
    """The exact prefix bytes of one run, plus a fingerprint for metrics."""

    text: str
    sha256: str
    bytes: int
    blocks: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        """The digest record carried on the engine result (no prompt text)."""
        return {"sha256": self.sha256, "bytes": self.bytes, "blocks": list(self.blocks)}


def _block_text(run_data: dict[str, Any], key: str) -> str:
    value = run_data.get(key)
    if key == "context_bundle":
        value = value.get("rendered") if isinstance(value, dict) else None
    return str(value or "").strip()


def assemble_prefix(run_data: dict[str, Any]) -> PrefixSnapshot:
    """Render the prefix from the four sources; pure and deterministic.

    An absent or blank block contributes nothing (no empty heading, no stray
    separator), so a run with no advisory context gets an empty prefix and the
    exact prompt it always had.
    """
    parts: list[str] = []
    names: list[str] = []
    for key in PREFIX_SOURCES:
        text = _block_text(run_data, key)
        if text:
            parts.append(text)
            names.append(key)
    rendered = SEPARATOR.join(parts) + SEPARATOR if parts else ""
    raw = rendered.encode("utf-8")
    return PrefixSnapshot(
        text=rendered,
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
        blocks=tuple(names),
    )


def frozen_prefix(run_data: dict[str, Any]) -> PrefixSnapshot:
    """The run's prefix: rendered on the first call, then the same bytes forever.

    Later edits to the source blocks on ``run_data`` do not change it; only
    :func:`reset_prefix` (a new plan) does.
    """
    snap = run_data.get(SNAPSHOT_KEY)
    if isinstance(snap, PrefixSnapshot):
        return snap
    snap = assemble_prefix(run_data)
    run_data[SNAPSHOT_KEY] = snap
    return snap


def reset_prefix(run_data: dict[str, Any]) -> None:
    """Forget the snapshot: the next execution attempt is a new run start."""
    run_data.pop(SNAPSHOT_KEY, None)
