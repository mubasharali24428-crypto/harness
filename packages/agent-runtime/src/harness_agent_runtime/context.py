"""F075: independent context windows — an agent reads only its own window plus
the explicit inputs its contract names."""

from __future__ import annotations

from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class ContextWindow:
    owner: str
    max_tokens: int
    _msgs: list[tuple[str, int]] = field(default_factory=list)

    def add(self, text: str) -> int:
        """Append; evict oldest to stay within budget. Returns evicted count."""
        cost = estimate_tokens(text)
        self._msgs.append((text, cost))
        evicted = 0
        while sum(c for _, c in self._msgs) > self.max_tokens and len(self._msgs) > 1:
            self._msgs.pop(0)
            evicted += 1
        return evicted

    def tokens(self) -> int:
        return sum(c for _, c in self._msgs)

    def render(self) -> list[str]:
        return [t for t, _ in self._msgs]


class ContextManager:
    def __init__(self) -> None:
        self._w: dict[str, ContextWindow] = {}

    def window(self, agent: str, max_tokens: int) -> ContextWindow:
        return self._w.setdefault(agent, ContextWindow(agent, max_tokens))

    def seed(
        self, agent: str, max_tokens: int, shared: dict[str, str], keys: tuple[str, ...]
    ) -> ContextWindow:
        """Copy ONLY the contract-named keys from shared state into a fresh window."""
        w = self.window(agent, max_tokens)
        for k in keys:
            if k in shared:
                w.add(f"{k}: {shared[k]}")
        return w
