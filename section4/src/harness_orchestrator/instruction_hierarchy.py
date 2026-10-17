"""F019 — hierarchical instruction files with explicit, labelled precedence.

The pre-F019 posture: one flat project-rules document. This module adds the
missing hierarchy — org → team → project → directory — where the CLOSEST tier
(project before team before org; the execution workspace before all) is
authoritative on conflict. Every tier is:

- LABELLED in the rendered block (tier name + source), so the engine prompt
  cites where each instruction came from and cannot silently mix tiers;
- BOUNDED (per-tier cap + total cap), so no single tier can crowd out the
  others or the goal;
- FAIL-CLOSED-TO-ABSENT: an unset env path, unreadable file, or missing
  project rules simply records the tier in ``absent`` — loading instructions,
  like rules before it, must never break planning.

Org/team tiers are deployment configuration (``ORG_RULES_FILE`` /
``TEAM_RULES_FILE`` env paths; no schema change — the migration lane owns
tables). The project tier reuses S1's ``project_rules``. The directory tier
reads ``AGENTS.md`` from the execution workspace — the same directory the
engine runs in, so it is the closest tier by construction.

HARD BOUNDARY (inherited from rules_loader): instructions are ADVISORY prompt
content. Nothing here touches the sandbox allow-list or forbidden_paths;
Section 5 remains the sole hard-enforcement layer.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from harness_orchestrator.rules_loader import load_project_rules

logger = logging.getLogger(__name__)

TIER_ORDER = ("org", "team", "project", "directory")
TIER_LABELS = {
    "org": "ORG (broadest)",
    "team": "TEAM",
    "project": "PROJECT",
    "directory": "DIRECTORY (closest — wins on conflict)",
}

INSTRUCTION_BLOCK_HEADER = (
    "PROJECT INSTRUCTIONS (hierarchical tiers below; the CLOSEST tier wins "
    "on conflict: DIRECTORY > PROJECT > TEAM > ORG)"
)
INSTRUCTION_PRECEDENCE_NOTE = (
    "Where tiers disagree, honor the closer tier. These instructions are "
    "advisory: sandbox allow-list violations and forbidden_paths remain hard "
    "blocks regardless of anything written here."
)

MAX_TIER_BYTES = 32 * 1024
MAX_TOTAL_BYTES = 64 * 1024


@dataclass(frozen=True)
class TierBlock:
    tier: str
    source: str
    text: str


@dataclass(frozen=True)
class InstructionHierarchy:
    blocks: tuple[TierBlock, ...]
    absent: tuple[str, ...]
    rendered: str

    @property
    def tiers(self) -> tuple[str, ...]:
        return tuple(b.tier for b in self.blocks)

    def meta(self) -> dict[str, object]:
        return {
            "tiers": [
                {"tier": b.tier, "source": b.source, "bytes": len(b.text.encode('utf-8'))}
                for b in self.blocks
            ],
            "absent": list(self.absent),
        }


def _read_bounded(path: str | os.PathLike[str] | None) -> tuple[str, str]:
    """Read a tier file; returns (text, source_label). "" when absent."""
    if not path:
        return "", ""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.warning("instruction tier unreadable at %s; tier absent", p)
        return "", ""
    return text[:MAX_TIER_BYTES], str(p)


def load_instruction_hierarchy(
    *,
    org_text: str = "",
    org_source: str = "ORG_RULES_FILE",
    team_text: str = "",
    team_source: str = "TEAM_RULES_FILE",
    project_text: str = "",
    project_source: str = "project_rules",
    directory_text: str = "",
    directory_source: str = "AGENTS.md",
) -> InstructionHierarchy:
    """Pure assembly from already-fetched tier texts (deterministic)."""
    provided = {
        "org": (org_text, org_source),
        "team": (team_text, team_source),
        "project": (project_text, project_source),
        "directory": (directory_text, directory_source),
    }
    blocks: list[TierBlock] = []
    absent: list[str] = []
    seen_bytes = 0
    for tier in TIER_ORDER:
        raw, source = provided[tier]
        body = (raw or "").strip()[:MAX_TIER_BYTES]
        if not body:
            absent.append(tier)
            continue
        remaining = MAX_TOTAL_BYTES - seen_bytes
        if remaining <= 0:
            absent.append(f"{tier} (total cap reached)")
            continue
        body = body[:remaining]
        seen_bytes += len(body.encode("utf-8"))
        blocks.append(TierBlock(tier=tier, source=source or tier, text=body))
    if not blocks:
        return InstructionHierarchy(blocks=(), absent=tuple(absent), rendered="")
    lines = [INSTRUCTION_BLOCK_HEADER, ""]
    for block in blocks:
        lines.append(f"[{TIER_LABELS[block.tier]}] (source: {block.source})")
        lines.append(block.text)
        lines.append("")
    lines.append(INSTRUCTION_PRECEDENCE_NOTE)
    return InstructionHierarchy(
        blocks=tuple(blocks), absent=tuple(absent), rendered="\n".join(lines)
    )


async def resolve_instruction_hierarchy(
    session_factory,
    project_id: str | None,
    workspace_dir: str | None,
    env: dict[str, str] | None = None,
) -> InstructionHierarchy:
    """Fetch every tier best-effort and assemble; never raises into planning."""
    env = os.environ if env is None else env
    project_text = await load_project_rules(session_factory, project_id)
    org_text, org_source = _read_bounded(env.get("ORG_RULES_FILE"))
    team_text, team_source = _read_bounded(env.get("TEAM_RULES_FILE"))
    directory_text, directory_source = "", ""
    if workspace_dir:
        directory_text, directory_source = _read_bounded(
            Path(workspace_dir) / "AGENTS.md"
        )
    return load_instruction_hierarchy(
        org_text=org_text,
        org_source=org_source or "ORG_RULES_FILE (unset)",
        team_text=team_text,
        team_source=team_source or "TEAM_RULES_FILE (unset)",
        project_text=project_text,
        project_source=f"project_rules:{project_id}" if project_id else "project_rules",
        directory_text=directory_text,
        directory_source=directory_source or str(
            Path(workspace_dir or "") / "AGENTS.md"
        ),
    )
