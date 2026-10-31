"""The Hx checker: names, types, capabilities, budgets, profiles.

The rules exist to make three claims true that the roadmap could not make
before: a program can only *request* authority, an effect must be declared and
awaited, and a workflow runs under a budget it cannot widen. Everything here is
static — there is no interpreter in this package — so the checker's job is to
fail loudly at the earliest point where a claim would otherwise be false.

``RuleSet`` exists so the test suite can disable one rule at a time and prove
the suite notices. A validator nobody has tried to fool is a validator nobody
should trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import ast as A
from .capabilities import (
    AGENT_OPERATIONS,
    CAPABILITIES,
    FORBIDDEN_AMBIENT,
    FORBIDDEN_AUTHORITY,
    HOST_OPERATIONS,
    HostOperation,
    capability_names,
)
from .diagnostics import Reporter

PRIMITIVES = frozenset({"Int", "Float", "String", "Bool", "Duration", "Money", "Any"})
GENERIC_ARITY = {"List": 1, "Map": 2, "Agent": 1}
NUMERIC = frozenset({"Int", "Float"})

BUDGET_KEYS = ("steps", "tool_calls", "tasks", "duration", "cost")
BUDGET_COUNT_KEYS = frozenset({"steps", "tool_calls", "tasks"})
MAX_BUDGET_UNITS = 1_000_000
MAX_DURATION_MS = 24 * 60 * 60 * 1000
MAX_COST = Decimal("1000.00")

AGENT_SETTINGS = ("role", "objective", "model")

_DURATION_RE = re.compile(r"^(\d+)(ms|s|m|h)$")
_DURATION_UNIT_MS = {"ms": 1, "s": 1_000, "m": 60_000, "h": 3_600_000}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class RuleSet:
    """Which rules are on. Tests flip one off to check that it was load-bearing."""

    enforce_capabilities: bool = True
    require_awaited_effects: bool = True
    forbid_ambient: bool = True


@dataclass(frozen=True)
class AgentInfo:
    name: str
    payload: str
    decl: A.AgentDecl


class Scope:
    def __init__(self, parent: Scope | None = None) -> None:
        self.parent = parent
        self.names: dict[str, str] = {}

    def define(self, name: str, value_type: str) -> None:
        self.names[name] = value_type

    def resolve(self, name: str) -> str | None:
        scope: Scope | None = self
        while scope is not None:
            if name in scope.names:
                return scope.names[name]
            scope = scope.parent
        return None


@dataclass
class Context:
    kind: str
    returns: str
    scope: Scope
    agents: dict[str, AgentInfo] = field(default_factory=dict)
    declared: set[str] = field(default_factory=set)
    declared_at: dict[str, A.Capability] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)
    spawned: dict[str, tuple[str, A.Spawn]] = field(default_factory=dict)
    awaited: set[str] = field(default_factory=set)
    budget: dict[str, object] = field(default_factory=dict)
    spawn_count: int = 0
    spawn_over_budget_reported: bool = False


class Checker:
    def __init__(
        self,
        program: A.Program,
        profile: str,
        reporter: Reporter,
        rules: RuleSet | None = None,
    ) -> None:
        self.program = program
        self.profile = profile
        self.report = reporter
        self.rules = rules or RuleSet()
        self.schemas: dict[str, A.SchemaDecl] = {}
        self.enums: dict[str, A.EnumDecl] = {}
        self.fns: dict[str, A.FnDecl] = {}
        self.agents: dict[str, A.AgentDecl] = {}
        self.field_types: dict[str, dict[str, str]] = {}

    # -- entry ------------------------------------------------------------
    def run(self) -> None:
        self._collect()
        for decl in self.program.decls:
            if isinstance(decl, A.SchemaDecl):
                self._check_schema(decl)
            elif isinstance(decl, A.EnumDecl):
                self._check_enum(decl)
            elif isinstance(decl, A.FnDecl):
                self._check_fn(decl)
            elif isinstance(decl, A.AgentDecl):
                self._check_agent(decl)
            elif isinstance(decl, A.WorkflowDecl):
                self._check_workflow(decl)

    def _collect(self) -> None:
        seen: dict[str, str] = {}
        for decl in self.program.decls:
            name = decl.name
            kind = type(decl).__name__.removesuffix("Decl").lower()
            if name in seen:
                self.report.error(
                    "HX3006",
                    f"{kind} '{name}' is already declared as a {seen[name]}",
                    decl.line,
                    decl.col,
                )
                continue
            seen[name] = kind
            if isinstance(decl, A.SchemaDecl):
                self.schemas[name] = decl
            elif isinstance(decl, A.EnumDecl):
                self.enums[name] = decl
            elif isinstance(decl, A.FnDecl):
                self.fns[name] = decl
            elif isinstance(decl, A.AgentDecl):
                self.agents[name] = decl

    # -- declarations -----------------------------------------------------
    def _check_schema(self, decl: A.SchemaDecl) -> None:
        fields: dict[str, str] = {}
        for schema_field in decl.fields:
            if schema_field.name in fields:
                self.report.error(
                    "HX3006",
                    f"duplicate field '{schema_field.name}' in schema '{decl.name}'",
                    schema_field.line,
                    schema_field.col,
                )
                continue
            resolved = self._resolve(schema_field.type, allow_agent=False)
            fields[schema_field.name] = resolved or "Any"
            if schema_field.default is not None:
                context = self._bare_context(resolved or "Any")
                actual, _ = self._expr(schema_field.default, context)
                if not self._compatible(fields[schema_field.name], actual):
                    self.report.error(
                        "HX3002",
                        f"default for '{schema_field.name}' expects "
                        f"{fields[schema_field.name]}, found {actual}",
                        schema_field.line,
                        schema_field.col,
                    )
        self.field_types[decl.name] = fields

    def _check_enum(self, decl: A.EnumDecl) -> None:
        seen: set[str] = set()
        for member in decl.members:
            if member.name in seen:
                self.report.error(
                    "HX3006",
                    f"duplicate enum member '{member.name}' in '{decl.name}'",
                    member.line,
                    member.col,
                )
            seen.add(member.name)

    def _check_fn(self, decl: A.FnDecl) -> None:
        context = self._bare_context(self._resolve(decl.returns) or "Any")
        self._bind_params(decl.params, context)
        self._block(decl.body, context)
        if not self._ends_with_return(decl.body) and context.returns not in {"Any", "Unit"}:
            self.report.error(
                "HX3007",
                f"fn '{decl.name}' must end with a return statement",
                decl.line,
                decl.col,
            )

    def _check_agent(self, decl: A.AgentDecl) -> AgentInfo | None:
        if self.profile != "agent":
            self.report.error(
                "HX6001",
                f"agent '{decl.name}' requires the agent profile, which the host selects",
                decl.line,
                decl.col,
            )
            return None
        resolved = self._resolve(decl.type, allow_agent=True)
        payload = "Any"
        if resolved and resolved.startswith("Agent<") and resolved.endswith(">"):
            payload = resolved[len("Agent<") : -1]
        elif resolved in {"Any", None}:
            return None
        else:
            self.report.error(
                "HX3001",
                f"agent '{decl.name}' must be declared as Agent<T>, found '{resolved}'",
                decl.line,
                decl.col,
            )
            return None
        settings: dict[str, A.Expr] = {}
        context = self._bare_context("Any")
        for setting in decl.settings:
            if setting.key not in AGENT_SETTINGS:
                self.report.error(
                    "HX3009",
                    f"unknown agent setting '{setting.key}' "
                    f"(known: {', '.join(AGENT_SETTINGS)})",
                    setting.line,
                    setting.col,
                )
                continue
            if setting.key in settings:
                self.report.error(
                    "HX3006",
                    f"duplicate agent setting '{setting.key}'",
                    setting.line,
                    setting.col,
                )
                continue
            actual, _ = self._expr(setting.value, context)
            if not self._compatible("String", actual):
                self.report.error(
                    "HX3002",
                    f"agent setting '{setting.key}' expects String, found {actual}",
                    setting.line,
                    setting.col,
                )
            settings[setting.key] = setting.value
        if "role" not in settings:
            self.report.error(
                "HX3010",
                f"agent '{decl.name}' must declare a role",
                decl.line,
                decl.col,
            )
        return AgentInfo(decl.name, payload, decl)

    def _check_workflow(self, decl: A.WorkflowDecl) -> None:
        if self.profile != "agent":
            self.report.error(
                "HX6001",
                f"workflow '{decl.name}' requires the agent profile, which the host selects",
                decl.line,
                decl.col,
            )
            return
        returns = self._resolve(decl.returns) or "Any"
        context = self._bare_context(returns)
        context.kind = "workflow"
        self._bind_params(decl.params, context)

        if decl.requires is not None:
            for capability in decl.requires.capabilities:
                if capability.name not in CAPABILITIES:
                    self.report.error(
                        "HX4002",
                        f"unknown capability '{capability.name}' "
                        f"(known: {capability_names()})",
                        capability.line,
                        capability.col,
                    )
                    continue
                if capability.name in context.declared:
                    self.report.warning(
                        "HX7005",
                        f"capability '{capability.name}' is declared more than once",
                        capability.line,
                        capability.col,
                    )
                    continue
                context.declared.add(capability.name)
                context.declared_at[capability.name] = capability

        if decl.budget is None:
            self.report.warning(
                "HX7002",
                f"workflow '{decl.name}' declares no budget; host defaults apply",
                decl.line,
                decl.col,
            )
        else:
            context.budget = self._check_budget(decl.budget)

        for agent in decl.agents:
            info = self._check_agent(agent)
            if info is None:
                continue
            if info.name in context.agents:
                self.report.error(
                    "HX3006",
                    f"agent '{info.name}' is already declared in this workflow",
                    agent.line,
                    agent.col,
                )
                continue
            context.agents[info.name] = info

        self._block(decl.body, context)

        if not self._ends_with_return(decl.body):
            self.report.error(
                "HX3007",
                f"workflow '{decl.name}' must end with a return statement",
                decl.line,
                decl.col,
            )

        for name, capability in context.declared_at.items():
            if name not in context.used:
                self.report.warning(
                    "HX7001",
                    f"capability '{name}' is declared but never used",
                    capability.line,
                    capability.col,
                )

        for name, (_, node) in context.spawned.items():
            if name not in context.awaited:
                self.report.error(
                    "HX4007",
                    f"task '{name}' is spawned but never awaited",
                    node.line,
                    node.col,
                )

        if context.spawn_count and context.budget.get("tasks") is None:
            self.report.warning(
                "HX7003",
                "spawn is used without a budget.tasks limit",
                decl.line,
                decl.col,
            )

    # -- budgets ----------------------------------------------------------
    def _check_budget(self, decl: A.BudgetDecl) -> dict[str, object]:
        values: dict[str, object] = {}
        seen: set[str] = set()
        for entry in decl.entries:
            if entry.key not in BUDGET_KEYS:
                self.report.error(
                    "HX5001",
                    f"unknown budget key '{entry.key}' (known: {', '.join(BUDGET_KEYS)})",
                    entry.line,
                    entry.col,
                )
                continue
            if entry.key in seen:
                self.report.error(
                    "HX5002",
                    f"duplicate budget key '{entry.key}'",
                    entry.line,
                    entry.col,
                )
                continue
            seen.add(entry.key)
            parsed = self._budget_value(entry)
            if parsed is not None:
                values[entry.key] = parsed
        return values

    def _budget_value(self, entry: A.BudgetEntry) -> object | None:
        value = entry.value
        if entry.key in BUDGET_COUNT_KEYS:
            if value.kind != "int":
                self.report.error(
                    "HX5004",
                    f"'{entry.key}' must be a positive integer",
                    value.line,
                    value.col,
                )
                return None
            number = int(value.raw)
            if not 1 <= number <= MAX_BUDGET_UNITS:
                self.report.error(
                    "HX5003",
                    f"'{entry.key}' must be between 1 and {MAX_BUDGET_UNITS}",
                    value.line,
                    value.col,
                )
                return None
            return number
        if entry.key == "duration":
            if value.kind != "duration":
                self.report.error(
                    "HX5004",
                    "'duration' must be a duration such as 10m or 90s",
                    value.line,
                    value.col,
                )
                return None
            match = _DURATION_RE.match(value.raw)
            if match is None:
                self.report.error(
                    "HX5004",
                    f"invalid duration '{value.raw}'",
                    value.line,
                    value.col,
                )
                return None
            milliseconds = int(match.group(1)) * _DURATION_UNIT_MS[match.group(2)]
            if not 1 <= milliseconds <= MAX_DURATION_MS:
                self.report.error(
                    "HX5003",
                    "'duration' must be between 1ms and 24h",
                    value.line,
                    value.col,
                )
                return None
            return milliseconds
        # cost
        if value.kind not in {"int", "float"} or value.currency is None:
            self.report.error(
                "HX5004",
                "'cost' must be money with a currency, e.g. 2.00 USD",
                value.line,
                value.col,
            )
            return None
        if value.currency != "USD":
            self.report.error(
                "HX5004",
                f"unsupported currency '{value.currency}' (only USD in spec 0.1.0)",
                value.line,
                value.col,
            )
            return None
        try:
            amount = Decimal(value.raw)
        except InvalidOperation:
            self.report.error("HX5004", f"invalid amount '{value.raw}'", value.line, value.col)
            return None
        if not Decimal("0") < amount <= MAX_COST:
            self.report.error(
                "HX5003",
                "'cost' must be greater than 0 and at most 1000.00 USD",
                value.line,
                value.col,
            )
            return None
        return amount

    # -- statements -------------------------------------------------------
    def _bind_params(self, params: tuple[A.Param, ...], context: Context) -> None:
        for param in params:
            resolved = self._resolve(param.type, allow_agent=False) or "Any"
            if context.scope.resolve(param.name) is not None:
                self.report.error(
                    "HX3006",
                    f"duplicate parameter '{param.name}'",
                    param.line,
                    param.col,
                )
                continue
            context.scope.define(param.name, resolved)

    def _block(self, block: A.Block, context: Context) -> None:
        for statement in block.statements:
            self._stmt(statement, context)

    def _stmt(self, statement: A.Stmt, context: Context) -> None:
        if isinstance(statement, A.LetStmt):
            actual, _ = self._expr(statement.value, context)
            declared = (
                self._resolve(statement.annotation, allow_agent=False)
                if statement.annotation is not None
                else None
            )
            if declared is not None and not self._compatible(declared, actual):
                self.report.error(
                    "HX3002",
                    f"'{statement.name}' is declared as {declared} but the value is {actual}",
                    statement.line,
                    statement.col,
                )
            context.scope.define(statement.name, declared or actual)
            if isinstance(statement.value, A.Spawn) and actual.startswith("Task<"):
                context.spawned[statement.name] = (actual[5:-1], statement.value)
            return
        if isinstance(statement, A.EnsureStmt):
            actual, _ = self._expr(statement.value, context)
            if actual not in {"Bool", "Any"}:
                self.report.error(
                    "HX3002",
                    f"'ensure' expects Bool, found {actual}",
                    statement.line,
                    statement.col,
                )
            return
        if isinstance(statement, A.ReturnStmt):
            if statement.value is None:
                if context.returns not in {"Any", "Unit"}:
                    self.report.error(
                        "HX3002",
                        f"expected a {context.returns} to return, found no value",
                        statement.line,
                        statement.col,
                    )
                return
            actual, _ = self._expr(statement.value, context)
            if not self._compatible(context.returns, actual):
                self.report.error(
                    "HX3002",
                    f"returning {actual} from a declaration that returns {context.returns}",
                    statement.line,
                    statement.col,
                )
            return
        if isinstance(statement, A.IfStmt):
            actual, _ = self._expr(statement.condition, context)
            if actual not in {"Bool", "Any"}:
                self.report.error(
                    "HX3002",
                    f"'if' expects Bool, found {actual}",
                    statement.line,
                    statement.col,
                )
            self._block(statement.then, context)
            if statement.orelse is not None:
                self._block(statement.orelse, context)
            return
        if isinstance(statement, A.ExprStmt):
            self._expr(statement.value, context)

    # -- expressions ------------------------------------------------------
    def _expr(self, node: A.Expr, context: Context, allow_effect: bool = False) -> tuple[str, bool]:
        if isinstance(node, A.IntLit):
            return "Int", False
        if isinstance(node, A.FloatLit):
            return "Float", False
        if isinstance(node, A.StringLit):
            return "String", False
        if isinstance(node, A.BoolLit):
            return "Bool", False
        if isinstance(node, A.DurationLit):
            return "Duration", False
        if isinstance(node, A.Ident):
            return self._ident(node, context)
        if isinstance(node, A.Member):
            return self._member(node, context)
        if isinstance(node, A.Call):
            return self._call(node, context, allow_effect)
        if isinstance(node, A.Await):
            return self._await(node, context)
        if isinstance(node, A.Spawn):
            return self._spawn(node, context)
        if isinstance(node, A.Unary):
            operand, _ = self._expr(node.operand, context)
            if operand not in {"Bool", "Any"}:
                self.report.error(
                    "HX3012",
                    f"'{node.op}' expects Bool, found {operand}",
                    node.line,
                    node.col,
                )
            return "Bool", False
        if isinstance(node, A.Binary):
            return self._binary(node, context)
        if isinstance(node, A.ListLit):
            item_type = "Any"
            for index, item in enumerate(node.items):
                actual, _ = self._expr(item, context)
                if index == 0:
                    item_type = actual
                elif not self._compatible(item_type, actual):
                    self.report.error(
                        "HX3002",
                        f"list mixes {item_type} and {actual}",
                        node.line,
                        node.col,
                    )
            return f"List<{item_type}>", False
        return "Any", False

    def _ident(self, node: A.Ident, context: Context) -> tuple[str, bool]:
        if self.rules.forbid_ambient and node.name in FORBIDDEN_AMBIENT:
            self.report.error(
                "HX4004",
                f"'{node.name}' is an ambient host operation; no profile provides it",
                node.line,
                node.col,
            )
            return "Any", False
        if node.name in context.agents:
            info = context.agents[node.name]
            return f"Agent<{info.payload}>", False
        resolved = context.scope.resolve(node.name)
        if resolved is None:
            self.report.error(
                "HX3005",
                f"unknown identifier '{node.name}'",
                node.line,
                node.col,
            )
            return "Any", False
        return resolved, False

    def _member(self, node: A.Member, context: Context) -> tuple[str, bool]:
        base_type, _ = self._expr(node.base, context)
        inner = self._optional_inner(base_type)
        fields = self.field_types.get(inner)
        if fields is not None:
            if node.name not in fields:
                self.report.error(
                    "HX3004",
                    f"schema '{inner}' has no field '{node.name}'",
                    node.line,
                    node.col,
                )
                return "Any", False
            return fields[node.name], False
        if base_type == "Any":
            return "Any", False
        self.report.error(
            "HX3011",
            f"cannot read member '{node.name}' of type {base_type}",
            node.line,
            node.col,
        )
        return "Any", False

    def _call(self, node: A.Call, context: Context, allow_effect: bool) -> tuple[str, bool]:
        dotted = self._dotted(node.callee)
        if dotted is None:
            self._expr(node.callee, context)
            self.report.error(
                "HX3011",
                "this expression is not callable",
                node.line,
                node.col,
            )
            return "Any", False
        name = ".".join(dotted)
        if len(dotted) == 2:
            base, operation = dotted
            if base in context.agents:
                agent = context.agents[base]
                return self._agent_call(node, context, agent, operation, allow_effect)
            if base in FORBIDDEN_AUTHORITY and self.rules.forbid_ambient:
                self.report.error(
                    "HX4006",
                    f"'{name}' would change authority; source requests capabilities and the "
                    "host policy decides",
                    node.line,
                    node.col,
                )
                self._check_args_loose(node, context)
                return "Any", True
            if name in HOST_OPERATIONS:
                return self._host_call(node, context, name, allow_effect)
            if base in FORBIDDEN_AMBIENT and self.rules.forbid_ambient:
                self.report.error(
                    "HX4004",
                    f"'{name}' is an ambient host operation; no profile provides it",
                    node.line,
                    node.col,
                )
                self._check_args_loose(node, context)
                return "Any", True
            self.report.error(
                "HX3005",
                f"unknown operation '{name}'",
                node.line,
                node.col,
            )
            self._check_args_loose(node, context)
            return "Any", False
        if len(dotted) == 1:
            return self._plain_call(node, context, dotted[0])
        self.report.error("HX3005", f"unknown operation '{name}'", node.line, node.col)
        self._check_args_loose(node, context)
        return "Any", False

    def _plain_call(self, node: A.Call, context: Context, name: str) -> tuple[str, bool]:
        if name in FORBIDDEN_AMBIENT and self.rules.forbid_ambient:
            self.report.error(
                "HX4004",
                f"'{name}' is an ambient host operation; no profile provides it",
                node.line,
                node.col,
            )
            self._check_args_loose(node, context)
            return "Any", True
        if name in FORBIDDEN_AUTHORITY and self.rules.forbid_ambient:
            self.report.error(
                "HX4006",
                f"'{name}' would change authority; source requests capabilities and the host "
                "policy decides",
                node.line,
                node.col,
            )
            return "Any", True
        fn = self.fns.get(name)
        if fn is None:
            if name in self.schemas or name in self.enums:
                self.report.error(
                    "HX3011",
                    f"'{name}' names a type and is not callable",
                    node.line,
                    node.col,
                )
            else:
                self.report.error(
                    "HX3005",
                    f"unknown function '{name}'",
                    node.line,
                    node.col,
                )
            self._check_args_loose(node, context)
            return "Any", False
        params = tuple(
            (param.name, self._resolve(param.type, allow_agent=False) or "Any")
            for param in fn.params
        )
        self._check_args(node, context, params)
        return self._resolve(fn.returns) or "Any", False

    def _agent_call(
        self,
        node: A.Call,
        context: Context,
        info: AgentInfo,
        operation: str,
        allow_effect: bool,
    ) -> tuple[str, bool]:
        if self.profile != "agent":
            self.report.error(
                "HX6001",
                f"calling agent '{info.name}' requires the agent profile",
                node.line,
                node.col,
            )
            return info.payload, True
        if operation not in AGENT_OPERATIONS:
            self.report.error(
                "HX3011",
                f"unknown agent operation '{operation}' "
                f"(known: {', '.join(AGENT_OPERATIONS)})",
                node.line,
                node.col,
            )
        self._check_args(node, context, (("input", "Any"),))
        if context.kind == "workflow":
            self._use_capability("model.invoke", context, node)
        else:
            self.report.error(
                "HX4003",
                f"'{info.name}.{operation}' is an effect and cannot run in a pure fn",
                node.line,
                node.col,
            )
        if not allow_effect and self.rules.require_awaited_effects:
            self.report.error(
                "HX4005",
                f"effect '{info.name}.{operation}' must be awaited",
                node.line,
                node.col,
            )
        return info.payload, True

    def _host_call(
        self,
        node: A.Call,
        context: Context,
        name: str,
        allow_effect: bool,
    ) -> tuple[str, bool]:
        operation = HOST_OPERATIONS[name]
        if self.profile != "agent":
            self.report.error(
                "HX6001",
                f"'{name}' is a host operation and requires the agent profile",
                node.line,
                node.col,
            )
            self._check_args(node, context, operation.params)
            return self._op_return(operation, node), operation.is_effect
        self._check_args(node, context, operation.params)
        if operation.capability is not None:
            if context.kind == "workflow":
                self._use_capability(operation.capability, context, node)
            else:
                self.report.error(
                    "HX4003",
                    f"'{name}' is an effect and cannot run in a pure fn",
                    node.line,
                    node.col,
                )
        if operation.is_effect and not allow_effect and self.rules.require_awaited_effects:
            self.report.error(
                "HX4005",
                f"effect '{name}' must be awaited",
                node.line,
                node.col,
            )
        return self._op_return(operation, node), operation.is_effect

    def _use_capability(self, capability: str, context: Context, node: A.Call) -> None:
        context.used.add(capability)
        if self.rules.enforce_capabilities and capability not in context.declared:
            self.report.error(
                "HX4001",
                f"capability '{capability}' is required here but is not declared in 'requires'",
                node.line,
                node.col,
            )

    def _op_return(self, operation: HostOperation, node: A.Call) -> str:
        text = operation.returns
        for identifier in _IDENT_RE.findall(text):
            if identifier in PRIMITIVES or identifier in GENERIC_ARITY:
                continue
            if identifier not in self.schemas and identifier not in self.enums:
                self.report.error(
                    "HX3001",
                    f"'{operation.name}' returns '{identifier}', which this program must "
                    f"declare as a schema or enum (typed boundary)",
                    node.line,
                    node.col,
                )
                return "Any"
        return text

    def _await(self, node: A.Await, context: Context) -> tuple[str, bool]:
        if self.profile != "agent":
            self.report.error(
                "HX6001",
                "'await' requires the agent profile, which the host selects",
                node.line,
                node.col,
            )
            return "Any", False
        actual, is_effect = self._expr(node.value, context, allow_effect=True)
        if actual.startswith("Task<") and actual.endswith(">"):
            if isinstance(node.value, A.Ident):
                context.awaited.add(node.value.name)
            return actual[5:-1], False
        if is_effect:
            return actual, False
        self.report.error(
            "HX3002",
            f"'await' expects an effect call or a spawned task, found {actual}",
            node.line,
            node.col,
        )
        return "Any", False

    def _spawn(self, node: A.Spawn, context: Context) -> tuple[str, bool]:
        if self.profile != "agent":
            self.report.error(
                "HX6001",
                "'spawn' requires the agent profile, which the host selects",
                node.line,
                node.col,
            )
            return "Any", False
        actual, is_effect = self._expr(node.value, context, allow_effect=True)
        if context.kind != "workflow":
            self.report.error(
                "HX4003",
                "'spawn' is a workflow construct and cannot run in a pure fn",
                node.line,
                node.col,
            )
        if not is_effect:
            self.report.error(
                "HX3002",
                f"'spawn' expects an effect call, found {actual}",
                node.line,
                node.col,
            )
            return "Any", False
        context.spawn_count += 1
        limit = context.budget.get("tasks")
        if (
            isinstance(limit, int)
            and context.spawn_count > limit
            and not context.spawn_over_budget_reported
        ):
            context.spawn_over_budget_reported = True
            self.report.error(
                "HX5005",
                f"spawned tasks exceed budget.tasks = {limit}",
                node.line,
                node.col,
            )
        return f"Task<{actual}>", False

    def _binary(self, node: A.Binary, context: Context) -> tuple[str, bool]:
        left, _ = self._expr(node.left, context)
        right, _ = self._expr(node.right, context)
        if node.op in {"and", "or"}:
            for operand in (left, right):
                if operand not in {"Bool", "Any"}:
                    self.report.error(
                        "HX3012",
                        f"'{node.op}' expects Bool operands, found {left} and {right}",
                        node.line,
                        node.col,
                    )
                    break
            return "Bool", False
        if node.op == "+":
            if left == "String" and right == "String":
                return "String", False
            if "Any" not in {left, right}:
                self.report.error(
                    "HX3012",
                    f"'+' concatenates String values; found {left} and {right}",
                    node.line,
                    node.col,
                )
            return "String", False
        if node.op in {"==", "!="}:
            if not self._compatible(left, right):
                self.report.error(
                    "HX3012",
                    f"cannot compare {left} and {right}",
                    node.line,
                    node.col,
                )
            return "Bool", False
        # ordering comparisons
        if not ({left, right} <= NUMERIC or left == right or "Any" in {left, right}):
            self.report.error(
                "HX3012",
                f"cannot order {left} and {right}",
                node.line,
                node.col,
            )
        return "Bool", False

    # -- helpers ----------------------------------------------------------
    def _check_args(
        self,
        node: A.Call,
        context: Context,
        params: tuple[tuple[str, str], ...],
    ) -> None:
        names = [name for name, _ in params]
        if len(node.args) > len(params):
            self.report.error(
                "HX3003",
                f"expected at most {len(params)} argument(s), found {len(node.args)}",
                node.line,
                node.col,
            )
        for index, argument in enumerate(node.args):
            actual, _ = self._expr(argument.value, context)
            if index >= len(params):
                continue
            expected = params[index][1]
            if not self._compatible(expected, actual):
                self.report.error(
                    "HX3002",
                    f"argument '{params[index][0]}' expects {expected}, found {actual}",
                    argument.value.line,
                    argument.value.col,
                )
        given = set(names[: len(node.args)])
        for keyword in node.kwargs:
            actual, _ = self._expr(keyword.value, context)
            if keyword.name not in names:
                self.report.error(
                    "HX3003",
                    f"unknown argument name '{keyword.name}' "
                    f"(expected: {', '.join(names) or 'none'})",
                    keyword.line,
                    keyword.col,
                )
                continue
            if keyword.name in given:
                self.report.error(
                    "HX3003",
                    f"argument '{keyword.name}' is given twice",
                    keyword.line,
                    keyword.col,
                )
                continue
            given.add(keyword.name)
            expected = dict(params)[keyword.name]
            if not self._compatible(expected, actual):
                self.report.error(
                    "HX3002",
                    f"argument '{keyword.name}' expects {expected}, found {actual}",
                    keyword.line,
                    keyword.col,
                )

    def _check_args_loose(self, node: A.Call, context: Context) -> None:
        """Check argument expressions for an operation that does not exist.

        The operation's arity is unknown, so complaining about how many
        arguments it was given would be inventing a rule; errors inside the
        arguments are still worth reporting.
        """
        for argument in node.args:
            self._expr(argument.value, context)
        for keyword in node.kwargs:
            self._expr(keyword.value, context)

    def _dotted(self, node: A.Expr) -> tuple[str, ...] | None:
        if isinstance(node, A.Ident):
            return (node.name,)
        if isinstance(node, A.Member):
            base = self._dotted(node.base)
            if base is None:
                return None
            return (*base, node.name)
        return None

    def _resolve(self, type_ref: A.TypeRef | None, allow_agent: bool = True) -> str | None:
        """Resolve a written type to the checker's internal name.

        ``T?`` becomes ``Optional<T>`` so the marker survives into comparisons:
        a ``String`` may stand where ``String?`` is expected, never the reverse.
        """
        resolved = self._resolve_base(type_ref, allow_agent)
        if resolved is None or type_ref is None or not type_ref.optional:
            return resolved
        if resolved.startswith("Optional<"):
            return resolved
        return f"Optional<{resolved}>"

    def _resolve_base(self, type_ref: A.TypeRef | None, allow_agent: bool) -> str | None:
        if type_ref is None:
            return None
        base = type_ref.name
        if base == "Agent":
            if not allow_agent:
                self.report.error(
                    "HX3001",
                    "Agent<T> may only appear in an agent declaration",
                    type_ref.line,
                    type_ref.col,
                )
                return "Any"
            if len(type_ref.args) != 1:
                self.report.error(
                    "HX3008",
                    f"'Agent' takes 1 type argument, found {len(type_ref.args)}",
                    type_ref.line,
                    type_ref.col,
                )
                return "Any"
            inner = self._resolve(type_ref.args[0], allow_agent=False) or "Any"
            return f"Agent<{inner}>"
        if base in GENERIC_ARITY:
            expected = GENERIC_ARITY[base]
            if len(type_ref.args) != expected:
                self.report.error(
                    "HX3008",
                    f"'{base}' takes {expected} type argument(s), found {len(type_ref.args)}",
                    type_ref.line,
                    type_ref.col,
                )
                return "Any"
            args = [self._resolve(arg, allow_agent=False) or "Any" for arg in type_ref.args]
            return f"{base}<{', '.join(args)}>"
        if base in PRIMITIVES:
            if type_ref.args:
                self.report.error(
                    "HX3008",
                    f"'{base}' does not take type arguments",
                    type_ref.line,
                    type_ref.col,
                )
                return "Any"
            return base
        if base in self.schemas or base in self.enums:
            if type_ref.args:
                self.report.error(
                    "HX3008",
                    f"'{base}' does not take type arguments",
                    type_ref.line,
                    type_ref.col,
                )
                return "Any"
            return base
        self.report.error(
            "HX3001",
            f"unknown type '{base}'",
            type_ref.line,
            type_ref.col,
        )
        return None

    def _compatible(self, expected: str | None, actual: str | None) -> bool:
        if expected is None or actual is None:
            return True
        if expected == actual or expected == "Any" or actual == "Any":
            return True
        if expected.startswith("Optional<") and expected.endswith(">"):
            inner = expected[len("Optional<") : -1]
            return self._compatible(inner, actual)
        return False

    def _optional_inner(self, type_text: str) -> str:
        if type_text.startswith("Optional<") and type_text.endswith(">"):
            return type_text[len("Optional<") : -1]
        return type_text

    def _bare_context(self, returns: str) -> Context:
        return Context(kind="fn", returns=returns, scope=Scope())

    def _ends_with_return(self, block: A.Block) -> bool:
        if not block.statements:
            return False
        last = block.statements[-1]
        if isinstance(last, A.ReturnStmt):
            return True
        if isinstance(last, A.IfStmt) and last.orelse is not None:
            return self._ends_with_return(last.then) and self._ends_with_return(last.orelse)
        return False


def check(
    program: A.Program, profile: str, reporter: Reporter, rules: RuleSet | None = None
) -> None:
    Checker(program, profile, reporter, rules).run()
