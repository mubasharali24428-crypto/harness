"""F050: per-change verification scorecard.

Every check carries an *evidence kind*: executed (we ran it), static_inference (derived without
running), skipped (deliberately not run, with reason) or blocked (could not run). The verdict
is conservative: skipped/blocked required evidence can never produce a plain pass.
verdict: fail | insufficient_evidence | pass_weak | pass
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .discovery import discover
from .mutation import run_mutation
from .repair import run_pytest


@dataclass
class Check:
    name: str
    evidence: str              # executed | static_inference | skipped | blocked
    passed: bool | None        # None when not executed
    detail: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Scorecard:
    changed: list[str]
    checks: list[Check]
    verdict: str
    reasons: list[str]
    generated_at: float
    evidence_digest: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def build_scorecard(root: Path, changed: list[str], *, mutation_threshold: float = 0.8,
                    run_mutation_check: bool = True, max_mutants: int = 30,
                    contract: Check | None = None, python: str = sys.executable) -> Scorecard:
    root = root.resolve()
    checks: list[Check] = []
    reasons: list[str] = []
    py_src = [c for c in changed if c.endswith(".py") and not _is_test(c) and (root / c).exists()]

    d = discover(root, changed)
    checks.append(Check("test_discovery", "static_inference", True,
                        f"{len(d.tests)} test file(s) selected via import graph/name matching",
                        {"tests": [asdict(t) for t in d.tests], "unmatched": d.unmatched_changed,
                         "run_all": d.run_all, "notes": d.notes}))
    tests = d.paths()
    if d.run_all:
        tests = []                                         # pytest with no args = everything
    covered_all = not d.unmatched_changed or all(not u.endswith(".py") for u in d.unmatched_changed)

    if not tests and not d.run_all:
        checks.append(Check("tests", "blocked", None, "no tests found for this change"))
        reasons.append("no test selected for the changed files")
    elif "pytest" not in d.runners and not any(t.endswith(".py") for t in tests) and not d.run_all:
        checks.append(Check("tests", "blocked", None, "only non-python runners detected; not executed"))
        reasons.append("test runner not supported by this tool in this environment")
    else:
        t0 = time.monotonic()
        fail = run_pytest(root, tests, python=python)
        checks.append(Check("tests", "executed", fail is None,
                            "all selected tests passed" if fail is None else
                            f"{len(fail.ids)} failing: {', '.join(fail.ids[:5])}",
                            {"seconds": round(time.monotonic() - t0, 2), "selected": tests,
                             "failing": fail.ids if fail else []}))

    tests_check = next((c for c in checks if c.name == "tests"), None)
    if run_mutation_check and py_src and tests and tests_check and tests_check.passed:
        rep = run_mutation(root, py_src, tests, max_mutants=max_mutants, python=python)
        if rep.status != "executed":
            checks.append(Check("mutation", "blocked", None, rep.note))
        else:
            ok = rep.score is not None and rep.score >= mutation_threshold
            checks.append(Check(
                "mutation", "executed", ok,
                f"score {rep.score} on {len(rep.results)} mutants (threshold {mutation_threshold})",
                {"score": rep.score, "survivors": [
                    {"file": r.mutant.file, "line": r.mutant.line, "op": r.mutant.op,
                     "detail": r.mutant.detail} for r in rep.survived],
                 "sampled": rep.sampled, "total_sites": rep.total_sites}))
    elif run_mutation_check and py_src:
        checks.append(Check("mutation", "skipped", None, "tests missing or failing; mutation needs a green baseline"))
    else:
        checks.append(Check("mutation", "skipped", None, "no changed python sources or disabled"))

    if shutil.which("ruff") or Path(python).with_name("ruff").exists():
        ruff = str(Path(python).with_name("ruff")) if Path(python).with_name("ruff").exists() else "ruff"
        files = [c for c in changed if c.endswith(".py") and (root / c).exists()]
        if files:
            r = subprocess.run([ruff, "check", "--no-cache", *files], cwd=root, capture_output=True,
                               text=True, check=False)
            checks.append(Check("lint", "executed", r.returncode == 0, r.stdout[-300:].strip()))
        else:
            checks.append(Check("lint", "skipped", None, "no changed python files"))
    else:
        checks.append(Check("lint", "blocked", None, "ruff not installed"))

    checks.append(contract or Check("contract", "skipped", None, "no contract check configured"))

    failed = [c for c in checks if c.evidence == "executed" and c.passed is False]
    if failed:
        verdict, reasons = "fail", reasons + [f"{c.name} failed" for c in failed]
    elif tests_check is None or tests_check.evidence != "executed":
        verdict = "insufficient_evidence"
    elif not covered_all:
        verdict = "insufficient_evidence"
        reasons.append("changed source file(s) have no associated test: " + ", ".join(d.unmatched_changed))
    else:
        mut = next((c for c in checks if c.name == "mutation"), None)
        if py_src and (mut is None or mut.evidence != "executed"):
            verdict = "pass_weak"
            reasons.append("tests pass but test strength was not measured")
        else:
            verdict = "pass"
    sc = Scorecard(changed, checks, verdict, reasons, time.time())
    body = json.dumps({"changed": changed, "checks": [asdict(c) for c in checks], "verdict": verdict},
                      sort_keys=True)
    sc.evidence_digest = hashlib.sha256(body.encode()).hexdigest()
    return sc


def _is_test(rel: str) -> bool:
    from .discovery import is_test_file
    return is_test_file(rel)
