"""Hx diagnostics — the frozen Harness tool-output protocol, one record per problem.

The wireshape is owned by Section 5 (``harness_sandbox.output_parser``) and is
reproduced here, not extended: ``{path,line,col,kind,message}``, absent keys
omitted, one JSON array (or NDJSON) on **stderr**, program output on stdout.

Hx carries a stable error code, but it rides *inside* ``message`` as a leading
``[HXnnnn]`` instead of becoming a sixth field. That keeps a consumer that only
knows the five frozen names working, and it means adding a code can never break
a reader — the same reason Section 5 refuses to invent positions it does not
have.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

Kind = Literal["error", "warning", "note"]


@dataclass(frozen=True)
class Diagnostic:
    """One record. ``code`` is presentation-only; ``text`` is what goes on the wire."""

    code: str
    kind: Kind
    message: str
    path: str | None = None
    line: int | None = None
    col: int | None = None

    @property
    def text(self) -> str:
        return f"[{self.code}] {self.message}" if self.code else self.message

    def wire(self) -> dict[str, object]:
        """The exact key set, in the exact order Section 5 emits."""
        record: dict[str, object] = {}
        if self.path is not None:
            record["path"] = self.path
        if self.line is not None:
            record["line"] = self.line
        if self.col is not None:
            record["col"] = self.col
        record["kind"] = self.kind
        record["message"] = self.text
        return record

    def sort_key(self) -> tuple[object, ...]:
        return (self.path or "", self.line or 0, self.col or 0, self.kind, self.code, self.text)


def render_json(diagnostics: list[Diagnostic]) -> str:
    """One JSON array — the protocol tier — built by hand so the bytes are stable.

    ``json.dumps`` of the dicts would be equivalent content-wise, but the field
    order and the absence of null-noise are part of what Section 5 parses, and
    a hand-built string keeps both pinned in one place.
    """
    records = []
    for diagnostic in diagnostics:
        wire = diagnostic.wire()
        fields = []
        for key in ("path", "line", "col"):
            if key in wire:
                fields.append(f'"{key}":{json.dumps(wire[key])}')
        fields.append(f'"kind":{json.dumps(wire["kind"])}')
        fields.append(f'"message":{json.dumps(wire["message"])}')
        records.append("{" + ",".join(fields) + "}")
    return "[" + ",".join(records) + "]"


def render_line(diagnostic: Diagnostic) -> str:
    """The located tier — ``path:line:col: kind: message``, nothing invented.

    A record without a position prints without one; the parser in Section 5
    treats an unlocated line as a scan, which is honest about what is known.
    """
    prefix = diagnostic.path or ""
    if diagnostic.line is not None:
        prefix = f"{prefix}:{diagnostic.line}"
        if diagnostic.col is not None:
            prefix = f"{prefix}:{diagnostic.col}"
    if prefix:
        return f"{prefix}: {diagnostic.kind}: {diagnostic.text}"
    return f"{diagnostic.kind}: {diagnostic.text}"


class Reporter:
    """Accumulates records for one source file and hands back a stable order.

    Dedupe is by (position, kind, message): two passes that notice the same
    problem must not produce two records, because a review gate that double
    counts reads as twice as broken as it is.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._records: list[Diagnostic] = []

    def _add(self, code: str, kind: Kind, message: str, line: int | None, col: int | None) -> None:
        self._records.append(Diagnostic(code, kind, message, self.path, line, col))

    def error(
        self, code: str, message: str, line: int | None = None, col: int | None = None
    ) -> None:
        self._add(code, "error", message, line, col)

    def warning(
        self, code: str, message: str, line: int | None = None, col: int | None = None
    ) -> None:
        self._add(code, "warning", message, line, col)

    def note(
        self, code: str, message: str, line: int | None = None, col: int | None = None
    ) -> None:
        self._add(code, "note", message, line, col)

    def diagnostics(self) -> list[Diagnostic]:
        unique: dict[tuple[object, ...], Diagnostic] = {}
        for record in self._records:
            key = (record.line, record.col, record.kind, record.code, record.text)
            unique.setdefault(key, record)
        return sorted(unique.values(), key=Diagnostic.sort_key)

    def errors(self) -> int:
        """Counted after dedupe, so a gate never reads twice as broken as it is."""
        return sum(1 for record in self.diagnostics() if record.kind == "error")
