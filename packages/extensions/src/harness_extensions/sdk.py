"""In-process extension SDK — the authoring surface for team extensions.

An extension is a Python module that declares commands and event hooks with
plain decorators. The SDK validates everything locally (names, placeholders,
event types) so mistakes surface at import time, supports in-process dry runs
(``render`` / ``dispatch``) for local development, and pushes declarations to
the extensions service through :class:`ExtensionsClient`.

Design boundary: local hooks run ONLY in processes that host the extension
(local workers, desktop agents). The registry service never imports extension
code — a team's extension cannot execute inside the shared service.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_-]+)\s*\}\}")
_KNOWN_EVENTS = ("tool_call.succeeded", "tool_call.failed")


class ExtensionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LocalEvent:
    eventType: str
    payload: dict


@dataclass
class CommandSpec:
    name: str
    template: str
    arguments: list[str]
    description: str | None
    render: Callable[..., str]  # the decorated callable, for local dry runs


@dataclass
class ExtensionApp:
    name: str
    commands: dict[str, CommandSpec] = field(default_factory=dict)
    hooks: dict[str, list[Callable[[LocalEvent], Any]]] = field(default_factory=dict)

    def command(
        self,
        name: str,
        *,
        template: str | None = None,
        arguments: list[str] | None = None,
        description: str | None = None,
    ):
        """Register a command from a decorated callable.

        The callable's signature supplies ``arguments`` (unless given) and it
        doubles as the local dry-run renderer; ``template`` (or the docstring)
        is what gets pushed to the service, where rendering happens server-side.
        """

        def decorate(fn: Callable[..., str]) -> Callable[..., str]:
            import inspect

            args = arguments
            if args is None:
                args = [p.name for p in inspect.signature(fn).parameters.values()]
            tpl = template if template is not None else (fn.__doc__ or "")
            self._register_command(name, tpl, args, description or fn.__doc__)
            self.commands[name].render = fn
            return fn

        return decorate

    def _register_command(
        self, name: str, template: str, arguments: list[str], description: str | None
    ) -> None:
        if not _NAME_RE.match(name):
            raise ExtensionError("EXT_COMMAND_NAME_INVALID", f"bad command name {name!r}")
        if name in self.commands:
            raise ExtensionError("EXT_COMMAND_DUPLICATE", f"command {name!r} declared twice")
        used = set(_PLACEHOLDER_RE.findall(template))
        undeclared = sorted(used - set(arguments))
        if undeclared:
            raise ExtensionError(
                "EXT_COMMAND_TEMPLATE_INVALID",
                f"template uses undeclared argument(s): {', '.join(undeclared)}",
            )
        self.commands[name] = CommandSpec(
            name=name,
            template=template,
            arguments=sorted(arguments),
            description=description,
            render=lambda **kw: _default_render(template, kw),
        )

    def hook(self, event_type: str):
        if event_type != "*" and event_type not in _KNOWN_EVENTS:
            raise ExtensionError("EXT_HOOK_EVENT_INVALID", f"unknown event type {event_type!r}")

        def decorate(fn: Callable[[LocalEvent], Any]) -> Callable[[LocalEvent], Any]:
            self.hooks.setdefault(event_type, []).append(fn)
            return fn

        return decorate

    # ------------------------------------------------------------- dry runs

    def render(self, name: str, **arguments: Any) -> str:
        spec = self.commands.get(name)
        if spec is None:
            raise ExtensionError("EXT_COMMAND_UNKNOWN", f"no command {name!r}")
        missing = [a for a in spec.arguments if a not in arguments]
        if missing:
            raise ExtensionError(
                "EXT_COMMAND_ARGS_MISSING", f"missing argument(s): {', '.join(missing)}"
            )
        import inspect

        if inspect.isfunction(spec.render) and not spec.render.__name__.startswith("<"):
            return spec.render(**arguments)
        return _default_render(spec.template, arguments)

    def dispatch(self, event_type: str, payload: dict) -> list[Any]:
        """Run local hook callables for one event (in-process dry run)."""
        results = []
        for fn in self.hooks.get(event_type, []) + self.hooks.get("*", []):
            results.append(fn(LocalEvent(eventType=event_type, payload=payload)))
        return results


def _default_render(template: str, arguments: dict) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: str(arguments.get(m.group(1), "")), template)
