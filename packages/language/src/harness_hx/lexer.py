"""Hx lexer — tokens with real positions, and a diagnostic for every surprise.

Two deliberate lexer choices:

* Every malformed character produces a record and is skipped. A lexer that
  dies on the first bad byte gives an agent one problem per round trip.
* A duration (``10m``) is a token, a money amount (``2.00 USD``) is not. Money
  only exists inside a ``budget`` block, so the parser joins the number and the
  currency there instead of teaching the lexer context it does not have.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import Reporter

KEYWORDS = frozenset(
    {
        "module",
        "schema",
        "enum",
        "fn",
        "agent",
        "workflow",
        "requires",
        "budget",
        "let",
        "ensure",
        "return",
        "if",
        "else",
        "for",
        "in",
        "await",
        "spawn",
        "true",
        "false",
        "and",
        "or",
        "not",
        # M8. `use` names a module the host resolves; `accumulate` binds a value per
        # loop pass without assignment; `with` builds a new record from an old one.
        # Every module file is still read by the host, so none of these three is
        # dynamic: there is no `import`, and no name can be resolved at run time.
        "use",
        "accumulate",
        "with",
    }
)

# Longest first: ``->`` must win over ``-``, ``<=`` over ``<``.
_PUNCTUATION = (
    "->",
    "==",
    "!=",
    "<=",
    ">=",
    "{",
    "}",
    "(",
    ")",
    "[",
    "]",
    ",",
    ":",
    ";",
    ".",
    "<",
    ">",
    "=",
    "+",
    "?",
    "-",
    "*",
    "/",
)

_DURATION_UNITS = ("ms", "s", "m", "h")

_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    col: int

    def describe(self) -> str:
        if self.kind == "eof":
            return "end of file"
        if self.kind in {"ident", "int", "float", "duration", "string"}:
            return f"{self.kind} {self.value!r}"
        return f"'{self.kind}'"


class Lexer:
    def __init__(self, source: str, reporter: Reporter) -> None:
        self.src = source
        self.report = reporter
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []

    # -- public -----------------------------------------------------------
    def run(self) -> list[Token]:
        while self.i < len(self.src):
            char = self.src[self.i]
            if char in " \t\r":
                self._advance()
            elif char == "\n":
                self._advance()
            elif self.src.startswith("//", self.i):
                self._line_comment()
            elif self.src.startswith("/*", self.i):
                self._block_comment()
            elif char == '"':
                self._string()
            elif char.isdigit():
                self._number()
            elif char.isalpha() or char == "_":
                self._identifier()
            else:
                self._punctuation(char)
        self.tokens.append(Token("eof", "", self.line, self.col))
        return self.tokens

    # -- internals --------------------------------------------------------
    def _advance(self) -> str:
        char = self.src[self.i]
        self.i += 1
        if char == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return char

    def _emit(self, kind: str, value: str, line: int, col: int) -> None:
        self.tokens.append(Token(kind, value, line, col))

    def _line_comment(self) -> None:
        while self.i < len(self.src) and self.src[self.i] != "\n":
            self._advance()

    def _block_comment(self) -> None:
        start_line, start_col = self.line, self.col
        self._advance()
        self._advance()
        while self.i < len(self.src):
            if self.src.startswith("*/", self.i):
                self._advance()
                self._advance()
                return
            self._advance()
        self.report.error("HX1003", "unterminated block comment", start_line, start_col)

    def _string(self) -> None:
        start_line, start_col = self.line, self.col
        self._advance()
        chars: list[str] = []
        while self.i < len(self.src):
            char = self.src[self.i]
            if char == '"':
                self._advance()
                self._emit("string", "".join(chars), start_line, start_col)
                return
            if char == "\n":
                self.report.error("HX1002", "unterminated string literal", start_line, start_col)
                self._emit("string", "".join(chars), start_line, start_col)
                return
            if char == "\\":
                escape_line, escape_col = self.line, self.col
                self._advance()
                if self.i >= len(self.src):
                    break
                escape = self._advance()
                if escape in _ESCAPES:
                    chars.append(_ESCAPES[escape])
                else:
                    self.report.error(
                        "HX1005",
                        f"invalid escape sequence '\\{escape}'",
                        escape_line,
                        escape_col,
                    )
                continue
            chars.append(self._advance())
        self.report.error("HX1002", "unterminated string literal", start_line, start_col)
        self._emit("string", "".join(chars), start_line, start_col)

    def _number(self) -> None:
        start_line, start_col = self.line, self.col
        start = self.i
        while self.i < len(self.src) and self.src[self.i].isdigit():
            self._advance()
        is_float = False
        if (
            self.i + 1 < len(self.src)
            and self.src[self.i] == "."
            and self.src[self.i + 1].isdigit()
        ):
            is_float = True
            self._advance()
            while self.i < len(self.src) and self.src[self.i].isdigit():
                self._advance()
        text = self.src[start : self.i]
        look = self.i
        while look < len(self.src) and self.src[look].isalpha():
            look += 1
        unit = self.src[self.i : look]
        if unit in _DURATION_UNITS:
            while self.i < look:
                self._advance()
            self._emit("duration", text + unit, start_line, start_col)
            return
        self._emit("float" if is_float else "int", text, start_line, start_col)

    def _identifier(self) -> None:
        start_line, start_col = self.line, self.col
        start = self.i
        while self.i < len(self.src) and (self.src[self.i].isalnum() or self.src[self.i] == "_"):
            self._advance()
        word = self.src[start : self.i]
        self._emit(word if word in KEYWORDS else "ident", word, start_line, start_col)

    def _punctuation(self, char: str) -> None:
        start_line, start_col = self.line, self.col
        for symbol in _PUNCTUATION:
            if self.src.startswith(symbol, self.i):
                for _ in symbol:
                    self._advance()
                self._emit(symbol, symbol, start_line, start_col)
                return
        self.report.error("HX1001", f"unexpected character {char!r}", start_line, start_col)
        self._advance()
