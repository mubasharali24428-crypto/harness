"""Recursive-descent parser with recovery, so one syntax error is one record.

The grammar is deliberately small: declarations, statements, and a precedence
ladder with no arithmetic (``+`` is string concatenation; comparisons and
boolean operators are enough for ``ensure``). Anything the grammar does not
know is reported at the token that surprised it and skipped, then parsing
resumes at the next statement or declaration boundary.
"""

from __future__ import annotations

from . import ast as A
from .diagnostics import Reporter
from .lexer import Lexer, Token

_TOP_LEVEL = frozenset({"module", "schema", "enum", "fn", "agent", "workflow"})
_STATEMENT_START = frozenset({"let", "ensure", "return", "if", "requires", "budget", "agent"})
_COMPARISONS = frozenset({"==", "!=", "<", ">", "<=", ">="})


class ParseError(Exception):
    def __init__(self, token: Token, code: str, message: str) -> None:
        super().__init__(message)
        self.token = token
        self.code = code
        self.message = message


class Parser:
    def __init__(self, tokens: list[Token], reporter: Reporter) -> None:
        self.tokens = tokens
        self.report = reporter
        self.i = 0

    # -- token helpers ----------------------------------------------------
    def peek(self, offset: int = 0) -> Token:
        index = min(self.i + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def at(self, kind: str) -> bool:
        return self.peek().kind == kind

    def advance(self) -> Token:
        token = self.peek()
        if self.i < len(self.tokens) - 1:
            self.i += 1
        return token

    def match(self, kind: str) -> bool:
        if self.at(kind):
            self.advance()
            return True
        return False

    def expect(self, kind: str, message: str) -> Token:
        if self.at(kind):
            return self.advance()
        raise ParseError(self.peek(), "HX2001", f"{message}, found {self.peek().describe()}")

    # -- program ----------------------------------------------------------
    def parse_program(self) -> A.Program:
        module: str | None = None
        decls: list[A.Decl] = []
        if self.at("module"):
            self.advance()
            if self.at("ident"):
                module = self.advance().value
            else:
                self._report(ParseError(self.peek(), "HX2001", "expected a module name"))
            self.match(";")
        while not self.at("eof"):
            start = self.i
            if self.match(";"):
                continue
            try:
                decl = self._parse_decl()
                if decl is not None:
                    decls.append(decl)
            except ParseError as error:
                self._report(error)
                self._sync_top_level()
            if self.i == start:
                self.advance()
        return A.Program(module, tuple(decls))

    def _report(self, error: ParseError) -> None:
        self.report.error(error.code, error.message, error.token.line, error.token.col)

    def _parse_decl(self) -> A.Decl | None:
        token = self.peek()
        if token.kind == "schema":
            return self._parse_schema()
        if token.kind == "enum":
            return self._parse_enum()
        if token.kind == "fn":
            return self._parse_fn()
        if token.kind == "agent":
            return self._parse_agent()
        if token.kind == "workflow":
            return self._parse_workflow()
        if token.kind == "ident":
            name = token.value
            if name == "profile":
                self.report.error(
                    "HX6002",
                    "the profile is selected by the host, never by the source; "
                    "remove this declaration",
                    token.line,
                    token.col,
                )
                self.advance()
                if self.at("ident") or self.at("agent"):
                    self.advance()
                return None
            if name in {"import", "use"}:
                self.report.error(
                    "HX4004",
                    f"'{name}' is not available: module loading is disabled in every profile "
                    "and programs are single-file",
                    token.line,
                    token.col,
                )
                self.advance()
                self._sync_top_level()
                return None
            raise ParseError(
                token,
                "HX2003",
                f"expected a declaration (schema, enum, fn, agent, workflow), found {name!r}",
            )
        raise ParseError(
            token,
            "HX2003",
            f"expected a declaration, found {token.describe()}",
        )

    # -- declarations -----------------------------------------------------
    def _parse_schema(self) -> A.SchemaDecl:
        keyword = self.advance()
        name = self.expect("ident", "expected a schema name").value
        self.expect("{", "expected '{' to open the schema")
        fields: list[A.Field] = []
        while not self.at("}") and not self.at("eof"):
            try:
                field_token = self.expect("ident", "expected a field name")
                self.expect(":", f"expected ':' after field {field_token.value!r}")
                field_type = self._parse_type()
                default = self._parse_expr() if self.match("=") else None
                fields.append(
                    A.Field(
                        field_token.value,
                        field_type,
                        default,
                        field_token.line,
                        field_token.col,
                    )
                )
            except ParseError as error:
                self._report(error)
                self._sync_member()
            if not self.match(",") and not self.match(";"):
                continue
        self._close_brace(keyword, "schema")
        return A.SchemaDecl(name, tuple(fields), keyword.line, keyword.col)

    def _parse_enum(self) -> A.EnumDecl:
        keyword = self.advance()
        name = self.expect("ident", "expected an enum name").value
        self.expect("{", "expected '{' to open the enum")
        members: list[A.EnumMember] = []
        while not self.at("}") and not self.at("eof"):
            try:
                member = self.expect("ident", "expected an enum member name")
                members.append(A.EnumMember(member.value, member.line, member.col))
            except ParseError as error:
                self._report(error)
                self._sync_member()
            if not self.match(",") and not self.match(";"):
                continue
        self._close_brace(keyword, "enum")
        return A.EnumDecl(name, tuple(members), keyword.line, keyword.col)

    def _parse_fn(self) -> A.FnDecl:
        keyword = self.advance()
        name = self.expect("ident", "expected a function name").value
        params = self._parse_params()
        returns = self._parse_return_type()
        body = self._parse_block()
        return A.FnDecl(name, params, returns, body, keyword.line, keyword.col)

    def _parse_agent(self) -> A.AgentDecl:
        keyword = self.advance()
        name = self.expect("ident", "expected an agent name").value
        self.expect(":", f"expected ':' after agent {name!r}")
        type_token = self.expect("ident", "expected the agent type, e.g. Agent<Proposal>")
        agent_type = self._parse_type_tail(type_token)
        settings: list[A.AgentSetting] = []
        if self.match("{"):
            while not self.at("}") and not self.at("eof"):
                try:
                    key = self.expect("ident", "expected a setting name")
                    self.expect(":", f"expected ':' after setting {key.value!r}")
                    value = self._parse_expr()
                    settings.append(A.AgentSetting(key.value, value, key.line, key.col))
                except ParseError as error:
                    self._report(error)
                    self._sync_member()
                if not self.match(",") and not self.match(";"):
                    continue
            self._close_brace(keyword, "agent")
        return A.AgentDecl(name, agent_type, tuple(settings), keyword.line, keyword.col)

    def _parse_workflow(self) -> A.WorkflowDecl:
        keyword = self.advance()
        name = self.expect("ident", "expected a workflow name").value
        params = self._parse_params()
        returns = self._parse_return_type()
        self.expect("{", "expected '{' to open the workflow")
        requires: A.Requires | None = None
        budget: A.BudgetDecl | None = None
        agents: list[A.AgentDecl] = []
        statements: list[A.Stmt] = []
        seen_statement = False
        while not self.at("}") and not self.at("eof"):
            start = self.i
            try:
                if self.at("requires"):
                    requires = self._workflow_section("requires", requires, seen_statement)
                elif self.at("budget"):
                    budget = self._workflow_section("budget", budget, seen_statement)
                elif self.at("agent"):
                    if seen_statement:
                        self.report.error(
                            "HX2004",
                            "agent declarations must come before the workflow body",
                            *self._pos(),
                        )
                        self._skip_section()
                    else:
                        agents.append(self._parse_agent())
                else:
                    seen_statement = True
                    statements.append(self._parse_stmt())
            except ParseError as error:
                self._report(error)
                self._sync_statement()
            if self.i == start:
                self.advance()
        self._close_brace(keyword, "workflow")
        return A.WorkflowDecl(
            name,
            params,
            returns,
            requires,
            budget,
            tuple(agents),
            A.Block(tuple(statements), keyword.line, keyword.col),
            keyword.line,
            keyword.col,
        )

    def _workflow_section(self, kind: str, current: object, seen_statement: bool) -> object:
        """Parse (or complain about) the one ``requires`` / ``budget`` section."""
        if current is not None:
            self.report.error("HX2005", f"duplicate '{kind}' section", *self._pos())
            self._skip_section()
            return current
        if seen_statement:
            self.report.error(
                "HX2004",
                f"'{kind}' must come before the workflow body",
                *self._pos(),
            )
            self._skip_section()
            return current
        return self._parse_requires() if kind == "requires" else self._parse_budget()

    def _parse_requires(self) -> A.Requires:
        keyword = self.advance()
        capabilities: list[A.Capability] = []
        while True:
            first = self.expect("ident", "expected a capability name, e.g. repo.read")
            parts = [first.value]
            while self.match("."):
                part = self.expect("ident", f"expected a name after {'.'.join(parts)!r}.")
                parts.append(part.value)
            capabilities.append(A.Capability(".".join(parts), first.line, first.col))
            if self.match(","):
                continue
            if self.at("ident") and self.peek(1).kind == ".":
                continue
            break
        return A.Requires(tuple(capabilities), keyword.line, keyword.col)

    def _parse_budget(self) -> A.BudgetDecl:
        keyword = self.advance()
        self.expect("{", "expected '{' after 'budget'")
        entries: list[A.BudgetEntry] = []
        while not self.at("}") and not self.at("eof"):
            try:
                key = self.expect("ident", "expected a budget key")
                self.expect(":", f"expected ':' after budget key {key.value!r}")
                value = self._parse_budget_value()
                entries.append(A.BudgetEntry(key.value, value, key.line, key.col))
            except ParseError as error:
                self._report(error)
                self._sync_member()
            if not self.match(";") and not self.match(","):
                continue
        self._close_brace(keyword, "budget")
        return A.BudgetDecl(tuple(entries), keyword.line, keyword.col)

    def _parse_budget_value(self) -> A.BudgetValue:
        token = self.peek()
        if token.kind in {"int", "float"}:
            self.advance()
            if (
                self.at("ident")
                and len(self.peek().value) == 3
                and self.peek().value.isupper()
            ):
                currency = self.advance()
                return A.BudgetValue(
                    token.kind, token.value, token.line, token.col, currency.value
                )
            return A.BudgetValue(token.kind, token.value, token.line, token.col)
        if token.kind == "duration":
            self.advance()
            return A.BudgetValue("duration", token.value, token.line, token.col)
        if token.kind in {"string", "true", "false"}:
            self.advance()
            return A.BudgetValue(token.kind, token.value, token.line, token.col)
        raise ParseError(token, "HX2001", f"expected a budget value, found {token.describe()}")

    # -- types and parameters --------------------------------------------
    def _parse_type(self) -> A.TypeRef:
        token = self.expect("ident", "expected a type name")
        return self._parse_type_tail(token)

    def _parse_type_tail(self, token: Token) -> A.TypeRef:
        args: list[A.TypeRef] = []
        if self.match("<"):
            args.append(self._parse_type())
            while self.match(","):
                args.append(self._parse_type())
            self.expect(">", "expected '>' to close the type arguments")
        optional = self.match("?")
        return A.TypeRef(token.value, tuple(args), optional, token.line, token.col)

    def _parse_params(self) -> tuple[A.Param, ...]:
        self.expect("(", "expected '(' to open the parameter list")
        params: list[A.Param] = []
        while not self.at(")") and not self.at("eof"):
            try:
                name = self.expect("ident", "expected a parameter name")
                self.expect(":", f"expected ':' after parameter {name.value!r}")
                params.append(A.Param(name.value, self._parse_type(), name.line, name.col))
            except ParseError as error:
                self._report(error)
                self._sync_member()
            if not self.match(","):
                break
        self.expect(")", "expected ')' to close the parameter list")
        return tuple(params)

    def _parse_return_type(self) -> A.TypeRef:
        if self.match("->"):
            return self._parse_type()
        token = self.peek()
        self.report.error(
            "HX2001",
            f"expected '->' and a return type, found {token.describe()}",
            token.line,
            token.col,
        )
        return A.TypeRef("Any", (), False, token.line, token.col)

    def _parse_block(self) -> A.Block:
        keyword = self.expect("{", "expected '{' to open a block")
        statements: list[A.Stmt] = []
        while not self.at("}") and not self.at("eof"):
            start = self.i
            try:
                statements.append(self._parse_stmt())
            except ParseError as error:
                self._report(error)
                self._sync_statement()
            if self.i == start:
                self.advance()
        self._close_brace(keyword, "block")
        return A.Block(tuple(statements), keyword.line, keyword.col)

    # -- statements -------------------------------------------------------
    def _parse_stmt(self) -> A.Stmt:
        token = self.peek()
        if token.kind == "let":
            self.advance()
            name = self.expect("ident", "expected a variable name")
            annotation = None
            if self.match(":"):
                annotation = self._parse_type()
            self.expect("=", f"expected '=' after variable {name.value!r}")
            return A.LetStmt(name.value, annotation, self._parse_expr(), name.line, name.col)
        if token.kind == "ensure":
            self.advance()
            return A.EnsureStmt(self._parse_expr(), token.line, token.col)
        if token.kind == "return":
            self.advance()
            if self.at("}") or self.at(";") or self.at("eof"):
                return A.ReturnStmt(None, token.line, token.col)
            return A.ReturnStmt(self._parse_expr(), token.line, token.col)
        if token.kind == "if":
            return self._parse_if()
        if token.kind == "for":
            return self._parse_for()
        expression = self._parse_expr()
        self.match(";")
        return A.ExprStmt(expression, token.line, token.col)

    def _parse_if(self) -> A.IfStmt:
        keyword = self.advance()
        condition = self._parse_expr()
        then = self._parse_block()
        orelse = self._parse_block() if self.match("else") else None
        return A.IfStmt(condition, then, orelse, keyword.line, keyword.col)

    def _parse_for(self) -> A.ForStmt:
        keyword = self.advance()
        name = self.expect("ident", "expected a loop variable name")
        self.expect("in", f"expected 'in' after loop variable {name.value!r}")
        iterable = self._parse_expr()
        body = self._parse_block()
        return A.ForStmt(name.value, iterable, body, keyword.line, keyword.col)

    # -- expressions ------------------------------------------------------
    def _parse_expr(self) -> A.Expr:
        return self._parse_or()

    def _parse_or(self) -> A.Expr:
        left = self._parse_and()
        while self.match("or"):
            operator = self.tokens[self.i - 1]
            right = self._parse_and()
            left = A.Binary("or", left, right, operator.line, operator.col)
        return left

    def _parse_and(self) -> A.Expr:
        left = self._parse_not()
        while self.match("and"):
            operator = self.tokens[self.i - 1]
            right = self._parse_not()
            left = A.Binary("and", left, right, operator.line, operator.col)
        return left

    def _parse_not(self) -> A.Expr:
        if self.at("not"):
            operator = self.advance()
            return A.Unary("not", self._parse_not(), operator.line, operator.col)
        return self._parse_comparison()

    def _parse_comparison(self) -> A.Expr:
        left = self._parse_addition()
        while self.peek().kind in _COMPARISONS:
            operator = self.advance()
            right = self._parse_addition()
            left = A.Binary(operator.kind, left, right, operator.line, operator.col)
        return left

    def _parse_addition(self) -> A.Expr:
        left = self._parse_unary()
        while self.match("+"):
            operator = self.tokens[self.i - 1]
            right = self._parse_unary()
            left = A.Binary("+", left, right, operator.line, operator.col)
        return left

    def _parse_unary(self) -> A.Expr:
        if self.at("await"):
            keyword = self.advance()
            return A.Await(self._parse_postfix(), keyword.line, keyword.col)
        if self.at("spawn"):
            keyword = self.advance()
            return A.Spawn(self._parse_postfix(), keyword.line, keyword.col)
        return self._parse_postfix()

    def _parse_postfix(self) -> A.Expr:
        expression = self._parse_primary()
        while True:
            if self.match("."):
                name = self.expect("ident", "expected a member name after '.'")
                expression = A.Member(expression, name.value, name.line, name.col)
                continue
            if self.at("("):
                expression = self._parse_call(expression)
                continue
            return expression

    def _parse_call(self, callee: A.Expr) -> A.Call:
        self.expect("(", "expected '(' to open the argument list")
        args: list[A.Arg] = []
        kwargs: list[A.KwArg] = []
        while not self.at(")") and not self.at("eof"):
            if self.at("ident") and self.peek(1).kind == ":":
                name = self.advance()
                self.advance()
                kwargs.append(A.KwArg(name.value, self._parse_expr(), name.line, name.col))
            else:
                args.append(A.Arg(self._parse_expr()))
            if not self.match(","):
                break
        self.expect(")", "expected ')' to close the argument list")
        return A.Call(callee, tuple(args), tuple(kwargs), callee.line, callee.col)

    def _parse_primary(self) -> A.Expr:
        token = self.peek()
        if token.kind == "int":
            self.advance()
            return A.IntLit(int(token.value), token.line, token.col)
        if token.kind == "float":
            self.advance()
            return A.FloatLit(float(token.value), token.line, token.col)
        if token.kind == "string":
            self.advance()
            return A.StringLit(token.value, token.line, token.col)
        if token.kind == "duration":
            self.advance()
            return A.DurationLit(token.value, token.line, token.col)
        if token.kind in {"true", "false"}:
            self.advance()
            return A.BoolLit(token.kind == "true", token.line, token.col)
        if token.kind == "ident":
            self.advance()
            return A.Ident(token.value, token.line, token.col)
        if token.kind == "(":
            self.advance()
            expression = self._parse_expr()
            self.expect(")", "expected ')' to close the expression")
            return expression
        if token.kind == "[":
            self.advance()
            items: list[A.Expr] = []
            while not self.at("]") and not self.at("eof"):
                items.append(self._parse_expr())
                if not self.match(","):
                    break
            self.expect("]", "expected ']' to close the list")
            return A.ListLit(tuple(items), token.line, token.col)
        raise ParseError(token, "HX2001", f"expected an expression, found {token.describe()}")

    # -- recovery ---------------------------------------------------------
    def _pos(self) -> tuple[int, int]:
        token = self.peek()
        return token.line, token.col

    def _skip_section(self) -> None:
        """Drop a duplicate or misplaced section without emitting more noise."""
        kind = self.advance().kind
        if kind == "requires":
            while not self.at("eof") and not self.at("}"):
                if self.peek().kind in _STATEMENT_START:
                    return
                self._skip_one()
            return
        while not self.at("eof"):
            if self.at("{"):
                self._skip_balanced("{", "}")
                return
            if self.at("}") or self.peek().kind in _STATEMENT_START:
                return
            self.advance()

    def _skip_one(self) -> None:
        if self.at("{"):
            self._skip_balanced("{", "}")
        elif self.at("(") or self.at("["):
            self._skip_balanced(self.peek().kind, ")" if self.at("(") else "]")
        else:
            self.advance()

    def _skip_balanced(self, opener: str, closer: str) -> None:
        depth = 0
        while not self.at("eof"):
            if self.at(opener):
                depth += 1
            elif self.at(closer):
                depth -= 1
                if depth <= 0:
                    self.advance()
                    return
            self.advance()

    def _sync_top_level(self) -> None:
        while not self.at("eof"):
            if self.peek().kind in _TOP_LEVEL:
                return
            self._skip_one()

    def _sync_statement(self) -> None:
        while not self.at("eof"):
            if self.peek().kind in _STATEMENT_START or self.peek().kind in {"}", ";"}:
                return
            self._skip_one()

    def _sync_member(self) -> None:
        while not self.at("eof"):
            kind = self.peek().kind
            if kind == "}":
                return
            if kind in {",", ";"}:
                self.advance()
                return
            self._skip_one()

    def _close_brace(self, opener: Token, what: str) -> None:
        if not self.match("}"):
            self.report.error(
                "HX2002",
                f"unclosed '{{' in {what} that starts here",
                opener.line,
                opener.col,
            )


def parse(source: str, reporter: Reporter) -> A.Program:
    tokens = Lexer(source, reporter).run()
    return Parser(tokens, reporter).parse_program()
