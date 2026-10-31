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
import shlex
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from .boundary import plain
from .errors import (
    HostUnavailable,
    HxHostError,
    InvalidHostResult,
    RecordingMismatch,
    from_record,
)
from .secrets import scrub_value

#: Refused by name even for a read-only local root. Same stance as Section 5:
#: the refusal is a property of the path, not of the command that asked.
SECRET_NAMES = frozenset({".env", ".env.local", "id_rsa", "id_ed25519", ".git", ".ssh"})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def _cat_command(path: str) -> str:
    """A read as one allow-listed command, with the path checked before it runs.

    The sandbox would deny an escape anyway; refusing here means the transcript
    says "forbidden path" instead of showing a command that looked permitted.
    """
    if not isinstance(path, str) or not path or path.startswith("/"):
        raise InvalidHostResult(f"repo.read: {path!r} is not a workspace-relative path")
    parts = Path(path).parts
    if any(part in (".", "..") for part in parts) or any(part in SECRET_NAMES for part in parts):
        raise InvalidHostResult(f"repo.read: {path!r} is a forbidden path")
    if path.endswith(SECRET_SUFFIXES):
        raise InvalidHostResult(f"repo.read: {path!r} is a forbidden path")
    return f"cat -- {shlex.quote(path)}"


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


def _agent_reply(handler: Any, agent: str, operation: str, payload: Any) -> Any:
    """A relay is an object with the bridge's own method; anything else is data.

    The distinction is by type, never by trying a call and seeing what fails: a
    host that guessed at arity would turn a wiring mistake into a run that
    almost worked.
    """
    invoke = getattr(handler, "invoke_agent", None)
    if callable(invoke):
        return invoke(agent, operation, payload)
    return handler(payload) if callable(handler) else handler


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
        return _agent_reply(reply, agent, operation, payload)


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


class ConfinedReader:
    """Section 5's streaming ``exec``, adapted to one text read.

    A read is content; a verdict is not — which is why reads use the streaming
    door and tests use the settled one. The cap is not decoration: a workflow
    that reads an enormous generated file must fail with a reason, not exhaust
    the process that is executing it.
    """

    def __init__(self, sandbox: Any, handle: Any, limit: int = 256 * 1024) -> None:
        self.sandbox = sandbox
        self.handle = handle
        self.limit = limit

    def read_text(self, command: str) -> str:
        stream = getattr(self.sandbox, "exec", None)
        if stream is None:
            raise HostUnavailable(
                "this sandbox exposes no exec(); a confined read needs Section 5's exec"
            )
        chunks: list[str] = []
        size = 0

        async def _collect() -> None:
            nonlocal size
            async for line in stream(self.handle, command):
                text = line if isinstance(line, str) else line.decode("utf-8", errors="replace")
                size += len(text)
                if size > self.limit:
                    raise InvalidHostResult(
                        f"confined read exceeded {self.limit} bytes; it is not a file a "
                        "workflow can hold"
                    )
                chunks.append(text)

        _await_blocking(_collect())
        return "".join(chunks)


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
    """The production shape: a reader, Section 5's confined executor, refusals.

    Two ways to read, and the difference is honest: a repository service (Section
    2's protocol) when the host has GitHub, or the confined task itself when it
    has only a sandbox. Neither invents a read the host cannot perform — with
    neither wired, ``repo.read`` refuses.
    """

    confined: ConfinedCommandRunner | None = None
    repo: Any | None = None
    reader: ConfinedReader | None = None
    project_id: str = ""
    ref: str = "main"
    test_commands: Mapping[str, str] = field(default_factory=dict)
    agents: Mapping[str, Any] = field(default_factory=dict)

    def read_file(self, path: str) -> Mapping[str, Any]:
        if self.repo is not None:
            text = self.repo.read_file(self.project_id, self.ref, path)
            if not isinstance(text, str):
                raise InvalidHostResult(f"repo.read returned {type(text).__name__}, expected text")
            return {"path": path, "text": text}
        if self.reader is not None:
            return {"path": path, "text": self.reader.read_text(_cat_command(path))}
        raise HostUnavailable(
            "repo.read needs a repository service adapter or a confined sandbox reader"
        )

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
        return _agent_reply(reply, agent, operation, payload)


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
    """One recorded request and its outcome. A replay is these, and nothing else.

    An exchange has three outcomes, and the third is why this class carries an
    outcome at all: ``in-flight`` means the request was written down before the
    host answered and the answer never arrived — because the process died mid-call.
    A recording full of results is easy to trust; the honest part is saying when
    one is not finished.

    Frozen, and settled by *replacing* the entry rather than mutating it: a
    recorded fact must not be editable after the fact, by this code or anything else.
    """

    operation: str
    arguments: Mapping[str, Any]
    result: Any = None
    outcome: str = "ok"
    failure: Mapping[str, Any] | None = None

    def key(self) -> tuple[str, str]:
        return self.operation, json.dumps(plain(dict(self.arguments)), sort_keys=True, default=str)

    @classmethod
    def in_flight(cls, operation: str, arguments: Mapping[str, Any]) -> BridgeExchange:
        return cls(operation=operation, arguments=dict(arguments), outcome="in-flight")

    @property
    def settled(self) -> bool:
        return self.outcome in ("ok", "failure")

    def to_json(self) -> dict[str, Any]:
        """A durable form that can leave the process: scrubbed, and code-named.

        Codes travel, classes do not. A recording written by a later build must be
        refused by this one rather than reinterpreted by it.
        """
        return {
            "operation": self.operation,
            "arguments": scrub_value(plain(dict(self.arguments))),
            "outcome": self.outcome,
            "result": scrub_value(plain(self.result)) if self.outcome == "ok" else None,
            "failure": scrub_value(dict(self.failure)) if self.failure else None,
        }

    @classmethod
    def from_json(cls, record: Mapping[str, Any]) -> BridgeExchange:
        failure = record.get("failure")
        return cls(
            operation=str(record.get("operation") or ""),
            arguments=dict(record.get("arguments") or {}),
            result=record.get("result"),
            outcome=str(record.get("outcome") or "in-flight"),
            failure=dict(failure) if isinstance(failure, Mapping) else None,
        )

    def answer(self) -> Any:
        """What the host returned, or the failure it returned. Never a guess."""
        if self.outcome == "ok":
            return self.result
        if self.outcome == "failure" and self.failure:
            raise from_record(self.failure)
        raise RecordingMismatch(
            f"the recording's {self.operation} call never settled",
            detail={"operation": self.operation, "outcome": self.outcome},
        )


RECORDING_KIND = "hx.recording/v2"


def recording_to_json(exchanges: Sequence[BridgeExchange]) -> dict[str, Any]:
    """Serialise a recording, versioned and self-describing.

    v2 is the first format that names an outcome per exchange. v1 recorded
    successes only, so its silence about a failure looked like a complete trace —
    which is exactly the thing a reviewer of a replayed run must never have to
    assume. A v1 file is refused rather than read under v2 semantics.
    """
    return {
        "kind": RECORDING_KIND,
        "exchanges": [exchange.to_json() for exchange in exchanges],
    }


def recording_from_json(payload: Mapping[str, Any]) -> list[BridgeExchange]:
    kind = payload.get("kind")
    if kind != RECORDING_KIND:
        raise RecordingMismatch(
            f"this recording is format {str(kind or 'v1 (unlabelled)')!r}; "
            f"{RECORDING_KIND} is what this build replays",
            detail={"found": kind, "expected": RECORDING_KIND},
        )
    raw = payload.get("exchanges")
    if not isinstance(raw, list):
        raise RecordingMismatch("the recording carries no exchange list")
    return [BridgeExchange.from_json(record) for record in raw if isinstance(record, Mapping)]


class RecordingBridge:
    """Wraps a live bridge and keeps every exchange, in completion order.

    Recording is what makes a run inspectable after the fact and re-runnable
    without the host. Since M7 a failure is recorded too, and the request is
    written down *before* the host answers: a host that dies mid-call leaves an
    ``in-flight`` exchange, and a replay refuses to pretend that gap is data.
    """

    def __init__(self, inner: HostBridge) -> None:
        self.inner = inner
        self.exchanges: list[BridgeExchange] = []
        self._lock = threading.Lock()

    def _settle(self, index: int, entry: BridgeExchange, result: Any) -> Any:
        with self._lock:
            self.exchanges[index] = replace(entry, result=result, outcome="ok")
        return result

    def _fail(self, index: int, entry: BridgeExchange, error: HxHostError) -> None:
        with self._lock:
            self.exchanges[index] = replace(
                entry,
                outcome="failure",
                failure={
                    "code": error.code,
                    "status": error.status,
                    "message": error.message,
                    "line": error.line,
                    "col": error.col,
                    "detail": error.detail,
                },
            )

    def _call(
        self, operation: str, arguments: Mapping[str, Any], invoke: Callable[[], Any]
    ) -> Any:
        entry = BridgeExchange.in_flight(operation, arguments)
        with self._lock:
            self.exchanges.append(entry)
            index = len(self.exchanges) - 1
        try:
            result = invoke()
        except HxHostError as error:
            self._fail(index, entry, error)
            raise
        return self._settle(index, entry, result)

    def read_file(self, path: str) -> Mapping[str, Any]:
        return self._call("repo.read", {"path": path}, lambda: self.inner.read_file(path))

    def list_files(self, prefix: str) -> Sequence[Mapping[str, Any]]:
        return self._call("repo.list", {"prefix": prefix}, lambda: self.inner.list_files(prefix))

    def propose_patch(self, changes: Any) -> Mapping[str, Any]:
        arguments = {"changes": plain(changes)}
        return self._call(
            "repo.propose_patch", arguments, lambda: self.inner.propose_patch(changes)
        )

    def run_tests(self, target: Any, suite: str) -> Mapping[str, Any]:
        arguments = {"target": plain(target), "suite": suite}
        return self._call(
            "verify.tests", arguments, lambda: self.inner.run_tests(target, suite)
        )

    def invoke_agent(self, agent: str, operation: str, payload: Any) -> Any:
        arguments = {"agent": agent, "operation": operation, "payload": plain(payload)}
        return self._call(
            "model.invoke",
            arguments,
            lambda: self.inner.invoke_agent(agent, operation, payload),
        )

    def incomplete(self) -> list[str]:
        """Operations recorded but never settled — a host that died mid-call."""
        with self._lock:
            return [entry.operation for entry in self.exchanges if not entry.settled]


class ReplayBridge:
    """Answers from recorded exchanges and never reaches a live host.

    An unrecorded call is refused rather than forwarded: a replay that can
    quietly touch the world is not a replay. Exchanges are matched by operation
    and arguments, so a run whose spawned calls completed in a different order
    still replays; each recorded answer is consumed once.

    A recorded **failure** is re-raised, not skipped: replaying a run that ended
    in an error must reproduce that error, at the same line, with the same code.
    Before M7 a replay of a failing run answered `HXH403` — "nothing recorded" —
    which hid the original failure behind the replay's own.
    """

    def __init__(self, exchanges: Sequence[BridgeExchange]) -> None:
        self._pending: dict[tuple[str, str], list[BridgeExchange]] = {}
        for exchange in exchanges:
            self._pending.setdefault(exchange.key(), []).append(exchange)
        self._lock = threading.Lock()
        self.replayed: list[str] = []
        self.replayed_failures: list[str] = []

    @classmethod
    def from_recording(cls, payload: Mapping[str, Any]) -> ReplayBridge:
        return cls(recording_from_json(payload))

    def _take(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        key = BridgeExchange(operation, arguments).key()
        with self._lock:
            recorded = self._pending.get(key)
            if not recorded:
                raise HostUnavailable(
                    f"replay has no recorded {operation} for these arguments; the "
                    "recording is incomplete or this run asks for something else, and "
                    "a replay never falls back to a live host"
                )
            exchange = recorded.pop(0)
            self.replayed.append(operation)
            if exchange.outcome == "failure":
                self.replayed_failures.append(operation)
        return exchange.answer()

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

    def unplayed(self) -> list[str]:
        """Recorded exchanges this replay never reached.

        Not an error: a replay that stops earlier than the live run leaves answers
        behind, and the evidence says how many, so "it replayed" can be read as
        "it replayed up to here".
        """
        with self._lock:
            return [
                exchange.operation
                for queue in self._pending.values()
                for exchange in queue
            ]
