"""The review summary (W24): what changed, what was run, what failed, what it cost, how long
it took, and what is still unknown.

It is built only from facts the run recorded: the engine's own tool calls, its token usage, the
diff, the clock. Nothing here is model-written, and nothing unknown is defaulted to a
favourable value: an unknown cost is ``None`` (never ``$0``), a run whose tool calls were not
surfaced has *unknown* tests (never *none*), and a test command that left no exit code is
*unknown* (never *passed*). The harness does not re-run anything; ``basis`` says so.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = 1
BASIS = "recorded from the agent's own tool calls and token usage; the harness re-ran nothing"
# File-writing tools in the engine's roster. `bash` can write too, but no record says when.
MUTATING_TOOLS = frozenset({"write", "edit"})

_RUNNERS = frozenset({"pytest", "py.test", "tox", "nox", "vitest", "jest", "rspec", "phpunit",
                      "ctest", "nosetests"})
_PYTHON_MODULES = frozenset({"pytest", "unittest", "nose2"})
_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
_TOOLCHAINS = frozenset({"go", "cargo", "dotnet", "swift", "deno", "mix", "mvn", "gradle",
                         "gradlew"})
_WRAPPERS = frozenset({"uv", "poetry", "pipenv", "run", "exec", "npx", "time", "env", "nice", "--"})
_NOT_A_RUN = frozenset({"--version", "-h", "--help", "--collect-only", "--co", "--list-tests"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _tokens(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    while tokens:
        first = tokens[0]
        if _ASSIGNMENT.match(first) or first in _WRAPPERS:
            tokens = tokens[1:]
        elif first == "timeout" and len(tokens) > 1:
            tokens = tokens[2:]  # `timeout 60 pytest`: the duration goes with the wrapper
        elif first in _PACKAGE_MANAGERS and tokens[1:2] in (["exec"], ["dlx"]):
            tokens = tokens[2:]
        else:
            break
    return tokens


def _is_test_segment(tokens: list[str]) -> bool:
    if not tokens or _NOT_A_RUN.intersection(tokens):
        return False
    head = os.path.basename(tokens[0])
    rest = [t for t in tokens[1:] if not t.startswith("-")]
    if head in _RUNNERS:
        return True
    if re.fullmatch(r"python[0-9.]*", head):
        return len(tokens) > 2 and tokens[1] == "-m" and tokens[2] in _PYTHON_MODULES
    if head in _PACKAGE_MANAGERS:
        script = rest[1].split(":")[0] if rest[:1] == ["run"] and len(rest) > 1 else None
        return bool(rest) and (rest[0] in {"test", "t"} or script == "test")
    if head in _TOOLCHAINS:
        return rest[:1] == ["test"]
    if head == "make":
        return any(t in {"test", "tests", "check"} for t in rest)
    return False


_EXIT_IN_SUMMARY = re.compile(r"\(exit (-?\d+|timeout)\)\s*$")


def call_summary(tool: Any, exit_code: int | str | None) -> str:
    """The text stored in ``agent_tool_calls.summary`` for an engine call.

    The table has no exit-status column, and a tool result's stored output is cut at the head, so
    the status would otherwise be lost; it rides at the end of the summary instead.
    """
    return f"engine {tool} call" + (f" (exit {exit_code})" if exit_code is not None else "")


def exit_code_from_summary(summary: str | None) -> int | str | None:
    """The exit status :func:`call_summary` stored, or None when none was recorded."""
    found = _EXIT_IN_SUMMARY.search(summary or "")
    if found is None:
        return None
    return found.group(1) if found.group(1) == "timeout" else int(found.group(1))


def is_test_command(command: str) -> bool:
    """True when any segment of a (possibly compound) shell command invokes a test runner.

    The first word of each segment decides, so ``grep pytest requirements.txt`` is not a test
    run, and ``--version`` / ``--collect-only`` style calls are not either.
    """
    segments = re.split(r"&&|\|\||[;|\n]", command or "")
    return any(_is_test_segment(_tokens(part)) for part in segments)


def diff_files(
    diff_text: str, changed: Sequence[str], deleted: Sequence[str]
) -> list[dict[str, Any]]:
    """Per-file ``{path, change, added, removed}`` from a unified diff.

    The name lists are authoritative for *which* files changed; the diff only supplies the kind
    and the line counts. A file the diff does not cover (truncated, or binary) keeps
    ``change: "changed"`` and ``None`` counts rather than a guess.
    """
    stats: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    in_hunk = False
    for line in (diff_text or "").splitlines():
        if line.startswith("diff --git "):
            current, in_hunk = None, False
        elif not in_hunk and line.startswith("--- "):
            current = {"change": "modified", "added": 0, "removed": 0, "path": None}
            if line.strip() == "--- /dev/null":
                current["change"] = "added"
            else:
                current["path"] = line[4:].strip().removeprefix("a/").lstrip("/")
        elif not in_hunk and line.startswith("+++ ") and current is not None:
            if line.strip() == "+++ /dev/null":
                current["change"] = "deleted"
            else:
                current["path"] = line[4:].strip().removeprefix("b/").lstrip("/")
            if current["path"]:
                stats[current["path"]] = current
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and current is not None:
            if line.startswith("+"):
                current["added"] += 1
            elif line.startswith("-"):
                current["removed"] += 1
    out = []
    for path in dict.fromkeys([*changed, *deleted]):
        found = stats.get(path)
        change = "deleted" if path in deleted else (found["change"] if found else "changed")
        out.append({"path": path, "change": change,
                    "added": found["added"] if found else None,
                    "removed": found["removed"] if found else None})
    return out


def _test_runs(calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    runs = []
    for index, call in enumerate(calls):
        if call.get("tool") != "bash" or not is_test_command(str(call.get("target") or "")):
            continue
        code = call.get("exitCode")
        if call.get("outcome") == "error" and code is None:
            result = "refused"  # denied by the command policy or the tool faulted: it did not run
        elif code == 0:
            result = "passed"
        elif code == "timeout":
            result = "timeout"
        elif isinstance(code, int):
            result = "failed"
        else:
            result = "unknown"  # no exit status was recorded: say so, never assume a pass
        runs.append({"command": str(call.get("target"))[:200], "result": result, "_at": index})
    return runs


def build_review_summary(
    *,
    files: Sequence[Mapping[str, Any]],
    tool_calls: Sequence[Mapping[str, Any]] | None,
    usage: Mapping[str, Any] | None,
    model: str | None,
    cost_usd: float | None,
    cost_source: str | None,
    elapsed_seconds: float | None,
    status: str | None,
    diff_omitted: str | None = None,
) -> dict[str, Any]:
    """The summary for one run. ``tool_calls`` is ``None`` when the engine surfaced none (unknown),
    ``[]`` when it surfaced an empty list; either way the caller passes them in call order."""
    uncertainty: list[dict[str, str]] = []

    def unsure(code: str, text: str) -> None:
        uncertainty.append({"code": code, "text": text})

    failures: list[dict[str, str]] = []
    if status and status != "completed":
        failures.append({"kind": "run", "what": f"the run ended {status}"})
        unsure("RUN_NOT_COMPLETED", f"the run ended {status}, so the result may be partial")

    tests: dict[str, Any]
    if tool_calls is None:
        tests = {"known": False, "runs": []}
        unsure("TOOL_CALLS_NOT_SURFACED",
               "no tool calls were recorded for this run, so which tests ran (and whether they "
               "passed) is unknown")
    else:
        runs = _test_runs(tool_calls)
        tests = {"known": True, "runs": [{k: v for k, v in r.items() if k != "_at"} for r in runs]}
        if runs:
            tests["note"] = (
                "exit status only; pass and fail counts, and why a command failed, are not recorded"
            )
        errored = [
            f"{c.get('tool')} {str(c.get('target') or '').strip()}".strip()[:80]
            for c in tool_calls
            if c.get("outcome") == "error" and not is_test_command(str(c.get("target") or ""))
        ]
        if errored:
            shown = "; ".join(errored[:3]) + ("; ..." if len(errored) > 3 else "")
            failures.append({"kind": "tool",
                             "what": f"{len(errored)} tool call(s) refused or errored: {shown}"})
        last_by_command: dict[str, dict[str, Any]] = {}
        for run in runs:  # the latest run of a command decides: fix then green is not a failure
            last_by_command[run["command"]] = run
        for run in last_by_command.values():
            if run["result"] in {"failed", "timeout", "refused"}:
                failures.append({"kind": "test", "what": f"{run['command']} {run['result']}"})
        if not runs:
            unsure("NO_TESTS_RUN", "no test command was run")
        else:
            if any(r["result"] == "unknown" for r in runs):
                unsure("TEST_RESULT_UNKNOWN",
                       "a test command left no exit status, so its result is unknown")
            last_test = max(r["_at"] for r in runs)
            if any(c.get("tool") in MUTATING_TOOLS for c in tool_calls[last_test + 1:]):
                unsure("EDITED_AFTER_LAST_TEST", "a file was written after the last test run")

    in_tokens = usage.get("inputTokens") if usage else None
    out_tokens = usage.get("outputTokens") if usage else None
    if cost_usd is None:
        if usage is None:
            unsure("USAGE_UNAVAILABLE", "no token usage was recorded, so the cost is unknown")
        else:
            unsure("COST_UNPRICED",
                   "tokens are known but no price covers this model, so the cost is unknown")
    if elapsed_seconds is None:
        unsure("ELAPSED_UNKNOWN", "no start and end time were recorded")
    if diff_omitted:
        unsure("DIFF_OMITTED",
               f"the diff was cut ({diff_omitted}); later files are not in the list or counts")
    if not files and not diff_omitted:
        unsure("NO_CHANGES", "the run changed no file")

    return {
        "schema": SCHEMA_VERSION,
        "basis": BASIS,
        "files": [dict(f) for f in files],
        "tests": tests,
        "failures": failures,
        "cost": {"usd": cost_usd, "source": cost_source if cost_usd is not None else None,
                 "model": model, "inputTokens": in_tokens, "outputTokens": out_tokens},
        "elapsedSeconds": None if elapsed_seconds is None else round(float(elapsed_seconds), 1),
        "uncertainty": uncertainty,
    }
