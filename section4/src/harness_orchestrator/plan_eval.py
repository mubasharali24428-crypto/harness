"""Phase 7.7 (C-07): offline evaluation of proposed assignment plans on held-out scenarios.

FUTURE PLAN 2.0 §12, 7.7: "Evaluate plan usefulness, role fit, omissions, unsafe assignments and
human corrections on held-out scenarios and pilot feedback. Record disagreement, not only
completion. Done when: results with failures recorded."

This module scores any proposer with the same signature as ``assignment_plan.propose``
(``(Brief, set[str]) -> list[dict]``) against scenarios whose expected answers the proposer never
sees. It is read-only and offline: no database, no network, no model call, nothing dispatched.

Scenario file (JSON, ``schema: 1``)::

    {"schema": 1, "revision": "...", "scenarios": [{
        "id": "...", "description": "...",
        "team": [{"userId": "sec", "role": "engineer", "capabilities": ["otp"]}],
        "brief": { ...assignment_plan.Brief... },
        "expected": {
            "rejected": false,                       # true: the brief itself must be refused
            "tasks": {"threat": {"acceptable": ["sec"], "unsafe": ["intern"]}}
        },
        "review": {"approved": {"threat": "sec"}}    # optional: what a human admin approved
    }]}

``team[].capabilities`` are held-out labels for scoring only. The default proposer gets what the
live route gives the planner: seat user ids and which seats are read-only (viewer or guest).
``acceptable: []`` means nobody on the team is a good fit, so the honest answer is UNASSIGNED
with the gap named.

Per task the scorer records one ``fit`` verdict and any number of ``issues``:

* fit: ``fit`` (acceptable assignee, or honestly unassigned when nobody fits), ``misfit`` (an
  assignee outside the acceptable set), ``unfilled`` (unassigned although someone fits),
  ``overclaimed`` (assigned although nobody fits).
* unsafe issues (any one makes the task unsafe): ``non_member``, ``read_only_assignee`` (viewer or
  guest given work), ``listed_unsafe``.
* honesty issues: ``silent_gap`` (unassigned with no missing expertise), ``asserted_fit`` /
  ``asserted_availability`` / ``invented_cost`` (a value Harness has no data for),
  ``dependency_changed``.
* plan issues: ``omitted`` (a brief task with no assignment), ``invented_task``.

Human corrections (disagreement) are recorded per task whenever the scenario's approved
assignee differs from the proposal, and are reported as a list, not only as a rate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from harness_orchestrator.assignment_plan import Brief, propose

Proposer = Callable[[Brief, set[str]], list[dict[str, Any]]]

READ_ONLY_ROLES = {"viewer", "guest"}
UNSAFE_ISSUES = {"non_member", "read_only_assignee", "listed_unsafe"}


def _rejection_reason(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        err = detail.get("error", {})
        return f"{err.get('code', exc.status_code)}: {err.get('message', '')}".strip()
    return f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


def score_task(key: str, brief_task: dict[str, Any], a: dict[str, Any] | None,
               expected: dict[str, Any], roles: dict[str, str]) -> dict[str, Any]:
    """Score one brief task's assignment. ``a`` is None when the proposer omitted the task."""
    if a is None:
        return {"task": key, "assignee": None, "fit": "unfilled", "issues": ["omitted"],
                "unsafe": False}
    who = a.get("assignee")
    acceptable = set(expected.get("acceptable", []))
    issues: list[str] = []
    if who is not None:
        if who not in roles:
            issues.append("non_member")
        elif roles[who] in READ_ONLY_ROLES:
            issues.append("read_only_assignee")
        if who in set(expected.get("unsafe", [])):
            issues.append("listed_unsafe")
    elif not a.get("missingExpertise"):
        issues.append("silent_gap")
    if a.get("capabilityFit", "unverified") != "unverified":
        issues.append("asserted_fit")
    if a.get("availability", "unknown") != "unknown":
        issues.append("asserted_availability")
    if a.get("costEstimate", "unknown") != "unknown":
        issues.append("invented_cost")
    if list(a.get("dependsOn", [])) != list(brief_task.get("dependsOn", [])):
        issues.append("dependency_changed")
    if acceptable:
        fit = "fit" if who in acceptable else ("unfilled" if who is None else "misfit")
    else:
        fit = "fit" if who is None else "overclaimed"
    return {"task": key, "assignee": who, "fit": fit, "issues": issues,
            "unsafe": bool(UNSAFE_ISSUES & set(issues))}


def _live_proposer(roles: dict[str, str]) -> Proposer:
    """The planner exactly as the live route calls it: seat ids plus which seats are read-only."""
    read_only = {u for u, r in roles.items() if r in READ_ONLY_ROLES}
    return lambda brief, members: propose(brief, members, read_only)


def score_scenario(sc: dict[str, Any], proposer: Proposer | None = None) -> dict[str, Any]:
    roles = {m["userId"]: m["role"] for m in sc["team"]}
    expected = sc.get("expected", {})
    want_rejected = bool(expected.get("rejected", False))
    out: dict[str, Any] = {"id": sc["id"], "expectedRejected": want_rejected}
    try:
        brief = Brief.model_validate(sc["brief"])
        assignments = (proposer or _live_proposer(roles))(brief, set(roles))
    except (HTTPException, ValidationError) as exc:
        out.update(rejected=True, rejectionReason=_rejection_reason(exc), tasks=[],
                   corrections=[], invented=[],
                   passed=want_rejected)
        if not want_rejected:
            out["failures"] = [f"brief refused unexpectedly ({out['rejectionReason']})"]
        else:
            out["failures"] = []
        return out
    out["rejected"] = False
    if want_rejected:
        out.update(tasks=[], corrections=[], invented=[], passed=False,
                   failures=["brief was accepted but should have been refused"])
        return out
    by_task = {a["task"]: a for a in assignments}
    brief_tasks = {t["key"]: t for t in sc["brief"]["tasks"]}
    exp_tasks = expected.get("tasks", {})
    tasks = [score_task(k, t, by_task.get(k), exp_tasks.get(k, {}), roles)
             for k, t in brief_tasks.items()]
    invented = sorted(set(by_task) - set(brief_tasks))
    approved = sc.get("review", {}).get("approved", {})
    corrections = [{"task": k, "proposed": by_task.get(k, {}).get("assignee"), "approved": v}
                   for k, v in approved.items() if by_task.get(k, {}).get("assignee") != v]
    failures = [f"{t['task']}: {', '.join(t['issues'])}" for t in tasks if t["issues"]]
    failures += [f"{t['task']}: {t['fit']} (assignee {t['assignee']!r})"
                 for t in tasks if t["fit"] != "fit"]
    failures += [f"invented task {k}" for k in invented]
    out.update(tasks=tasks, invented=invented, corrections=corrections,
               passed=not failures, failures=failures)
    return out


def evaluate(doc: dict[str, Any], proposer: Proposer | None = None) -> dict[str, Any]:
    if doc.get("schema") != 1:
        raise ValueError(f"unsupported scenario schema {doc.get('schema')!r}")
    results = [score_scenario(sc, proposer) for sc in doc["scenarios"]]
    tasks = [t for r in results for t in r["tasks"]]
    plans = [r for r in results if not r["rejected"]]
    reviewed_tasks = sum(len(sc.get("review", {}).get("approved", {}))
                         for sc in doc["scenarios"])
    corrections = [dict(c, scenario=r["id"]) for r in results for c in r["corrections"]]
    n = len(tasks)
    summary = {
        "scenarios": len(results),
        "scenariosPassed": sum(r["passed"] for r in results),
        "plansProduced": len(plans),
        "tasksScored": n,
        "fit": sum(t["fit"] == "fit" for t in tasks),
        "misfit": sum(t["fit"] == "misfit" for t in tasks),
        "unfilled": sum(t["fit"] == "unfilled" for t in tasks),
        "overclaimed": sum(t["fit"] == "overclaimed" for t in tasks),
        "unsafeAssignments": sum(t["unsafe"] for t in tasks),
        "omissions": sum("omitted" in t["issues"] for t in tasks),
        "honestyIssues": sum(bool({"silent_gap", "asserted_fit", "asserted_availability",
                                   "invented_cost"} & set(t["issues"])) for t in tasks),
        "rejectionsCorrect": sum(r["rejected"] == r["expectedRejected"] for r in results),
        "humanCorrections": len(corrections),
        "humanReviewedTasks": reviewed_tasks,
    }
    summary["fitRate"] = round(summary["fit"] / n, 3) if n else None
    summary["correctionRate"] = (round(len(corrections) / reviewed_tasks, 3)
                                 if reviewed_tasks else None)
    return {"schema": 1, "revision": doc.get("revision"), "summary": summary,
            "corrections": corrections, "results": results}


def render_markdown(report: dict[str, Any], scenario_sha: str, proposer_name: str) -> str:
    s = report["summary"]
    lines = [
        f"# Plan evaluation — {report['revision']}",
        "",
        f"- Proposer: `{proposer_name}` · scenarios sha256 `{scenario_sha[:16]}`",
        f"- Scenarios passed: **{s['scenariosPassed']} / {s['scenarios']}** · tasks scored: "
        f"{s['tasksScored']} · fit rate: {s['fitRate']}",
        f"- Unsafe assignments: **{s['unsafeAssignments']}** · omissions: {s['omissions']} · "
        f"misfit: {s['misfit']} · unfilled: {s['unfilled']} · overclaimed: {s['overclaimed']} · "
        f"honesty issues: {s['honestyIssues']}",
        f"- Brief refusals correct: {s['rejectionsCorrect']} / {s['scenarios']}",
        f"- Human corrections: {s['humanCorrections']} of {s['humanReviewedTasks']} reviewed "
        f"tasks (rate {s['correctionRate']})",
        "",
        "| Scenario | Result | Failures |",
        "|---|---|---|",
    ]
    for r in report["results"]:
        fails = "; ".join(r["failures"]) or "none"
        lines.append(f"| `{r['id']}` | {'PASS' if r['passed'] else 'FAIL'} | {fails} |")
    if report["corrections"]:
        lines += ["", "## Human corrections (disagreement)", "",
                  "| Scenario | Task | Proposed | Approved |", "|---|---|---|---|"]
        lines += [f"| `{c['scenario']}` | `{c['task']}` | {c['proposed']} | {c['approved']} |"
                  for c in report["corrections"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scenarios", type=Path)
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--md-out", type=Path)
    args = ap.parse_args(argv)
    raw = args.scenarios.read_bytes()
    report = evaluate(json.loads(raw))
    sha = hashlib.sha256(raw).hexdigest()
    report["scenarioSha256"] = sha
    md = render_markdown(report, sha, "harness_orchestrator.assignment_plan.propose")
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2) + "\n")
    if args.md_out:
        args.md_out.write_text(md)
    sys.stdout.write(md)
    s = report["summary"]
    # Exit 1 when the plan is unsafe or incomplete, so a regression is visible in a gate.
    return 1 if s["unsafeAssignments"] or s["omissions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
