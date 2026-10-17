"""S4: reusable failure-reproduction bundles (F044) and automatic reproduction from an
issue / stack trace (F045).

Safety stance: issue text and stack traces are DATA. Nothing from them is ever executed or
interpolated into a command. Candidate commands are assembled only from fixed templates plus
path tokens that (a) match a strict charset, (b) resolve inside the workspace, (c) exist, and
(d) are not secret / forbidden. The assembled command then still goes through the sandbox
command policy like any other.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import (
    SecurityPolicyViolationError,
    is_secret_file,
    validate_path_edit,
)

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
_PY_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>\S+))?')
_JS_FRAME = re.compile(r"at (?:(?P<func>[^\s(]+) )?\(?(?P<file>[^\s():]+):(?P<line>\d+):\d+\)?")
_PY_EXC = re.compile(r"^(?P<type>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt|Warning))"
                     r"(?::\s*(?P<msg>.*))?$", re.M)
_TYPE_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


@dataclass
class Frame:
    file: str
    line: int
    func: str | None = None


@dataclass
class ParsedTrace:
    frames: list[Frame] = field(default_factory=list)
    exception_type: str | None = None
    message: str = ""


def parse_stack_trace(text: str) -> ParsedTrace:
    text = text[:50_000]
    out = ParsedTrace()
    for m in _PY_FRAME.finditer(text):
        out.frames.append(Frame(m["file"], int(m["line"]), m["func"]))
    if not out.frames:
        for m in _JS_FRAME.finditer(text):
            out.frames.append(Frame(m["file"], int(m["line"]), m["func"]))
    excs = list(_PY_EXC.finditer(text))
    if excs:
        out.exception_type = excs[-1]["type"].split(".")[-1]
        out.message = (excs[-1]["msg"] or "")[:300]
    return out


def traces_from_issue(issue_text: str) -> list[ParsedTrace]:
    """Pull traces out of fenced blocks / raw text. Commands mentioned in the issue are ignored."""
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", issue_text, re.S) or [issue_text]
    traces = [parse_stack_trace(b) for b in blocks if "Traceback" in b or " at " in b]
    return [t for t in traces if t.frames]


def _resolve(workspace: Path, raw: str, forbidden: list[str]) -> str | None:
    p = Path(raw)
    parts = [x for x in p.parts if x not in ("/",)]
    # try the longest suffix of the traced path that exists in the workspace
    for i in range(len(parts)):
        rel = "/".join(parts[i:])
        if not _SAFE_PATH.match(rel) or ".." in rel.split("/"):
            continue
        cand = (workspace / rel).resolve()
        if cand.is_file() and cand.is_relative_to(workspace.resolve()):
            try:
                validate_path_edit(rel, forbidden)
            except SecurityPolicyViolationError:
                return None
            return rel
    return None


def plan_commands(workspace: Path, trace: ParsedTrace, forbidden: list[str] | None = None
                  ) -> list[str]:
    forbidden = forbidden or []
    workspace = workspace.resolve()
    cmds: list[str] = []
    seen: set[str] = set()
    for fr in reversed(trace.frames):          # innermost frame last in python tracebacks
        rel = _resolve(workspace, fr.file, forbidden)
        if not rel or rel in seen or is_secret_file(rel):
            continue
        seen.add(rel)
        name = Path(rel).name
        if rel.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
            node = fr.func if fr.func and re.match(r"^test_\w+$", fr.func) else None
            cmds.append(f"python3 -m pytest {rel}{'::' + node if node else ''} -x -q")
        elif rel.endswith(".py"):
            module = Path(rel).stem
            if not re.match(r"^\w+$", module):
                continue
            for t in sorted(workspace.rglob("test_*.py")):
                trel = t.relative_to(workspace).as_posix()
                if not _SAFE_PATH.match(trel) or trel in seen:
                    continue
                try:
                    if re.search(rf"\b{re.escape(module)}\b", t.read_text(errors="ignore")):
                        cmds.append(f"python3 -m pytest {trel} -x -q")
                        seen.add(trel)
                except OSError:
                    pass
    return cmds[:5]


@dataclass
class Bundle:
    bundle_id: str
    team_id: str
    command: str
    exception_type: str | None
    files: dict[str, str]
    created_at: float


class BundleStore:
    """Tenant-scoped, bounded (count + bytes) store of reproduction bundles."""

    def __init__(self, root: Path, max_bundles: int = 50, max_bytes: int = 50 << 20) -> None:
        self.root, self.max_bundles, self.max_bytes = root.resolve(), max_bundles, max_bytes

    def _dir(self, team: str) -> Path:
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", team):
            raise ValueError("bad team id")
        return self.root / team

    def capture(self, team: str, workspace: Path, command: str, exception_type: str | None,
                paths: list[str], forbidden: list[str] | None = None) -> Bundle:
        workspace = workspace.resolve()
        bid = f"fb_{int(time.time() * 1000)}"
        dest = self._dir(team) / bid / "files"
        dest.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}
        total = 0
        for rel in paths:
            if not _SAFE_PATH.match(rel) or ".." in rel.split("/") or is_secret_file(rel):
                continue
            src = (workspace / rel).resolve()
            try:
                validate_path_edit(rel, forbidden)
            except SecurityPolicyViolationError:
                continue
            if not src.is_file() or not src.is_relative_to(workspace) or src.is_symlink():
                continue
            total += src.stat().st_size
            if total > self.max_bytes:
                shutil.rmtree(dest.parent)
                raise ValueError("bundle exceeds size quota")
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
            files[rel] = "ok"
        b = Bundle(bid, team, command, exception_type, files, time.time())
        (dest.parent / "bundle.json").write_text(json.dumps(asdict(b)))
        for old in self.list(team)[self.max_bundles:]:        # retention: newest N
            shutil.rmtree(self._dir(team) / old.bundle_id, ignore_errors=True)
        return b

    def list(self, team: str) -> list[Bundle]:
        d = self._dir(team)
        if not d.exists():
            return []
        bs = [Bundle(**json.loads((p / "bundle.json").read_text()))
              for p in d.iterdir() if (p / "bundle.json").exists()]
        return sorted(bs, key=lambda b: b.created_at, reverse=True)

    def load(self, team: str, bundle_id: str) -> tuple[Bundle, Path]:
        if not re.match(r"^fb_\d+$", bundle_id):
            raise KeyError(bundle_id)
        p = self._dir(team) / bundle_id
        if not (p / "bundle.json").exists():
            raise KeyError(bundle_id)
        return Bundle(**json.loads((p / "bundle.json").read_text())), p / "files"


@dataclass
class ReproReport:
    status: str            # reproduced | flaky | not_reproduced
    hits: int
    attempts: int
    detail: str = ""


async def reproduce(bundle: Bundle, files_dir: Path, runner: LocalSandboxRunner, task_id: str,
                    attempts: int = 3, forbidden: list[str] | None = None) -> ReproReport:
    """Re-run the bundle in a fresh sandbox task; deterministic iff every attempt matches."""
    handle = runner.create_task(task_id, "bundle", "main", forbidden, team_id=bundle.team_id)
    try:
        ws: Path = runner.active_tasks[task_id]["workspace"]
        for rel in bundle.files:
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(files_dir / rel, ws / rel)
        hits, detail = 0, ""
        for _ in range(attempts):
            res = await runner.exec_structured(handle, bundle.command)
            text = res.summary + "\n" + "\n".join(res.diagnostics)
            matched = res.exit_code != 0 and (
                bundle.exception_type is None
                or (_TYPE_OK.match(bundle.exception_type) and bundle.exception_type in text))
            hits += bool(matched)
            detail = text[-300:]
        status = ("reproduced" if hits == attempts else "not_reproduced" if hits == 0 else "flaky")
        return ReproReport(status, hits, attempts, detail)
    finally:
        runner.destroy(handle)


async def reproduce_from_trace(
    trace_text: str, workspace: Path, runner: LocalSandboxRunner, store: BundleStore, team: str,
    forbidden: list[str] | None = None,
) -> tuple[Bundle | None, list[dict]]:
    """Try each safely-derived candidate command; keep the first that fails with the traced
    exception type, and save it as a bundle. Returns (bundle|None, attempts log)."""
    trace = parse_stack_trace(trace_text)
    log: list[dict] = []
    for i, cmd in enumerate(plan_commands(workspace, trace, forbidden)):
        tid = f"repro-{int(time.time() * 1000)}-{i}"
        h = runner.create_task(tid, "scratch", "main", forbidden, team_id=team)
        try:
            ws: Path = runner.active_tasks[tid]["workspace"]
            shutil.copytree(workspace, ws, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv",
                                                          "node_modules"))
            res = await runner.exec_structured(h, cmd)
        except SecurityPolicyViolationError as e:
            log.append({"command": cmd, "outcome": "policy_denied", "reason": str(e)})
            continue
        finally:
            runner.destroy(h)
        text = res.summary + "\n" + "\n".join(res.diagnostics)
        ok = res.exit_code != 0 and (trace.exception_type is None or trace.exception_type in text)
        log.append({"command": cmd, "outcome": "reproduced" if ok else "no_match",
                    "exit": res.exit_code})
        if ok:
            paths = [p.relative_to(workspace.resolve()).as_posix()
                     for p in workspace.resolve().rglob("*.py")
                     if ".venv" not in p.parts and "__pycache__" not in p.parts][:200]
            return store.capture(team, workspace, cmd, trace.exception_type, paths, forbidden), log
    return None, log
