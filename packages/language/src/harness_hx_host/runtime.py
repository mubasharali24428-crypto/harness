"""The Hx workflow driver: the smallest thing that can honestly execute a checked program.

This is deliberately **not** a general-purpose interpreter. It executes the
construct set the agent profile's target workflow uses — straight-line ``let``,
``await``, ``spawn``/join, ``ensure``, ``if``, ``return``, pure ``fn`` calls and
calls to the closed host-operation table — and refuses anything else with
``HXH102``. There is no loop, no assignment, no closure, no module system, and
no way to name an operation that is not in ``harness_hx.capabilities``.

Three rules are enforced here that the checker can only *describe*:

1. **Authorisation per call.** The decision is consulted at every call site, so
   a capability granted for one workflow is not authority for another, and a
   revocation between calls takes effect.
2. **Budgets are accounting, not advice.** ``steps``, ``tool_calls``, ``tasks``
   and ``duration`` are checked while running and terminate the run. Wall-clock
   duration is checked on every step and every join, so a slow host cannot
   outlive the declared deadline.
3. **Every crossing is recorded.** Each host call emits an ordered event with a
   run id, correlation id, operation id and idempotency key, redacted, so the
   evidence bundle can be replayed against a fake host and compared.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from harness_hx import ast as A
from harness_hx.capabilities import AGENT_OPERATIONS, HOST_OPERATIONS

from .authority import Decision
from .boundary import (
    SchemaValue,
    host_result,
    plain,
    truthy,
    type_name_text,
    validate,
)
from .bridge import HostBridge
from .errors import (
    BudgetExhausted,
    Cancelled,
    CapabilityDenied,
    HostUnavailable,
    HxHostError,
    NotRunnable,
    ScriptError,
)

_DURATION_UNITS_MS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000}
_SENSITIVE = ("secret", "token", "password", "passwd", "api_key", "apikey", "credential", "private")


def _redact(value: Any, depth: int = 0) -> Any:
    """Keys that name a secret never reach an event, a log or the evidence bundle."""
    if depth > 6:
        return "…"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE):
                out[str(key)] = "[redacted]"
            else:
                out[str(key)] = _redact(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value]
    if isinstance(value, SchemaValue):
        return _redact(value.to_json(), depth + 1)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


@dataclass
class Budget:
    """The declared limits, and the counters that make them real."""

    steps: int | None = None
    tool_calls: int | None = None
    tasks: int | None = None
    duration_ms: int | None = None
    used_steps: int = 0
    used_tool_calls: int = 0
    used_tasks: int = 0
    started: float = field(default_factory=time.monotonic)

    @classmethod
    def from_decl(cls, decl: A.BudgetDecl | None) -> Budget:
        budget = cls()
        if decl is None:
            return budget
        for entry in decl.entries:
            raw = entry.value.raw
            if entry.key == "steps":
                budget.steps = int(raw)
            elif entry.key == "tool_calls":
                budget.tool_calls = int(raw)
            elif entry.key == "tasks":
                budget.tasks = int(raw)
            elif entry.key == "duration":
                unit = raw[-2:] if raw.endswith("ms") else raw[-1:]
                budget.duration_ms = int(raw[: -len(unit)]) * _DURATION_UNITS_MS[unit]
        return budget

    def elapsed_ms(self, now: float) -> float:
        return (now - self.started) * 1000.0

    def check_clock(self, now: float, *, line: int | None = None) -> None:
        if self.duration_ms is not None and self.elapsed_ms(now) > self.duration_ms:
            raise BudgetExhausted(
                f"budget.duration ({self.duration_ms}ms) exhausted", line=line
            )

    def spend_step(self, now: float, *, line: int | None = None) -> None:
        self.check_clock(now, line=line)
        self.used_steps += 1
        if self.steps is not None and self.used_steps > self.steps:
            raise BudgetExhausted(f"budget.steps ({self.steps}) exhausted", line=line)

    def spend_tool_call(self, *, line: int | None = None) -> None:
        self.used_tool_calls += 1
        if self.tool_calls is not None and self.used_tool_calls > self.tool_calls:
            raise BudgetExhausted(f"budget.tool_calls ({self.tool_calls}) exhausted", line=line)

    def spend_task(self, *, line: int | None = None) -> None:
        self.used_tasks += 1
        if self.tasks is not None and self.used_tasks > self.tasks:
            raise BudgetExhausted(f"budget.tasks ({self.tasks}) exhausted", line=line)

    def to_json(self) -> dict[str, Any]:
        return {
            "declared": {
                "steps": self.steps,
                "toolCalls": self.tool_calls,
                "tasks": self.tasks,
                "durationMs": self.duration_ms,
            },
            "used": {
                "steps": self.used_steps,
                "toolCalls": self.used_tool_calls,
                "tasks": self.used_tasks,
            },
        }


class _Return(Exception):  # noqa: N818 - a control-flow signal, not an error class
    def __init__(self, value: Any) -> None:
        super().__init__("return")
        self.value = value


@dataclass
class _Join:
    """A spawned effect: a future, and the position that asked for it."""

    future: Future
    line: int
    col: int


class Runtime:
    def __init__(
        self,
        program: A.Program,
        workflow: A.WorkflowDecl,
        decision: Decision,
        bridge: HostBridge,
        *,
        inputs: Mapping[str, Any] | None = None,
        run_id: str,
        correlation_id: str,
        cancel: threading.Event | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.program = program
        self.workflow = workflow
        self.decision = decision
        self.bridge = bridge
        self.inputs = dict(inputs or {})
        self.run_id = run_id
        self.correlation_id = correlation_id
        self.cancel = cancel
        self.clock = clock
        self.budget = Budget.from_decl(workflow.budget)
        # The budget's clock starts when the run does, not when the object was built.
        self.budget.started = self.clock()
        self.events: list[dict[str, Any]] = []
        self._emit_sink = emit
        self._lock = threading.Lock()
        self._seq = 0
        self.schemas = {decl.name: decl for decl in program.decls if isinstance(decl, A.SchemaDecl)}
        self.functions = {decl.name: decl for decl in program.decls if isinstance(decl, A.FnDecl)}
        self.agents = {agent.name: agent for agent in workflow.agents}
        # Spawned work is bounded twice: by budget.tasks (enforced) and by the pool.
        self._pool = ThreadPoolExecutor(max_workers=max(1, min(self.budget.tasks or 1, 16)))
        self._scopes: list[dict[str, Any]] = []

    # -- scope ------------------------------------------------------------

    def _bind(self, name: str, value: Any) -> None:
        self._scopes[-1][name] = value

    def _lookup(self, name: str, node: A.Expr) -> Any:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        raise ScriptError(f"undefined variable '{name}'", line=node.line, col=node.col)

    def _block(self, block: A.Block) -> None:
        self._scopes.append({})
        try:
            for statement in block.statements:
                self._stmt(statement)
        finally:
            self._scopes.pop()

    # -- events -----------------------------------------------------------

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def event(self, kind: str, **fields: Any) -> dict[str, Any]:
        seq = self._next_seq()
        record: dict[str, Any] = {
            "seq": seq,
            "kind": kind,
            "atMs": round(self.budget.elapsed_ms(self.clock()), 3),
            "runId": self.run_id,
            "correlationId": self.correlation_id,
        }
        operation = fields.get("operation")
        if operation:
            record["operationId"] = f"{self.run_id}:{seq}:{operation}"
            record["idempotencyKey"] = hashlib.sha256(
                f"{self.run_id}:{seq}:{operation}".encode()
            ).hexdigest()[:32]
        record.update(fields)
        self.events.append(record)
        if self._emit_sink is not None:
            self._emit_sink(record)
        return record

    # -- statements -------------------------------------------------------

    def _tick(self, line: int, col: int) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("the run was cancelled", line=line, col=col)
        self.budget.spend_step(self.clock(), line=line)

    def _stmt(self, statement: A.Stmt) -> None:
        self._tick(statement.line, statement.col)
        if isinstance(statement, A.LetStmt):
            value = self._eval(statement.value)
            if statement.annotation is not None:
                value = validate(
                    type_name_text(statement.annotation),
                    value,
                    self.schemas,
                    error=ScriptError,
                    where=f"let {statement.name}",
                )
            self._bind(statement.name, value)
            return
        if isinstance(statement, A.EnsureStmt):
            value = self._eval(statement.value)
            if not truthy(value):
                raise ScriptError("ensure failed: the workflow's condition is false",
                                  line=statement.line, col=statement.col)
            self.event("ensure.passed", line=statement.line)
            return
        if isinstance(statement, A.ReturnStmt):
            value = None if statement.value is None else self._eval(statement.value)
            raise _Return(value)
        if isinstance(statement, A.IfStmt):
            if truthy(self._eval(statement.condition)):
                self._block(statement.then)
            elif statement.orelse is not None:
                self._block(statement.orelse)
            return
        if isinstance(statement, A.ExprStmt):
            self._eval(statement.value)
            return
        raise NotRunnable(
            f"{type(statement).__name__} is outside the executable subset",
            line=statement.line,
            col=statement.col,
        )

    # -- expressions ------------------------------------------------------

    def _eval(self, node: A.Expr) -> Any:
        if isinstance(node, A.IntLit):
            return node.value
        if isinstance(node, A.FloatLit):
            return node.value
        if isinstance(node, A.StringLit):
            return node.value
        if isinstance(node, A.BoolLit):
            return node.value
        if isinstance(node, A.DurationLit):
            return node.raw
        if isinstance(node, A.Ident):
            return self._lookup(node.name, node)
        if isinstance(node, A.Member):
            base = self._eval(node.base)
            return self._member(base, node)
        if isinstance(node, A.Await):
            inner = node.value
            if not isinstance(inner, A.Call):
                value = self._eval(inner)
                if isinstance(value, _Join):
                    return self._join(value)
                raise ScriptError("await needs an effect call or a spawned task",
                                  line=node.line, col=node.col)
            return self._call(inner)
        if isinstance(node, A.Spawn):
            inner = node.value
            if not isinstance(inner, A.Call):
                raise ScriptError("spawn needs an effect call", line=node.line, col=node.col)
            name, capability = self._callee(inner)
            if capability is None:
                raise ScriptError("spawn needs an effect, not a pure call",
                                  line=node.line, col=node.col)
            self._require(capability, name, node)
            # The tool call is spent here, on the spawning thread, so a budget
            # stop is attributed to the line that asked — the effect itself
            # must not count it a second time.
            self.budget.spend_tool_call(line=node.line)
            self.budget.spend_task(line=node.line)
            future = self._pool.submit(self._effect, inner, name, spend=False)
            return _Join(future, node.line, node.col)
        if isinstance(node, A.Call):
            return self._call(node)
        if isinstance(node, A.Unary):
            value = self._eval(node.operand)
            if node.op == "not":
                return not truthy(value)
            if node.op == "-":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ScriptError(f"'{node.op}' needs a number", line=node.line, col=node.col)
                return -value
            raise NotRunnable(f"unary '{node.op}' is outside the executable subset",
                              line=node.line, col=node.col)
        if isinstance(node, A.Binary):
            return self._binary(node)
        if isinstance(node, A.ListLit):
            return [self._eval(item) for item in node.items]
        raise NotRunnable(
            f"{type(node).__name__} is outside the executable subset",
            line=node.line,
            col=node.col,
        )

    def _member(self, base: Any, node: A.Member) -> Any:
        if isinstance(base, SchemaValue):
            if node.name not in base.fields:
                raise ScriptError(
                    f"{base.name} has no field '{node.name}'", line=node.line, col=node.col
                )
            return base.fields[node.name]
        raise ScriptError(
            f"cannot read '{node.name}' from {type(base).__name__}", line=node.line, col=node.col
        )

    def _binary(self, node: A.Binary) -> Any:
        op = node.op
        if op == "and":
            left = self._eval(node.left)
            return self._eval(node.right) if truthy(left) else left
        if op == "or":
            left = self._eval(node.left)
            return left if truthy(left) else self._eval(node.right)
        left = self._eval(node.left)
        right = self._eval(node.right)
        if op == "==":
            return plain(left) == plain(right)
        if op == "!=":
            return plain(left) != plain(right)
        if op in {"<", ">", "<=", ">="}:
            try:
                if op == "<":
                    return left < right
                if op == ">":
                    return left > right
                if op == "<=":
                    return left <= right
                return left >= right
            except TypeError as error:
                raise ScriptError(
                    f"cannot compare {type(left).__name__} and {type(right).__name__}",
                    line=node.line,
                    col=node.col,
                ) from error
        if op == "+":
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                if isinstance(left, bool) or isinstance(right, bool):
                    raise ScriptError("'+' needs numbers, not Bool", line=node.line, col=node.col)
                return left + right
            raise ScriptError(
                f"'+' cannot join {type(left).__name__} and {type(right).__name__}",
                line=node.line,
                col=node.col,
            )
        raise NotRunnable(f"binary '{op}' is outside the executable subset",
                          line=node.line, col=node.col)

    # -- calls ------------------------------------------------------------

    def _callee(self, node: A.Call) -> tuple[str, str | None]:
        """Resolve a callee to an operation name and the capability it needs."""
        parts: list[str] = []
        current: A.Expr = node.callee
        while isinstance(current, A.Member):
            parts.append(current.name)
            current = current.base
        if not isinstance(current, A.Ident):
            return "", None
        parts.append(current.name)
        dotted = tuple(reversed(parts))
        if len(dotted) == 2 and dotted[0] in self.agents:
            if dotted[1] not in AGENT_OPERATIONS:
                raise ScriptError(f"'{dotted[0]}' has no operation '{dotted[1]}'",
                                  line=node.line, col=node.col)
            return ".".join(dotted), "model.invoke"
        name = ".".join(dotted)
        operation = HOST_OPERATIONS.get(name)
        if operation is None:
            if name in self.functions:
                return name, None
            raise ScriptError(f"'{name}' is not a callable in this program",
                              line=node.line, col=node.col)
        return name, operation.capability

    def _require(self, capability: str, operation: str, node: A.Expr) -> None:
        if not self.decision.allows(capability):
            raise CapabilityDenied(
                f"'{operation}' needs capability '{capability}', which this host did not grant",
                line=node.line,
                col=node.col,
                detail={"capability": capability, "operation": operation},
            )

    def _bind_arguments(
        self, node: A.Call, params: tuple[tuple[str, str], ...]
    ) -> tuple[dict[str, Any], list[Any]]:
        positional = [self._eval(arg.value) for arg in node.args]
        named = {kwarg.name: self._eval(kwarg.value) for kwarg in node.kwargs}
        names = [name for name, _ in params]
        if len(positional) > len(params):
            raise ScriptError(f"too many arguments for '{names[0]}' call",
                              line=node.line, col=node.col)
        bound: dict[str, Any] = {}
        for index, value in enumerate(positional):
            bound[names[index]] = value
        for key, value in named.items():
            if key not in names:
                raise ScriptError(f"unknown argument '{key}'", line=node.line, col=node.col)
            bound[key] = value
        missing = [name for name in names if name not in bound]
        if missing:
            raise ScriptError(
                f"missing argument(s) {', '.join(missing)}", line=node.line, col=node.col
            )
        for name, type_text in params:
            bound[name] = validate(
                type_text, bound[name], self.schemas, error=ScriptError, where=f"argument {name}"
            )
        return bound, positional

    def _call(self, node: A.Call) -> Any:
        name, capability = self._callee(node)
        if name in self.functions and capability is None:
            return self._user_call(self.functions[name], node)
        if capability is not None:
            return self._effect(node, name)
        return self._pure_call(node, name)

    def _user_call(self, decl: A.FnDecl, node: A.Call) -> Any:
        params = tuple((param.name, type_name_text(param.type)) for param in decl.params)
        bound, _ = self._bind_arguments(node, params)
        self._scopes.append(dict(bound))
        try:
            self._block(decl.body)
        except _Return as returned:
            return validate(
                type_name_text(decl.returns),
                returned.value,
                self.schemas,
                error=ScriptError,
                where=f"return of {decl.name}",
            )
        finally:
            self._scopes.pop()
        raise ScriptError(f"{decl.name} ended without a return", line=node.line, col=node.col)

    def _effect(self, node: A.Call, name: str, *, spend: bool = True) -> Any:
        if "." in name and name.split(".")[0] in self.agents:
            return self._agent_effect(node, name, spend=spend)
        operation = HOST_OPERATIONS[name]
        capability = operation.capability
        if capability is None:  # pragma: no cover - table invariant
            raise NotRunnable(f"'{name}' is not an effect")
        self._require(capability, name, node)
        if spend:
            self.budget.spend_tool_call(line=node.line)
        return self._invoke_bridge(node, operation)

    def _invoke_bridge(self, node: A.Call, operation: Any) -> Any:
        bound, positional = self._bind_arguments(node, operation.params)
        start = self.clock()
        self.event("call.start", operation=operation.name, capability=operation.capability,
                   args=_redact(bound))
        try:
            value = self._dispatch(operation.name, bound, positional)
        except HxHostError as error:
            self.event("call.failed", operation=operation.name, capability=operation.capability,
                       error=error.code)
            raise
        result = host_result(operation.returns, value, self.schemas, operation.name)
        self.event(
            "call.finished",
            operation=operation.name,
            capability=operation.capability,
            ok=True,
            durationMs=round((self.clock() - start) * 1000.0, 3),
        )
        return result

    def _dispatch(self, name: str, bound: Mapping[str, Any], positional: list[Any]) -> Any:
        bridge: HostBridge = self.bridge
        try:
            if name == "repo.read":
                return bridge.read_file(bound["path"])
            if name == "repo.list":
                return bridge.list_files(bound["prefix"])
            if name == "repo.propose_patch":
                return bridge.propose_patch(_bridge_argument(bound["changes"]))
            if name == "verify.tests":
                return bridge.run_tests(_bridge_argument(bound["target"]), bound["suite"])
        except HxHostError:
            raise
        except Exception as error:  # noqa: BLE001 - a host bug becomes a typed failure
            raise HostUnavailable(f"{name} failed in the host: {error}") from error
        raise NotRunnable(f"'{name}' has no bridge method")

    def _pure_call(self, node: A.Call, name: str) -> Any:
        operation = HOST_OPERATIONS.get(name)
        if operation is None:
            raise ScriptError(f"'{name}' is not a callable in this program",
                              line=node.line, col=node.col)
        _bound, positional = self._bind_arguments(node, operation.params)
        if name == "review.bundle":
            # The schema names the record's fields (proposal, evidence); the table
            # names the operation's arguments. Positional order is the only mapping
            # the language defines, so arity must agree exactly rather than guess.
            schema = self.schemas.get(operation.returns)
            if schema is None:
                raise ScriptError(f"no schema named '{operation.returns}' is declared")
            fields = [field.name for field in schema.fields]
            if len(positional) != len(fields):
                raise ScriptError(
                    f"'{name}' takes {len(fields)} argument(s) ({', '.join(fields)}), "
                    f"got {len(positional)}",
                    line=node.line,
                    col=node.col,
                )
            return validate(
                operation.returns,
                dict(zip(fields, positional, strict=True)),
                self.schemas,
                error=ScriptError,
                where=f"{name} result",
            )
        raise NotRunnable(f"pure operation '{name}' is not implemented")

    def _agent_effect(self, node: A.Call, name: str, *, spend: bool = True) -> Any:
        agent_name, operation = name.split(".", 1)
        agent = self.agents[agent_name]
        self._require("model.invoke", name, node)
        if spend:
            self.budget.spend_tool_call(line=node.line)
        bound, positional = self._bind_arguments(node, (("input", "Any"),))
        payload = _bridge_argument(bound["input"])
        self.event("call.start", operation=name, capability="model.invoke",
                   args=_redact({"input": payload}))
        start = self.clock()
        try:
            reply = self.bridge.invoke_agent(agent_name, operation, payload)
        except HxHostError as error:
            self.event("call.failed", operation=name, capability="model.invoke", error=error.code)
            raise
        except Exception as error:  # noqa: BLE001
            raise HostUnavailable(f"{name} failed in the model relay: {error}") from error
        result = host_result(type_name_text(agent.type.args[0]), reply, self.schemas, name)
        self.event(
            "call.finished",
            operation=name,
            capability="model.invoke",
            ok=True,
            durationMs=round((self.clock() - start) * 1000.0, 3),
        )
        return result

    def _join(self, join: _Join) -> Any:
        timeout = None
        if self.budget.duration_ms is not None:
            remaining = self.budget.duration_ms - self.budget.elapsed_ms(self.clock())
            timeout = max(0.0, remaining / 1000.0)
        try:
            return join.future.result(timeout=timeout)
        except TimeoutError as error:
            raise BudgetExhausted(
                f"budget.duration ({self.budget.duration_ms}ms) exhausted while joining a task",
                line=join.line,
                col=join.col,
            ) from error

    # -- entry ------------------------------------------------------------

    def run(self) -> Any:
        params = tuple((param.name, type_name_text(param.type)) for param in self.workflow.params)
        self._scopes.append({})
        try:
            for name, type_text in params:
                if name not in self.inputs:
                    raise ScriptError(f"workflow input '{name}' was not supplied")
                self._scopes[-1][name] = validate(
                    type_text, self.inputs[name], self.schemas, error=ScriptError,
                    where=f"input {name}",
                )
            self.event("run.started", workflow=self.workflow.name, profile="agent")
            try:
                self._block(self.workflow.body)
            except _Return as returned:
                return validate(
                    type_name_text(self.workflow.returns),
                    returned.value,
                    self.schemas,
                    error=ScriptError,
                    where="workflow return",
                )
            raise ScriptError(f"workflow '{self.workflow.name}' ended without a return")
        finally:
            self._scopes.pop()
            self._pool.shutdown(wait=True)


def _bridge_argument(value: Any) -> Any:
    """Hand the bridge plain data: a host must never receive a runtime-only object."""
    return plain(value)
