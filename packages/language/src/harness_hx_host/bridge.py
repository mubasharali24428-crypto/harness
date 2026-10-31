"""The host bridge: the only door out of a running Hx workflow.

A program cannot construct a bridge, choose one, or reach past it. It calls a
name from the closed operation table; the runner maps that name to exactly one
method here; the method is whatever the *host* wired. Three implementations
ship:

* :class:`FakeHostBridge` — deterministic, in-memory, for tests and for the
  evidence harness. No clock, no filesystem, no network.
* :class:`SandboxHostBridge` — the real shape: reads through a repository
  service (Section 2's protocol), runs an allow-listed suite through Section 5's
  confined executor, and refuses anything this host has no adapter for instead
  of inventing one.
* :class:`ReadOnlyRepoBridge` — the local development shape: repository reads
  inside one root, patches as *proposals only*, and a hard refusal for tests and
  models because no sandbox or model relay exists in that process.
* :class:`RecordingBridge` and :class:`ReplayBridge` — wrap any of the above:
  one keeps every exchange, the other answers from those exchanges alone.

None of them writes, merges or approves. ``repo.propose_patch`` returns a
``Patch`` value that a human reviews; it never applies an edit.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .boundary import plain
from .errors import HostUnavailable, InvalidHostResult

#: Refused by name even for a read-only local root. Same stance as Section 5:
#: the refusal is a property of the path, not of the command that asked.
SECRET_NAMES = frozenset({".env", ".env.local", "id_rsa", "id_ed25519", ".git", ".ssh"})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


@dataclass(frozen=True)
class BridgeCall:
    """One recorded crossing of the boundary. Evidence is built from these."""

    operation: str
    arguments: Mapping[str, Any]


class HostBridge(Protocol):
    """What every host must provide. One method per entry in ``HOST_OPERATIONS``."""

    def read_file(self, path: str) -> Mapping[str, Any]: ...

    def list_files(self, prefix: str) -> Sequence[Mapping[str, Any]]: ...

    def propose_patch(self, changes: Any) -> Mapping[str, Any]: ...

    def run_tests(self, target: Any, suite: str) -> Mapping[str, Any]: ...

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any: ...


def _patch_value(changes: Any) -> dict[str, str]:
    """A patch is a proposal: an id and a diff, computed, never applied."""
    if isinstance(changes, Mapping) and isinstance(changes.get("patch"), str):
        diff = changes["patch"]
    else:
        diff = json.dumps(changes, sort_keys=True, default=str)
    digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    return {"id": f"patch-{digest[:16]}", "diff": diff}


@dataclass
class FakeHostBridge:
    """Deterministic host for tests: fixed files, fixed evidence, recorded calls."""

    files: dict[str, str] = field(
        default_factory=lambda: {
            "README.md": "# demo\n",
            "docs/architecture-map.md": "# map\n",
        }
    )
    tests: Mapping[str, Any] = field(default_factory=lambda: {"passed": True, "detail": "exit 0"})
    agents: Mapping[str, Any] = field(default_factory=dict)
    calls: list[BridgeCall] = field(default_factory=list)
    fail_reads: frozenset[str] = frozenset()

    def read_file(self, path: str) -> dict[str, Any]:
        self.calls.append(BridgeCall("repo.read", {"path": path}))
        if path in self.fail_reads:
            raise InvalidHostResult(f"repo.read could not read {path!r}")
        if path not in self.files:
            raise InvalidHostResult(f"repo.read: no such file {path!r}")
        return {"path": path, "text": self.files[path]}

    def list_files(self, prefix: str) -> list[dict[str, Any]]:
        self.calls.append(BridgeCall("repo.list", {"prefix": prefix}))
        return [
            {"path": name, "text": text}
            for name, text in sorted(self.files.items())
            if name.startswith(prefix)
        ]

    def propose_patch(self, changes: Any) -> dict[str, str]:
        self.calls.append(BridgeCall("repo.propose_patch", {"changes": changes}))
        return _patch_value(changes)

    def run_tests(self, target: Any, suite: str) -> Mapping[str, Any]:
        self.calls.append(BridgeCall("verify.tests", {"target": target, "suite": suite}))
        return self.tests

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any:
        self.calls.append(
            BridgeCall("model.invoke", {"agent": agent, "operation": operation, "payload": payload})
        )
        reply = self.agents.get(agent)
        if reply is None:
            raise HostUnavailable(f"no reply configured for agent {agent!r}")
        return reply(payload) if callable(reply) else reply


class ConfinedCommandRunner:
    """Section 5's confined command boundary, adapted to one synchronous call.

    Section 5 owns confinement; this class adds nothing to it. ``exec_structured``
    is a coroutine returning a settled ``StructuredExecResult`` (status, exit
    code, counts), which is the only shape that can honestly become ``Evidence``.
    A stream of lines has no exit code yet, so it is refused rather than guessed.
    """

    def __init__(self, sandbox: Any, handle: Any) -> None:
        self.sandbox = sandbox
        self.handle = handle

    def run(self, command: str) -> dict[str, Any]:
        structured = getattr(self.sandbox, "exec_structured", None)
        if structured is None:
            raise HostUnavailable(
                "this sandbox exposes only streaming exec(); Evidence needs a settled exit code "
                "(Section 5's exec_structured)"
            )
        result = structured(self.handle, command)
        if inspect.iscoroutine(result) or inspect.isawaitable(result):
            result = _await_blocking(result)
        exit_code = getattr(result, "exit_code", None)
        if exit_code is None and isinstance(result, Mapping):
            exit_code = result.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise InvalidHostResult(
                f"confined exec for {command!r} returned no integer exit code "
                f"({type(result).__name__})"
            )
        summary = getattr(result, "summary", None) or ""
        return {
            "passed": exit_code == 0,
            "detail": f"exit {exit_code}" + (f": {summary}" if summary else ""),
        }


def _await_blocking(awaitable: Any) -> Any:
    """Run an awaitable to completion from synchronous code, loop or no loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    outcome: dict[str, Any] = {}

    def _runner() -> None:
        try:
            outcome["value"] = asyncio.run(awaitable)
        except BaseException as error:  # noqa: BLE001 - re-raised on the caller's thread
            outcome["error"] = error

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


@dataclass
class SandboxHostBridge:
    """The production shape: Section 2 reads, Section 5 runs, this host refuses the rest."""

    confined: ConfinedCommandRunner | None = None
    repo: Any | None = None
    project_id: str = ""
    ref: str = "main"
    test_commands: Mapping[str, str] = field(default_factory=dict)
    agents: Mapping[str, Any] = field(default_factory=dict)

    def read_file(self, path: str) -> Mapping[str, Any]:
        if self.repo is None:
            raise HostUnavailable("repo.read needs a repository service adapter")
        text = self.repo.read_file(self.project_id, self.ref, path)
        if not isinstance(text, str):
            raise InvalidHostResult(f"repo.read returned {type(text).__name__}, expected text")
        return {"path": path, "text": text}

    def list_files(self, prefix: str) -> list[dict[str, Any]]:
        if self.repo is None:
            raise HostUnavailable("repo.list needs a repository service adapter")
        tree = self.repo.read_repo_tree(self.project_id, self.ref)
        entries: list[dict[str, Any]] = []
        for entry in tree:
            path = entry.get("path") if isinstance(entry, Mapping) else None
            if isinstance(path, str) and path.startswith(prefix):
                entries.append({"path": path, "text": ""})
        return entries

    def propose_patch(self, changes: Any) -> dict[str, str]:
        return _patch_value(changes)

    def run_tests(self, target: Any, suite: str) -> Mapping[str, Any]:
        command = self.test_commands.get(suite)
        if command is None:
            raise HostUnavailable(f"no allow-listed test command for suite {suite!r}")
        if self.confined is None:
            raise HostUnavailable("verify.tests needs a confined executor")
        return self.confined.run(command)

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any:
        reply = self.agents.get(agent)
        if reply is None:
            raise HostUnavailable(f"no model relay wired for agent {agent!r}")
        return reply(payload) if callable(reply) else reply


@dataclass
class ReadOnlyRepoBridge:
    """Local development: read one root, propose patches, refuse everything else."""

    root: Path
    calls: list[BridgeCall] = field(default_factory=list)

    def _resolve(self, path: str) -> Path:
        if any(part in SECRET_NAMES for part in Path(path).parts):
            raise InvalidHostResult(f"repo.read: {path!r} is a forbidden path")
        if path.endswith(SECRET_SUFFIXES):
            raise InvalidHostResult(f"repo.read: {path!r} is a forbidden path")
        target = (self.root / path).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise InvalidHostResult(f"repo.read: {path!r} escapes the allowed root")
        return target

    def read_file(self, path: str) -> Mapping[str, Any]:
        self.calls.append(BridgeCall("repo.read", {"path": path}))
        target = self._resolve(path)
        if not target.is_file():
            raise InvalidHostResult(f"repo.read: no such file {path!r}")
        return {"path": path, "text": target.read_text(encoding="utf-8")}

    def list_files(self, prefix: str) -> list[dict[str, Any]]:
        self.calls.append(BridgeCall("repo.list", {"prefix": prefix}))
        root = self.root.resolve()
        entries = []
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            if relative.startswith(prefix) and not any(
                part in SECRET_NAMES for part in candidate.relative_to(root).parts
            ):
                entries.append({"path": relative, "text": ""})
        return entries

    def propose_patch(self, changes: Any) -> dict[str, str]:
        self.calls.append(BridgeCall("repo.propose_patch", {"changes": changes}))
        return _patch_value(changes)

    def run_tests(self, target: Any, suite: str) -> Mapping[str, Any]:
        self.calls.append(BridgeCall("verify.tests", {"target": target, "suite": suite}))
        raise HostUnavailable(
            "verify.tests is not available in the local read-only bridge; "
            "it needs Section 5's confined executor"
        )

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any:
        self.calls.append(BridgeCall("model.invoke", {"agent": agent, "operation": operation}))
        raise HostUnavailable(
            "model.invoke is not available in the local read-only bridge; "
            "it needs the Harness model relay"
        )


BridgeFactory = Callable[[], HostBridge]


@dataclass(frozen=True)
class BridgeExchange:
    """One recorded request and response. A replay is these, and nothing else."""

    operation: str
    arguments: Mapping[str, Any]
    result: Any = None

    def key(self) -> tuple[str, str]:
        return self.operation, json.dumps(plain(dict(self.arguments)), sort_keys=True, default=str)


class RecordingBridge:
    """Wraps a live bridge and keeps every exchange, in completion order.

    Recording is what makes a run inspectable after the fact and re-runnable
    without the host. Only successful exchanges are recorded: a failure is a
    fact about the host, not a value that can be replayed.
    """

    def __init__(self, inner: HostBridge) -> None:
        self.inner = inner
        self.exchanges: list[BridgeExchange] = []
        self._lock = threading.Lock()

    def _record(self, operation: str, arguments: Mapping[str, Any], result: Any) -> Any:
        with self._lock:
            self.exchanges.append(BridgeExchange(operation, arguments, result))
        return result

    def read_file(self, path: str) -> Mapping[str, Any]:
        return self._record("repo.read", {"path": path}, self.inner.read_file(path))

    def list_files(self, prefix: str) -> Sequence[Mapping[str, Any]]:
        return self._record("repo.list", {"prefix": prefix}, self.inner.list_files(prefix))

    def propose_patch(self, changes: Any) -> Mapping[str, Any]:
        return self._record(
            "repo.propose_patch", {"changes": plain(changes)}, self.inner.propose_patch(changes)
        )

    def run_tests(self, target: Any, suite: str) -> Mapping[str, Any]:
        return self._record(
            "verify.tests",
            {"target": plain(target), "suite": suite},
            self.inner.run_tests(target, suite),
        )

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any:
        return self._record(
            "model.invoke",
            {"agent": agent, "operation": operation, "payload": plain(payload)},
            self.inner.invoke_agent(agent, operation, payload),
        )


class ReplayBridge:
    """Answers from recorded exchanges and never reaches a live host.

    An unrecorded call is refused rather than forwarded: a replay that can
    quietly touch the world is not a replay. Exchanges are matched by operation
    and arguments, so a run whose spawned calls completed in a different order
    still replays; each recorded answer is consumed once.
    """

    def __init__(self, exchanges: Sequence[BridgeExchange]) -> None:
        self._pending: dict[tuple[str, str], list[Any]] = {}
        for exchange in exchanges:
            self._pending.setdefault(exchange.key(), []).append(exchange.result)
        self._lock = threading.Lock()
        self.replayed: list[str] = []

    def _take(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        key = BridgeExchange(operation, arguments).key()
        with self._lock:
            recorded = self._pending.get(key)
            if not recorded:
                raise HostUnavailable(
                    f"replay has no recorded {operation} for these arguments; "
                    "a replay never falls back to a live host"
                )
            result = recorded.pop(0)
            self.replayed.append(operation)
        return result

    def read_file(self, path: str) -> Any:
        return self._take("repo.read", {"path": path})

    def list_files(self, prefix: str) -> Any:
        return self._take("repo.list", {"prefix": prefix})

    def propose_patch(self, changes: Any) -> Any:
        return self._take("repo.propose_patch", {"changes": plain(changes)})

    def run_tests(self, target: Any, suite: str) -> Any:
        return self._take("verify.tests", {"target": plain(target), "suite": suite})

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any:
        return self._take(
            "model.invoke", {"agent": agent, "operation": operation, "payload": plain(payload)}
        )
