"""M4: ``model.invoke`` routed by the Harness — with no secret in any prompt or log.

Section 3 owns routing: its router chooses the provider, the model and the cost,
and this relay asks it. What the relay owns is the seam where a workflow's data
becomes a prompt and a reply becomes a value, and that seam has three rules:

1. **A secret does not leave.** Every prompt string is scrubbed against
   registered secrets and recognised key shapes before the router is called.
2. **A secret does not come back.** The reply is scrubbed before it is parsed,
   so a model that echoes a token cannot turn it into a program value that then
   reaches an event or the evidence bundle.
3. **No silent substitution.** If the router answers with a different model than
   the one the agent asked for, the substitution is *recorded*; the relay never
   retries on a cheaper model and never edits the request to fit one.

The relay keeps a report of what crossed — counts, digests and model names —
never the prompt text. That is what makes "no secret in any log" checkable
rather than hopeful: the surface has nothing to leak.

Roles come from the source: an agent declares ``role`` (a human-facing label)
and optionally ``model``. Which *routing* role that maps to is the host's
decision — ``roles={"developer": "reviewer"}`` — because the program is not
allowed to choose the model policy.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from harness_contracts.signatures import ModelCallOpts, ModelRole

from harness_hx import ast as A
from harness_hx.api import check_source

from .errors import CheckRefused, HostUnavailable, InvalidHostResult
from .secrets import REDACTED, Tally, scrub

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are the Hx agent {agent!r} (declared role {label!r}). {objective}"
    " You answer one operation at a time. Reply with exactly one JSON object and"
    " nothing else: no prose, no code fences, no commentary."
)


@dataclass(frozen=True)
class AgentPolicy:
    """What the source declared about one agent, and what the host decided."""

    name: str
    label: str
    role: ModelRole = ModelRole.PRIMARY
    model: str | None = None
    objective: str = ""


@dataclass
class ModelRelay:
    """The bridge's ``invoke_agent``, answered by a Section 3 ``ModelRouter``."""

    router: Any
    agents: Mapping[str, AgentPolicy] = field(default_factory=dict)
    secrets: Sequence[str] = ()
    max_tokens: int = 4096
    temperature: float | None = None
    calls: int = 0
    downgrades: list[dict[str, Any]] = field(default_factory=list)
    redactions: Tally = field(default_factory=Tally)
    exchanges: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_source(
        cls,
        source: str,
        router: Any,
        *,
        path: str = "<memory>",
        profile: str = "agent",
        roles: Mapping[str, ModelRole | str] | None = None,
        secrets: Sequence[str] = (),
        **kwargs: Any,
    ) -> ModelRelay:
        """Read the agent declarations, so a caller cannot invent one."""
        checked = check_source(source, path, profile)
        if not checked.ok:
            raise CheckRefused("the program does not check, so its agents cannot be routed")
        decided = {name: ModelRole(role) for name, role in (roles or {}).items()}
        agents: dict[str, AgentPolicy] = {}
        for decl in checked.program.decls:
            # Agents are declared inside the workflow that uses them; a top-level
            # declaration is also read, so neither placement is silently unrouted.
            for agent in (*getattr(decl, "agents", ()), decl):
                if not isinstance(agent, A.AgentDecl):
                    continue
                settings = {setting.key: _literal(setting.value) for setting in agent.settings}
                agents[agent.name] = AgentPolicy(
                    name=agent.name,
                    label=settings.get("role", ""),
                    role=decided.get(agent.name, ModelRole.PRIMARY),
                    model=settings.get("model") or None,
                    objective=settings.get("objective", ""),
                )
        return cls(router=router, agents=agents, secrets=secrets, **kwargs)

    # -- the bridge door ---------------------------------------------------

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any:
        """One agent operation: scrub, ask, scrub, parse. In that order."""
        policy = self.agents.get(agent) or AgentPolicy(name=agent, label=agent)
        messages = self._messages(policy, operation, payload)
        self.calls += 1
        reply = self._ask(policy, operation, messages)
        text = self._reply_text(policy, operation, reply)
        value = self._parse(policy, operation, text)
        self._record(policy, operation, messages, reply)
        return value

    # -- the three rules ---------------------------------------------------

    def _messages(
        self, policy: AgentPolicy, operation: str, payload: Any
    ) -> list[dict[str, str]]:
        system = _SYSTEM.format(
            agent=policy.name, label=policy.label, objective=policy.objective
        ).strip()
        body = json.dumps(
            {"agent": policy.name, "operation": operation, "input": _plain(payload)},
            sort_keys=True,
            default=str,
        )
        return [
            {"role": "system", "content": self._outbound(system)},
            {"role": "user", "content": self._outbound(body)},
        ]

    def _outbound(self, text: str) -> str:
        """Nothing leaves before it is scrubbed; the report says what was taken."""
        return scrub(text, secrets=self.secrets, tally=self.redactions).text

    def _ask(
        self, policy: AgentPolicy, operation: str, messages: list[dict[str, str]]
    ) -> Any:
        router = self.router
        if router is None or not callable(getattr(router, "call_model", None)):
            raise HostUnavailable(
                "model.invoke needs Section 3's ModelRouter; this host has no model relay wired"
            )
        opts = ModelCallOpts(maxTokens=self.max_tokens, temperature=self.temperature)
        try:
            return router.call_model(
                f"{policy.name}.{operation}", policy.role, messages, opts
            )
        except Exception as error:  # noqa: BLE001 - provider errors carry headers
            raise HostUnavailable(
                f"{policy.name}.{operation} failed in the model relay: "
                f"{type(error).__name__}: {self._inbound(str(error))}"
            ) from error

    def _reply_text(self, policy: AgentPolicy, operation: str, reply: Any) -> str:
        text = getattr(reply, "text", None)
        if text is None and isinstance(reply, Mapping):
            text = reply.get("text")
        if not isinstance(text, str):
            raise InvalidHostResult(
                f"{policy.name}.{operation}: the model relay returned no text "
                f"({type(reply).__name__})"
            )
        answered = getattr(reply, "model", None)
        if policy.model and answered and answered != policy.model:
            # Substitution is Section 3's to make and a reviewer's to see.
            # It is recorded, never retried, never "corrected" by us.
            self.downgrades.append(
                {
                    "agent": policy.name,
                    "operation": operation,
                    "requested": policy.model,
                    "answered": answered,
                    "performed": False,
                }
            )
        return self._inbound(text)

    def _inbound(self, text: str) -> str:
        return scrub(text, secrets=self.secrets, tally=self.redactions).text

    def _parse(self, policy: AgentPolicy, operation: str, text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise InvalidHostResult(
                f"{policy.name}.{operation}: the model relay did not answer with one JSON "
                f"object (got {len(text)} characters starting {text[:40]!r})"
            ) from error

    def _record(
        self,
        policy: AgentPolicy,
        operation: str,
        messages: list[dict[str, str]],
        reply: Any,
    ) -> None:
        """Keep the facts a reviewer needs; never the text that produced them."""
        prompt = "\n".join(message["content"] for message in messages)
        entry = {
            "agent": policy.name,
            "operation": operation,
            "role": str(policy.role),
            "requestedModel": policy.model,
            "answeredModel": getattr(reply, "model", None),
            "promptSha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "promptBytes": len(prompt.encode("utf-8")),
        }
        self.exchanges.append(entry)
        logger.info(
            "model.invoke agent=%s operation=%s role=%s model=%s prompt=%s bytes=%d",
            entry["agent"],
            entry["operation"],
            entry["role"],
            entry["answeredModel"],
            entry["promptSha256"][:16],
            entry["promptBytes"],
        )

    # -- what a reviewer keeps --------------------------------------------

    def report(self) -> dict[str, Any]:
        return {
            "kind": "hx.model/v1",
            "calls": self.calls,
            "router": type(self.router).__name__ if self.router is not None else None,
            "redactions": self.redactions.to_json(),
            "downgrades": list(self.downgrades),
            "exchanges": list(self.exchanges),
        }


def _literal(node: Any) -> str:
    """The declared text of an agent setting; a non-string setting resolves to ""."""
    if isinstance(node, A.StringLit):
        return node.value
    raw = getattr(node, "raw", None)
    return raw if isinstance(raw, str) else ""


def _plain(value: Any) -> Any:
    """Prompt input is JSON, so a runtime object must become plain data first."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "to_json"):
        return _plain(value.to_json())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return REDACTED if isinstance(value, bytes) else str(value)


__all__ = ["AgentPolicy", "ModelRelay"]
