"""Local-workspace mode for the CLI (W6 slice 4).

The gate is unchanged — propose → approve happens on the server exactly as for
a web run — and what follows is NOT a second engine: the confined turn executes
through `deploy/runners/local_engine.py`, which builds the orchestrator's own
`DeepSeekHarnessWrapper` with the container boundary (same image, same
`confine` launch laws) and computes the diff inside that boundary. This module
only: locates the launcher, parses its NDJSON, and applies the result back to
the user's directory — file copies limited to the paths the boundary's diff
named, deletions from the diff's own hunk headers, never an inference.

Honest boundary (recorded in docs/week-6-plan.md): server-side audit covers the
plan gate (identical to a web run), the uploaded diff artifact, and a run
comment carrying `executed=local` provenance. Off-host tool-call events cannot
be minted into `agent_events` by any existing endpoint; the launcher's NDJSON is
kept and reported, never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .commands import CliError

_ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = _ROOT / "deploy" / "runners" / "local_engine.py"


class ApplyConflict(CliError):
    """The workspace moved since these bytes were reviewed; nothing was written.

    The engine's diff is computed against a copy of the directory at launch. If
    the file on disk no longer hashes to that copy's base digest, writing the
    run's bytes would be a silent overwrite of someone else's edit — a second
    run's result, an editor save, a rebase. The apply is all-or-nothing.
    """

    code = "APPLY_CONFLICT"

    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        self.conflicts = conflicts
        names = ", ".join(str(c["path"]) for c in conflicts[:5])
        more = "" if len(conflicts) <= 5 else f" (+{len(conflicts) - 5} more)"
        super().__init__(f"the workspace changed since review; refusing to overwrite: "
                         f"{names}{more}")


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def launcher_python(environ: dict[str, str] | None = None) -> str:
    """The interpreter that can import section4: the repo venv, or an explicit override."""
    env = os.environ if environ is None else environ
    override = env.get("HARNESS_LOCAL_PYTHON", "").strip()
    candidates = [override] if override else [
        str(_ROOT / "section4" / ".venv" / "bin" / "python"),
        sys.executable,
    ]
    for cand in candidates:
        if cand and Path(cand).exists():
            return cand
    raise CliError("no interpreter found for the local launcher (set HARNESS_LOCAL_PYTHON)")


def _describe_progress(record: Any) -> str:
    """One compact line per worker tool event — enough to see what it is doing."""
    if not isinstance(record, dict):
        return str(record)[:160]
    tool = record.get("tool") or record.get("name") or record.get("type") or "event"
    detail = record.get("input") or record.get("command") or record.get("args") or ""
    text = json.dumps(detail, default=str) if not isinstance(detail, str) else detail
    return f"{tool} {text[:120]}"


def launch(
    directory: str,
    goal: str,
    run_id: str,
    *,
    shell_policy: dict[str, Any] | None = None,
    timeout: float = 900.0,
    note: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """One confined local turn through the repo launcher; its files survive scratch.

    The result carries `workDir`/`baselineDir` (temp dirs owned by the caller —
    remove them after applying) and `changed` (the names the boundary diffed).
    """
    if not LAUNCHER.is_file():
        raise CliError(
            f"local mode needs the repo launcher at {LAUNCHER} — it is not installed "
            "with the SDK alone; run from a harness checkout"
        )
    work_out = Path(tempfile.mkdtemp(prefix=f"harness-apply-{run_id}-"))
    argv = [
        launcher_python(), str(LAUNCHER),
        "--dir", directory, "--goal", goal, "--run-id", run_id,
        "--timeout", str(int(timeout)), "--work-out", str(work_out),
    ]
    policy_file = None
    if shell_policy:
        fd, policy_file = tempfile.mkstemp(prefix="harness-policy-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(shell_policy, handle)
        argv += ["--policy-file", policy_file]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=timeout + 120, check=False,  # exit codes carry the refusal
        )
    finally:
        if policy_file:
            os.unlink(policy_file)
    records = []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            note(f"launcher: {line}")  # never swallow unexpected output silently
    for record in records:
        if "progress" in record:
            # The worker's own tool events. They cannot become server agent_events
            # from off-host, so they are at least shown, never silently dropped.
            note(f"engine: {_describe_progress(record['progress'])}")
    refusal = next((r["refusal"] for r in records if "refusal" in r), None)
    if refusal:
        shutil.rmtree(work_out, ignore_errors=True)
        raise CliError(f"local engine refused: {refusal}")
    result = next((r for r in records if "engine_result" in r), None)
    if result is None:
        shutil.rmtree(work_out, ignore_errors=True)
        tail = (proc.stderr or "").strip()[-400:]
        raise CliError(f"local engine produced no result (exit {proc.returncode}): {tail}")
    merged = dict(result["engine_result"])
    merged["diff"] = next((r.get("diff", "") for r in records if "diff" in r), "")
    merged["workDir"] = str(work_out)
    return merged


def _safe_rel(rel: str, kind: str) -> str:
    if not rel or ".." in Path(rel).parts or rel.startswith(".git"):
        raise CliError(f"engine diff names an unsafe path ({kind}): {rel}")
    return rel


def apply_back(
    workspace: Path,
    work_dir: Path,
    changed: list[str],
    deleted: list[str] | None = None,
    expected: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Copy exactly the diff-named engine outputs into DIR; remove proven deletions.

    `changed` and `deleted` are the boundary's own lists (the worker-image diff
    and its hunk headers) — never an inference from "absent", which would
    delete every untouched file. A name that would resolve outside the
    workspace is a refusal: the diff is engine output, but the apply is the
    client's promise not to be walked out of the sandbox.

    `expected` maps each path to the sha256 it had when the run copied the
    workspace (``None`` for "did not exist"). When given, every named path is
    checked BEFORE anything is written; any mismatch raises ``ApplyConflict``
    and no file is touched. This is the hash anchor that makes a second
    concurrent result refuse instead of silently overwriting the first.
    """
    workspace = workspace.resolve()
    work_dir = work_dir.resolve()
    changed = [_safe_rel(r, "change") for r in changed]
    deleted = [_safe_rel(r, "delete") for r in (deleted or [])]
    if expected is not None:
        conflicts = []
        for rel in dict.fromkeys([*changed, *deleted]):
            if rel not in expected:
                continue
            target = (workspace / rel).resolve()
            if workspace not in target.parents:
                raise CliError(f"refusing to touch outside the workspace: {rel}")
            actual = _sha256(target)
            if actual != expected[rel]:
                conflicts.append({"path": rel, "expected": expected[rel], "actual": actual})
        if conflicts:
            raise ApplyConflict(conflicts)
    applied = []
    for rel in changed:
        src = (work_dir / rel).resolve()
        if work_dir not in src.parents and src != work_dir:
            raise CliError(f"engine output names a path outside the work dir: {rel}")
        if not src.is_file():
            continue
        dst = (workspace / rel).resolve()
        if workspace not in dst.parents:
            raise CliError(f"refusing to write outside the workspace: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        applied.append(rel)
    removed = []
    for rel in deleted:
        target = (workspace / rel).resolve()
        if workspace not in target.parents:
            raise CliError(f"refusing to delete outside the workspace: {rel}")
        if target.is_file():
            target.unlink()
            removed.append(rel)
    return {"applied": applied, "deleted": removed}
