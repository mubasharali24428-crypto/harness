import re
from typing import Literal

from pydantic import BaseModel


class StructuredExecResult(BaseModel):
    status: Literal["passed", "failed", "error"]
    exit_code: int
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    duration_ms: int = 0
    summary: str = ""
    diagnostics: list[str] = []


def parse_exec_output(
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_ms: int = 0,
) -> StructuredExecResult:
    combined = f"{stdout}\n{stderr}".strip()
    diagnostics = []

    passed = 0
    failed = 0
    skipped = 0

    pattern = r"(\d+)\s+(passed|failed|skipped|error|errors)"
    for match in re.finditer(pattern, combined, re.IGNORECASE):
        count = int(match.group(1))
        label = match.group(2).lower()
        if "pass" in label:
            passed += count
        elif "fail" in label or "error" in label:
            failed += count
        elif "skip" in label:
            skipped += count

    for line in combined.splitlines():
        line_clean = line.strip()
        if (
            line_clean.startswith("FAILED ")
            or line_clean.startswith("ERROR ")
            or line_clean.startswith("● ")
            or line_clean.startswith("FAIL ")
        ):
            diagnostics.append(line_clean)
        elif "error:" in line_clean.lower() or "warning:" in line_clean.lower():
            diagnostics.append(line_clean)

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
        diagnostics=diagnostics[:20],
    )


def parse_output(*args, **kwargs):
    return parse_exec_output(*args, **kwargs)
