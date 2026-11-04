"""The module loader: which files a program is made of, decided before it runs.

`use notes` names a module; this module turns names into bytes. The host supplies
the bytes — through a mapping or a directory — because a program that could open
paths itself would have a filesystem again, which is the thing the whole effect
model exists to keep out.

Three rules shape the design, and each is a limit someone might want lifted later:

* **One level.** A used module may not `use` anything. Nested modules would need a
  re-export story (what does `a.b.c` mean, who may forward whose names) and the
  language has no use for it yet, so the loader says so rather than guessing. It
  also keeps "how many files is this run made of" answerable without a search.
* **Names, not paths.** `use notes` resolves to `notes`, whose declared module name
  must match. So a file cannot be reached under one name and compiled under
  another, and no `..`, separator or absolute path exists in the vocabulary at all.
* **Every file is accounted for.** The loader returns the sources in a stable order
  so the manifest can pin a digest for each: a run is bound to the exact set of
  bytes that produced the tree it executes, not only to its entry file.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import ast as A
from .diagnostics import Diagnostic, Reporter
from .parser import parse

#: A program is an entry plus a small number of helpers. Both numbers are generous
#: for that and tight enough that no import graph can make a check unbounded.
MAX_MODULES = 16
MAX_MODULE_BYTES = 256 * 1024

Resolver = Callable[[str], str | None]


@dataclass(frozen=True)
class ResolvedModule:
    """One module the loader accepted: its bytes, its tree, where it came from.

    `program` is the tree renamed for the file that `use`s it, which is what the
    entry checks and the driver executes. `source_program` is the file as written,
    checked on its own so an error in a helper is reported in the helper's words
    (`Summary`, line 4) and not in the reader's (`notes.Summary`).
    """

    name: str
    path: str
    source: str
    program: A.Program
    source_program: A.Program


@dataclass(frozen=True)
class LoadResult:
    """The graph, plus the records that explain a graph that could not be built.

    A failed `use` still yields the entry tree: the author gets every other problem
    in the file in one pass, not one error per edit.
    """

    modules: tuple[ResolvedModule, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not any(record.kind == "error" for record in self.diagnostics)


def dict_resolver(sources: Mapping[str, str]) -> Resolver:
    """Resolve a module name against a name -> source mapping.

    Keys are module names, with or without the `.hx` suffix, because the caller
    already decided which bytes belong to which name — that choice is exactly the
    authority the program is denied.
    """

    def resolve(name: str) -> str | None:
        for key, text in sources.items():
            if key == name or key.removesuffix(".hx") == name:
                return text
        return None

    return resolve


def directory_resolver(root: str | Path) -> Resolver:
    """Resolve `use notes` to `<root>/notes.hx`, one level, no traversal.

    A name with a separator, a leading dot or any other attempt to leave the
    directory resolves to nothing rather than being sanitised: the caller cannot ask
    for a file outside the root it pointed at, in any spelling, because there is no
    spelling that reaches it.
    """
    base = Path(root)

    def resolve(name: str) -> str | None:
        if not name or name.startswith(".") or any(sep in name for sep in ("/", "\\")):
            return None
        candidate = base / f"{name}.hx"
        if not candidate.is_file():
            return None
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return None

    return resolve


#: What a reader may take from a module: types and pure functions. A workflow is an
#: entry point and an agent is a capability request, so neither crosses the boundary.
OWNABLE = (A.SchemaDecl, A.EnumDecl, A.FnDecl)

BUILTINS = frozenset(
    {"Int", "Float", "String", "Bool", "Duration", "Money", "Any", "List", "Map", "Agent"}
)


def module_program(name: str, program: A.Program) -> A.Program:
    """Return a module's declarations renamed into the file that `use`s it.

    Inside `notes.hx` a type is `Summary`; to the reader it is `notes.Summary`. The
    rename covers the bodies too, because a value produced by a helper must carry the
    same type name the reader's annotation names — two spellings of one record would
    either break validation or, worse, let a look-alike record through it.

    The checker's no-shadowing rule (HX3025) is what makes a name-by-name rename
    safe: inside a module file, a binding can never stand in for a declaration name,
    so every occurrence of that name is the declaration.
    """
    own = frozenset(decl.name for decl in program.decls)

    def type_ref(ref: A.TypeRef) -> A.TypeRef:
        args = tuple(type_ref(arg) for arg in ref.args)
        if not ref.qualifier and ref.name in own and ref.name not in BUILTINS:
            return dataclasses.replace(ref, args=args, qualifier=(name,))
        return dataclasses.replace(ref, args=args)

    def qualified(node: A.Expr) -> A.Expr:
        """Point a bare reference at this module's own declaration."""
        if isinstance(node, A.Ident) and node.name in own:
            return A.Member(A.Ident(name, node.line, node.col), node.name, node.line, node.col)
        if (
            isinstance(node, A.Member)
            and isinstance(node.base, A.Ident)
            and node.base.name in own
        ):
            base = A.Member(A.Ident(name, node.line, node.col), node.base.name, node.line, node.col)
            return A.Member(base, node.name, node.line, node.col)
        return node

    def expr(node: A.Expr) -> A.Expr:
        if node is None:
            return None
        if isinstance(node, (A.IntLit, A.FloatLit, A.StringLit, A.BoolLit, A.DurationLit)):
            return node
        if isinstance(node, A.Ident):
            return qualified(node)
        if isinstance(node, A.Member):
            rewritten = qualified(node)
            if rewritten is not node:
                return rewritten
            return A.Member(expr(node.base), node.name, node.line, node.col)
        if isinstance(node, A.Call):
            args = tuple(A.Arg(expr(arg.value)) for arg in node.args)
            kwargs = tuple(
                A.KwArg(kw.name, expr(kw.value), kw.line, kw.col) for kw in node.kwargs
            )
            return A.Call(expr(node.callee), args, kwargs, node.line, node.col)
        if isinstance(node, A.WithExpr):
            return A.WithExpr(
                expr(node.base),
                tuple(A.KwArg(kw.name, expr(kw.value), kw.line, kw.col) for kw in node.updates),
                node.line,
                node.col,
            )
        if isinstance(node, A.Await):
            return A.Await(expr(node.value), node.line, node.col)
        if isinstance(node, A.Spawn):
            return A.Spawn(expr(node.value), node.line, node.col)
        if isinstance(node, A.Unary):
            return A.Unary(node.op, expr(node.operand), node.line, node.col)
        if isinstance(node, A.Binary):
            return A.Binary(node.op, expr(node.left), expr(node.right), node.line, node.col)
        if isinstance(node, A.ListLit):
            return A.ListLit(tuple(expr(item) for item in node.items), node.line, node.col)
        return node

    def stmt(node: A.Stmt) -> A.Stmt:
        if isinstance(node, A.LetStmt):
            return A.LetStmt(
                node.name,
                type_ref(node.annotation) if node.annotation is not None else None,
                expr(node.value),
                node.line,
                node.col,
            )
        if isinstance(node, A.EnsureStmt):
            return A.EnsureStmt(expr(node.value), node.line, node.col)
        if isinstance(node, A.ReturnStmt):
            return A.ReturnStmt(expr(node.value) if node.value is not None else None,
                                node.line, node.col)
        if isinstance(node, A.ExprStmt):
            return A.ExprStmt(expr(node.value), node.line, node.col)
        if isinstance(node, A.IfStmt):
            return A.IfStmt(
                expr(node.condition),
                block(node.then),
                block(node.orelse) if node.orelse is not None else None,
                node.line,
                node.col,
            )
        if isinstance(node, A.ForStmt):
            acc = node.accumulate
            return A.ForStmt(
                node.name,
                expr(node.iterable),
                block(node.body),
                node.line,
                node.col,
                None
                if acc is None
                else A.Accumulator(
                    acc.name, type_ref(acc.type), expr(acc.init), acc.line, acc.col
                ),
            )
        return node

    def block(node: A.Block) -> A.Block:
        return A.Block(tuple(stmt(item) for item in node.statements), node.line, node.col)

    decls: list[A.Decl] = []
    for decl in program.decls:
        if isinstance(decl, A.SchemaDecl):
            decls.append(
                A.SchemaDecl(
                    f"{name}.{decl.name}",
                    tuple(
                        A.Field(
                            field.name,
                            type_ref(field.type),
                            expr(field.default) if field.default is not None else None,
                            field.line,
                            field.col,
                        )
                        for field in decl.fields
                    ),
                    decl.line,
                    decl.col,
                )
            )
        elif isinstance(decl, A.EnumDecl):
            decls.append(
                A.EnumDecl(
                    f"{name}.{decl.name}",
                    tuple(
                        A.EnumMember(member.name, member.line, member.col)
                        for member in decl.members
                    ),
                    decl.line,
                    decl.col,
                )
            )
        elif isinstance(decl, A.FnDecl):
            decls.append(
                A.FnDecl(
                    f"{name}.{decl.name}",
                    tuple(
                        A.Param(param.name, type_ref(param.type), param.line, param.col)
                        for param in decl.params
                    ),
                    type_ref(decl.returns),
                    block(decl.body),
                    decl.line,
                    decl.col,
                )
            )
    return dataclasses.replace(program, module=name, decls=tuple(decls))


def load_modules(
    program: A.Program, entry_path: str, resolve: Resolver | None
) -> LoadResult:
    """Resolve one program's `use` clauses. Checking the files is done elsewhere."""
    diagnostics: list[Diagnostic] = []
    resolved: list[ResolvedModule] = []

    def error(code: str, message: str, path: str, line: int, col: int) -> None:
        diagnostics.append(
            Diagnostic(code=code, kind="error", message=message, path=path, line=line, col=col)
        )

    for use in program.uses:
        path = f"{use.module}.hx"
        # A file cannot use the module it declares: the name would then mean both "this
        # file" and "another file", and the manifest's per-module digests — which are
        # keyed by declared module name — would have two entries for one name.
        if use.module == program.module:
            error(
                "HX3017",
                f"this file declares module {program.module!r} and also uses it; a module "
                "is a name for one file, so a program cannot take itself in as a helper",
                entry_path,
                use.line,
                use.col,
            )
            continue
        # A record must name the file its line and column sit in. Everything decided
        # *about* a `use` clause before its bytes were read — unresolvable, too big,
        # duplicate, misnamed — belongs to the entry file, at the clause. Blaming
        # `helper.hx` for a file this check never got would point an editor at nothing.
        if resolve is None:
            error(
                "HX3015",
                f"module {use.module!r} cannot be resolved: this check was given no module "
                "source, so a program that uses one cannot be compiled",
                entry_path,
                use.line,
                use.col,
            )
            continue
        if len(resolved) >= MAX_MODULES:
            error(
                "HX3018",
                f"a program may use at most {MAX_MODULES} modules",
                entry_path,
                use.line,
                use.col,
            )
            continue
        source = resolve(use.module)
        if source is None:
            error(
                "HX3015",
                f"module {use.module!r} has no source: the host resolves `use` names, and it "
                "has none for this one",
                entry_path,
                use.line,
                use.col,
            )
            continue
        if len(source.encode("utf-8")) > MAX_MODULE_BYTES:
            error(
                "HX3018",
                f"module {use.module!r} is larger than {MAX_MODULE_BYTES} bytes",
                entry_path,
                use.line,
                use.col,
            )
            continue
        if any(loaded.name == use.module for loaded in resolved):
            error(
                "HX3017",
                f"module {use.module!r} is loaded twice",
                entry_path,
                use.line,
                use.col,
            )
            continue
        reporter = Reporter(path)
        parsed = parse(source, reporter)
        if parsed.module != use.module:
            error(
                "HX3016",
                f"the file for `use {use.module}` declares module {parsed.module!r}; "
                "the name used and the name declared must agree, or a run cannot say which "
                "file it executed",
                entry_path,
                use.line,
                use.col,
            )
            continue
        if parsed.uses:
            nested = parsed.uses[0]
            error(
                "HX3017",
                f"module {use.module!r} uses {nested.module!r}: modules are one level deep, so "
                "a helper cannot pull in helpers of its own",
                path,
                nested.line,
                nested.col,
            )
            continue
        for decl in parsed.decls:
            if isinstance(decl, (A.WorkflowDecl, A.AgentDecl)):
                kind = "workflow" if isinstance(decl, A.WorkflowDecl) else "agent"
                error(
                    "HX3019",
                    f"module {use.module!r} declares a {kind} ({decl.name!r}); another file "
                    "may take types and pure functions from a module, never an entry point "
                    "or a capability request",
                    path,
                    decl.line,
                    decl.col,
                )
        # The module's own syntax records are carried up with their path, so one
        # check reports across every file the program is made of.
        diagnostics.extend(reporter.diagnostics())
        resolved.append(
            ResolvedModule(
                use.module, path, source, module_program(use.module, parsed), parsed
            )
        )

    return LoadResult(tuple(resolved), tuple(diagnostics))
