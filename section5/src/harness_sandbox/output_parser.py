"""Tool-output protocol — structured diagnostics for every sandbox command.

Roadmap H1 adopts Hermes' ``hermesc --json`` diagnostic contract as the shape a
tool result must reach the agent in. Hermes prints machine-readable records
``{path,line,col,kind,message}`` as ONE JSON array on **stderr**, and keeps
**stdout** for the program's own output — the two never mix, so a caller parses
instead of guessing. Before this, every tool result was scraped: the parser
grepped ``error:`` out of a single merged blob and handed the agent strings.

Three ingestion tiers, in honour order, recorded per result in
``diagnostics_source`` so nothing claims more precision than it had:

``protocol``  the tool emitted the contract (a JSON array of records on
              stderr, or NDJSON one record per line). Keys from foreign tools
              are mapped by the alias tables below — that is a compatibility
              tier, not the contract; a tool that emits the contract needs no
              alias.
``located``   no JSON, but lines in the classic
              ``path:line:col: kind: message`` form (gcc/clang, Hermes' own
              human output, tsc, eslint stylish). Positions are real.
``scan``      neither. The old keyword heuristic, kept because a test runner
              summary is genuinely useful to a reviewer — but it produces
              position-less records, so the transcript can say "found by
              scanning" instead of implying the file and line are known.

Kind is conservative: an unrecognised severity word becomes ``error``. A
governance product may over-report a failure, never soften one.
"""

import json
import re
from typing import Literal

from pydantic import BaseModel

DiagnosticKind = Literal["error", "warning", "note"]

# The cap is a display bound, not a truth bound: `diagnostics_truncated` says
# when the rest of the records were dropped.
MAX_DIAGNOSTICS = 20


class Diagnostic(BaseModel):
    """One diagnostic record — the Hermes ``--json`` field set, verbatim."""

    path: str | None = None
    line: int | None = None
    col: int | None = None
    kind: DiagnosticKind = "error"
    message: str


DiagnosticSource = Literal["protocol", "located", "scan", "none"]


class StructuredExecResult(BaseModel):
    status: Literal["passed", "failed", "error"]
    exit_code: int
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    duration_ms: int = 0
    summary: str = ""
    diagnostics: list[Diagnostic] = []
    diagnostics_source: DiagnosticSource = "none"
    diagnostics_truncated: bool = False


# ---------------------------------------------------------------------------
# emitter — the writer half of the contract, so tools and tests can round-trip
# ---------------------------------------------------------------------------


def render_diagnostics_json(diagnostics: list[Diagnostic]) -> str:
    """The protocol's wire form: one JSON array, Hermes field order.

    Write it to **stderr**; stdout belongs to the program. Kept as a string
    builder rather than ``json.dumps`` of the model so the field order and the
    absence of null-noise match what ``hermesc --json`` prints (records only
    carry the keys they have).
    """
    parts = []
    for d in diagnostics:
        fields = []
        if d.path is not None:
            fields.append(f'"path":{json.dumps(d.path)}')
        if d.line is not None:
            fields.append(f'"line":{d.line}')
        if d.col is not None:
            fields.append(f'"col":{d.col}')
        fields.append(f'"kind":{json.dumps(d.kind)}')
        fields.append(f'"message":{json.dumps(d.message)}')
        parts.append("{" + ",".join(fields) + "}")
    return "[" + ",".join(parts) + "]"


# ---------------------------------------------------------------------------
# tier 1: the protocol itself
# ---------------------------------------------------------------------------

_PATH_KEYS = ("path", "file", "filename")
_LINE_KEYS = ("line", "line_number", "lineno")
_COL_KEYS = ("col", "column", "colno")
_KIND_KEYS = ("kind", "severity", "level", "code")
_MSG_KEYS = ("message", "msg", "text")

_KIND_WORDS = {
    "error": "error",
    "err": "error",
    "failure": "error",
    "fatal": "error",
    "warning": "warning",
    "warn": "warning",
    "note": "note",
    "info": "note",
    "hint": "note",
    "style": "note",
}


def _first(record: dict, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _kind_of(raw: object) -> DiagnosticKind:
    """Map a tool's severity onto the contract. Unknown -> error (never softer)."""
    if isinstance(raw, bool):
        return "error"
    if isinstance(raw, int):
        # eslint-style numeric severity: 2 error, 1 warning, 0/3 informational.
        return {2: "error", 1: "warning"}.get(raw, "note")
    token = str(raw if raw is not None else "error").strip().lower()
    if token in _KIND_WORDS:
        return _KIND_WORDS[token]
    for word, kind in _KIND_WORDS.items():
        if token.startswith(word):
            return kind
    return "error"


def _int_of(raw: object) -> int | None:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _record_to_diagnostic(record: dict) -> Diagnostic | None:
    message = _first(record, _MSG_KEYS)
    if not isinstance(message, str) or not message.strip():
        return None  # a result object, not a diagnostic — do not pretend
    path = _first(record, _PATH_KEYS)
    return Diagnostic(
        path=path if isinstance(path, str) else None,
        line=_int_of(_first(record, _LINE_KEYS)),
        col=_int_of(_first(record, _COL_KEYS)),
        kind=_kind_of(_first(record, _KIND_KEYS)),
        message=message.strip(),
    )


def parse_protocol_diagnostics(stream: str) -> list[Diagnostic]:
    """Read the contract off a stream: a JSON array, or NDJSON one record/line.

    A stream is only a diagnostic source if its records carry a message; a tool
    that prints an unrelated JSON payload on stderr yields nothing here and the
    next tier takes over — silently ignoring foreign JSON is what keeps the
    transcript honest about where a position came from.
    """
    text = stream.strip()
    if not text:
        return []

    candidates: list[object] = []
    try:
        candidates.append(json.loads(text))
    except ValueError:
        for line in text.splitlines():
            line = line.strip()
            if not line or line[0] not in "[{":
                continue
            try:
                candidates.append(json.loads(line))
            except ValueError:
                continue

    diagnostics: list[Diagnostic] = []
    for candidate in candidates:
        items = candidate if isinstance(candidate, list) else [candidate]
        for item in items:
            if isinstance(item, dict):
                record = _record_to_diagnostic(item)
                if record is not None:
                    diagnostics.append(record)
    return diagnostics


# ---------------------------------------------------------------------------
# tier 2: located text lines — path:line:col[: ]kind: message
# ---------------------------------------------------------------------------

_LOCATION_RE = re.compile(
    r"^(?P<path>[^\s/\\][^:]*|(?:[A-Za-z]:)?[\\/][^:]+):(?P<line>\d{1,6}):(?P<col>\d{1,6})"
    r"\s*[:\-]?\s*(?:(?P<kind>error|failure|fatal|warning|warn|note|info|hint|style)\s*)?:?"
    r"\s*(?P<message>\S.*)$"
)

# tsc / msbuild put the position in parentheses: `app.tsx(17,24): error TS2339: …`
_PAREN_RE = re.compile(
    r"^(?P<path>[^\s()]+)\((?P<line>\d{1,6}),(?P<col>\d{1,6})\)\s*[:\-]?\s*"
    r"(?:(?P<kind>error|warning|note|info)\s*)?(?P<message>\S.*)$"
)

# eslint's stylish rows carry a position but name the file on a separate header
# line, so the position is real and the path stays unknown. Reporting it without
# a path is honest; inventing one by remembering the last header is not.
_BARE_POSITION_RE = re.compile(
    r"^(?P<line>\d{1,6}):(?P<col>\d{1,6})\s+(?P<kind>error|warning|note|info)\s+(?P<message>\S.*)$"
)


def _from_match(match: re.Match, require_kind: bool = False) -> Diagnostic | None:
    kind_word = (match.group("kind") or "").lower()
    message = match.group("message").strip()
    if require_kind and not kind_word:
        return None
    # `main.c:12:5: In function 'main':` — a context line, not a diagnostic.
    if not kind_word and not _looks_like_problem(message):
        return None
    path = match.groupdict().get("path")
    return Diagnostic(
        path=path.strip() if path else None,
        line=int(match.group("line")),
        col=int(match.group("col")),
        kind=_kind_of(kind_word) if kind_word else "error",
        message=message,
    )


def parse_located_line(line: str) -> Diagnostic | None:
    stripped = line.strip()
    for pattern, require_kind in (
        (_LOCATION_RE, False),
        (_PAREN_RE, False),
        (_BARE_POSITION_RE, True),
    ):
        match = pattern.match(stripped)
        if match:
            return _from_match(match, require_kind=require_kind)
    return None


_SCAN_PREFIXES = ("FAILED ", "ERROR ", "FAIL ", "● ")
_SCAN_RE = re.compile(r"\b(error|errors|warning|warnings|failed|failure)\b[:\s]", re.IGNORECASE)

# `FAILED tests/test_api.py::test_login - assert 1 == 0` and pytest's `ERROR …`
# header carry a real node id but no column.
_NODEID_RE = re.compile(
    r"^(?P<prefix>FAILED|ERROR|FAIL)\s+(?P<path>[^\s:]+\.py)(?P<node>::[^:\s]+)?"
    r"(?:\s+-\s+(?P<message>.*))?$"
)


def _looks_like_problem(message: str) -> bool:
    lowered = message.lower()
    return bool(_SCAN_RE.search(lowered)) or lowered.startswith(_SCAN_PREFIXES)


def parse_scan_line(line: str) -> Diagnostic | None:
    """Last resort: a keyword line with no position. Position stays null."""
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith(_SCAN_PREFIXES):
        return Diagnostic(kind="error", message=stripped)
    match = re.search(r"\b(error|errors|warning|warnings|note)\b\s*:", stripped, re.IGNORECASE)
    if match:
        return Diagnostic(kind=_kind_of(match.group(1)), message=stripped)
    return None


def _nodeid_diagnostic(line: str) -> Diagnostic | None:
    match = _NODEID_RE.match(line.strip())
    if not match:
        return None
    message = match.group("message") or ""
    node = match.group("node") or ""
    return Diagnostic(
        kind="error",
        path=match.group("path"),
        message=(f"{match.group('prefix')} {match.group('path')}{node}"
                 + (f" - {message}" if message else "")).strip(),
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _collect(stderr: str, stdout: str) -> tuple[list[Diagnostic], DiagnosticSource]:
    """Highest-fidelity tier available, diagnostics taken where they belong.

    The contract puts diagnostics on stderr, so the protocol and located tiers
    read stderr only. Test-runner failures are the documented exception a
    ``scan`` tier exists for: pytest prints its summary to stdout, so the scan
    pass sees both streams — and says so in ``diagnostics_source``.
    """
    records = parse_protocol_diagnostics(stderr)
    if records:
        return _dedupe(records), "protocol"

    located: list[Diagnostic] = []
    for line in stderr.splitlines():
        diagnostic = parse_located_line(line)
        if diagnostic is not None:
            located.append(diagnostic)
    if located:
        return _dedupe(located), "located"

    scanned: list[Diagnostic] = []
    for line in f"{stdout}\n{stderr}".splitlines():
        diagnostic = _nodeid_diagnostic(line) or parse_scan_line(line)
        if diagnostic is not None:
            scanned.append(diagnostic)
    if scanned:
        return _dedupe(scanned), "scan"
    return [], "none"


def _dedupe(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """One defect reported twice is one defect. Full-record identity only —
    two lines that share a message at different positions both stay."""
    seen: set[tuple] = set()
    unique: list[Diagnostic] = []
    for d in diagnostics:
        key = (d.path, d.line, d.col, d.kind, d.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)
    return unique


_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|error|errors)", re.IGNORECASE)


def _tally(combined: str) -> tuple[int, int, int]:
    passed = failed = skipped = 0
    for count, label in _COUNT_RE.findall(combined):
        value = int(count)
        lowered = label.lower()
        if "pass" in lowered:
            passed += value
        elif "fail" in lowered or "error" in lowered:
            failed += value
        elif "skip" in lowered:
            skipped += value
    return passed, failed, skipped


def parse_exec_output(
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_ms: int = 0,
) -> StructuredExecResult:
    combined = f"{stdout}\n{stderr}".strip()
    passed, failed, skipped = _tally(combined)
    diagnostics, source = _collect(stderr, stdout)

    if exit_code == 0:
        status = "passed"
        if passed == 0 and failed == 0:
            passed = 1
        summary = f"Command '{command}' completed successfully."
    else:
        status = "failed" if exit_code == 1 else "error"
        if failed == 0:
            failed = 1
        summary = f"Command '{command}' failed with exit code {exit_code}."

    return StructuredExecResult(
        status=status,
        exit_code=exit_code,
        passed_count=passed,
        failed_count=failed,
        skipped_count=skipped,
        duration_ms=duration_ms,
        summary=summary,
        diagnostics=diagnostics[:MAX_DIAGNOSTICS],
        diagnostics_source=source,
        diagnostics_truncated=len(diagnostics) > MAX_DIAGNOSTICS,
    )
