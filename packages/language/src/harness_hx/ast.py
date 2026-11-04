"""Hx syntax tree — every node keeps the position it started at.

Positions travel with the node rather than being looked up later: the whole
point of the diagnostic contract is that a record says where the problem is,
and a tree that drops the position cannot honestly tell anyone.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TypeRef:
    """A type name, optionally qualified by a used module (`notes.Summary`).

    `qualifier` is empty for everything declared in the file being read, which is
    every program written before M8 — the default keeps those trees identical.
    """

    name: str
    args: tuple[TypeRef, ...] = ()
    optional: bool = False
    line: int = 0
    col: int = 0
    qualifier: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """The single string every table in the checker and runtime keys on."""
        return ".".join((*self.qualifier, self.name))


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
class ForStmt(Stmt):
    """One pass per element of a ``List``, and one step per pass by the budget.

    There is no ``while``: a loop whose termination depends on mutation would
    have to prove it terminates, and this language has no mutation. Over a list
    the bound is structural; over the budget it is declared; both are visible in
    the source.

    ``accumulate`` is the one way a loop produces a value. It is a **binding**, not
    an assignment: the accumulator names a fresh value per pass, so nothing that
    existed before the loop is overwritten, and the loop still ends when the list
    does or the budget does — never when a variable happens to change.
    """

    name: str
    iterable: Expr
    body: Block
    line: int
    col: int
    accumulate: Accumulator | None = None


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
class UseDecl:
    """`use notes` — this program reads the module named `notes`.

    A name, not a path: the host resolves names to bytes (so a program cannot ask
    for `../../etc/passwd`), and the resolved file's digest is bound into the
    manifest like the entry file's.
    """

    module: str
    line: int
    col: int


@dataclass(frozen=True)
class Accumulator:
    """The `accumulate total: Int = 0` clause of a `for` loop."""

    name: str
    type: TypeRef
    init: Expr
    line: int
    col: int


@dataclass(frozen=True)
class WithExpr(Expr):
    """`proposal with { summary: "done" }` — a new record, not an edit.

    The base is untouched, which is why this exists instead of assignment: an
    update expression says which value became which, and a mutation says nothing a
    reviewer can check.
    """

    base: Expr
    updates: tuple[KwArg, ...]
    line: int
    col: int


@dataclass(frozen=True)
class Program:
    module: str | None
    decls: tuple[Decl, ...]
    path: str | None = None
    diagnostics: tuple[object, ...] = field(default_factory=tuple)
    uses: tuple[UseDecl, ...] = ()


@dataclass(frozen=True)
class LoadedModule:
    """One module the loader resolved for a program: its text, its tree, its digest.

    Kept beside the program rather than merged into it so the digest of every file
    that shaped a run stays addressable — the manifest lists these, and a replay
    that swapped one of them is refused by name.
    """

    name: str
    path: str
    source: str
    program: Program
