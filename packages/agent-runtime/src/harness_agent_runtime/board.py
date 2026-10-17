"""F073/F079: team-scoped shared task board with human assignment + escalation."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from .errors import RuntimeDenied

STATUSES = ("todo", "in_progress", "blocked", "review", "done", "failed")
_ALLOWED = {
    "todo": {"in_progress", "blocked", "failed"},
    "in_progress": {"review", "done", "blocked", "failed"},
    "blocked": {"in_progress", "todo", "failed"},
    "review": {"done", "in_progress", "failed"},
    "done": set(),
    "failed": {"todo"},
}


@dataclass
class Card:
    id: str
    team_id: str
    title: str
    status: str = "todo"
    assignee: str | None = None  # "agent:<name>" | "user:<id>"
    version: int = 1
    history: list[dict[str, Any]] = field(default_factory=list)


class TaskBoard:
    def __init__(self) -> None:
        self._cards: dict[str, Card] = {}
        self._seq = itertools.count(1)
        self._escalations: dict[str, dict[str, Any]] = {}

    def _get(self, team_id: str, card_id: str) -> Card:
        c = self._cards.get(card_id)
        if c is None or c.team_id != team_id:
            raise RuntimeDenied("board.not_found", card_id)  # identical for foreign cards
        return c

    def create(self, team_id: str, title: str, actor: str) -> Card:
        c = Card(f"c{next(self._seq)}", team_id, title)
        c.history.append({"actor": actor, "event": "created"})
        self._cards[c.id] = c
        return c

    def move(self, team_id: str, card_id: str, to: str, actor: str, *, expect_version: int) -> Card:
        c = self._get(team_id, card_id)
        if c.version != expect_version:
            raise RuntimeDenied("board.conflict", f"have v{c.version}")
        if to not in _ALLOWED[c.status]:
            raise RuntimeDenied("board.bad_transition", f"{c.status}->{to}")
        c.history.append({"actor": actor, "event": f"{c.status}->{to}"})
        c.status, c.version = to, c.version + 1
        return c

    def assign(self, team_id: str, card_id: str, assignee: str, actor: str) -> Card:
        if not assignee.startswith(("agent:", "user:")):
            raise RuntimeDenied("board.bad_assignee", assignee)
        c = self._get(team_id, card_id)
        c.history.append({"actor": actor, "event": f"assigned {assignee}"})
        c.assignee, c.version = assignee, c.version + 1
        return c

    def escalate(self, team_id: str, card_id: str, reason: str, actor: str) -> dict[str, Any]:
        c = self._get(team_id, card_id)
        if c.status in ("done",):
            raise RuntimeDenied("board.bad_transition", "done cards cannot escalate")
        if card_id in self._escalations:
            return self._escalations[card_id]  # idempotent
        if c.status != "blocked" and "blocked" in _ALLOWED[c.status]:
            c.history.append({"actor": actor, "event": f"{c.status}->blocked"})
            c.status, c.version = "blocked", c.version + 1
        item = {"card": card_id, "team": team_id, "reason": reason, "state": "open", "by": actor}
        self._escalations[card_id] = item
        return item

    def human_queue(self, team_id: str) -> list[dict[str, Any]]:
        return [
            e for e in self._escalations.values() if e["team"] == team_id and e["state"] == "open"
        ]

    def resolve(self, team_id: str, card_id: str, human: str, note: str) -> Card:
        c = self._get(team_id, card_id)
        e = self._escalations.get(card_id)
        if e is None or e["state"] != "open":
            raise RuntimeDenied("board.no_open_escalation")
        e.update(state="resolved", resolvedBy=human, note=note)
        c.history.append({"actor": human, "event": "escalation_resolved"})
        c.status, c.version = "todo", c.version + 1
        return c

    def list(self, team_id: str) -> list[Card]:
        return [c for c in self._cards.values() if c.team_id == team_id]
