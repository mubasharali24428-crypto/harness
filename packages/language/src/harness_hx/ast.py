"""Hx syntax tree — every node keeps the position it started at.

Positions travel with the node rather than being looked up later: the whole
point of the diagnostic contract is that a record says where the problem is,
and a tree that drops the position cannot honestly tell anyone.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TypeRef:
    name: str
    args: tuple[TypeRef, ...] = ()
    optional: bool = False
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class Param:
    name: str
    type: TypeRef
    line: int
    col: int


@dataclass(frozen=True)
class Field:
    name: str
    type: TypeRef
    default: Expr | None
    line: int
    col: int


@dataclass(frozen=True)
class EnumMember:
    name: str
    line: int
    col: int


@dataclass(frozen=True)
class SchemaDecl:
    name: str
    fields: tuple[Field, ...]
    line: int
    col: int


@dataclass(frozen=True)
class EnumDecl:
    name: str
    members: tuple[EnumMember, ...]
    line: int
    col: int


@dataclass(frozen=True)
class AgentSetting:
    key: str
    value: Expr
    line: int
    col: int


@dataclass(frozen=True)
class AgentDecl:
    name: str
    type: TypeRef
    settings: tuple[AgentSetting, ...]
    line: int
    col: int


@dataclass(frozen=True)
class Capability:
    name: str
    line: int
    col: int


@dataclass(frozen=True)
class Requires:
    capabilities: tuple[Capability, ...]
    line: int
    col: int


@dataclass(frozen=True)
class BudgetValue:
    """One raw budget value. ``currency`` is set only for a money literal."""

    kind: str
    raw: str
    line: int
    col: int
    currency: str | None = None


@dataclass(frozen=True)
class BudgetEntry:
    key: str
    value: BudgetValue
    line: int
    col: int


@dataclass(frozen=True)
class BudgetDecl:
    entries: tuple[BudgetEntry, ...]
    line: int
    col: int


# ---------------------------------------------------------------------------
# statements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    statements: tuple[Stmt, ...]
    line: int
    col: int


class Stmt:
    """Marker base — kept as a plain class so ``isinstance`` lists stay readable."""

    line: int
    col: int


@dataclass(frozen=True)
class LetStmt(Stmt):
    name: str
    annotation: TypeRef | None
    value: Expr
    line: int
    col: int


@dataclass(frozen=True)
class EnsureStmt(Stmt):
    value: Expr
    line: int
    col: int


@dataclass(frozen=True)
class ReturnStmt(Stmt):
    value: Expr | None
    line: int
    col: int


@dataclass(frozen=True)
class IfStmt(Stmt):
    condition: Expr
    then: Block
    orelse: Block | None
    line: int
    col: int


@dataclass(frozen=True)
class ExprStmt(Stmt):
    value: Expr
    line: int
    col: int


@dataclass(frozen=True)
class FnDecl:
    name: str
    params: tuple[Param, ...]
    returns: TypeRef
    body: Block
    line: int
    col: int


@dataclass(frozen=True)
class WorkflowDecl:
    name: str
    params: tuple[Param, ...]
    returns: TypeRef
    requires: Requires | None
    budget: BudgetDecl | None
    agents: tuple[AgentDecl, ...]
    body: Block
    line: int
    col: int


Decl = SchemaDecl | EnumDecl | FnDecl | AgentDecl | WorkflowDecl


# ---------------------------------------------------------------------------
# expressions
# ---------------------------------------------------------------------------


class Expr:
    line: int
    col: int


@dataclass(frozen=True)
class IntLit(Expr):
    value: int
    line: int
    col: int


@dataclass(frozen=True)
class FloatLit(Expr):
    value: float
    line: int
    col: int


@dataclass(frozen=True)
class StringLit(Expr):
    value: str
    line: int
    col: int


@dataclass(frozen=True)
class BoolLit(Expr):
    value: bool
    line: int
    col: int


@dataclass(frozen=True)
class DurationLit(Expr):
    raw: str
    line: int
    col: int


@dataclass(frozen=True)
class Ident(Expr):
    name: str
    line: int
    col: int


@dataclass(frozen=True)
class Member(Expr):
    base: Expr
    name: str
    line: int
    col: int


@dataclass(frozen=True)
class Arg:
    value: Expr


@dataclass(frozen=True)
class KwArg:
    name: str
    value: Expr
    line: int
    col: int


@dataclass(frozen=True)
class Call(Expr):
    callee: Expr
    args: tuple[Arg, ...]
    kwargs: tuple[KwArg, ...]
    line: int
    col: int


@dataclass(frozen=True)
class Await(Expr):
    value: Expr
    line: int
    col: int


@dataclass(frozen=True)
class Spawn(Expr):
    value: Expr
    line: int
    col: int


@dataclass(frozen=True)
class Unary(Expr):
    op: str
    operand: Expr
    line: int
    col: int


@dataclass(frozen=True)
class Binary(Expr):
    op: str
    left: Expr
    right: Expr
    line: int
    col: int


@dataclass(frozen=True)
class ListLit(Expr):
    items: tuple[Expr, ...]
    line: int
    col: int


@dataclass(frozen=True)
class Program:
    module: str | None
    decls: tuple[Decl, ...]
    path: str | None = None
    diagnostics: tuple[object, ...] = field(default_factory=tuple)
