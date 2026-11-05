"""W31 experiment — compare the Scheduler with a fresh model on synthetic work.

Three readers of the same request shape:

* `w31.model` — Harness-owned, time-indexed CP-SAT (fresh model).
* `solver.allocation` — the Scheduler's neutral allocation model (interval CP-SAT).
* `w31.bridge` — the Scheduler's timetable contract, adapted to the allocation shape.

Every solved result is checked by `w31.contract.validate`, which shares no solver
internals. Latency is measured over repeated runs and reported with all samples,
not just the number that looked best.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from w31 import bridge, contract, fixtures, model

DEFAULT_SIZES = (10, 50, 100, 200)
DEFAULT_REPEAT = 3
DEFAULT_TIME_LIMIT_S = 10.0
REPO_ROOT = Path(__file__).resolve().parents[3]

CONSTRAINT_COVERAGE = (
    {
        "feature": "capability tags",
        "fresh": "hard",
        "scheduler_allocation": "hard",
        "timetable_bridge": "hard via required_features",
    },
    {
        "feature": "arbitrary duration (1+ slots)",
        "fresh": "hard",
        "scheduler_allocation": "hard",
        "timetable_bridge": "not expressible outside 2-6 slots (60-180 min)",
    },
    {
        "feature": "dependencies",
        "fresh": "hard",
        "scheduler_allocation": "hard",
        "timetable_bridge": "not expressible",
    },
    {
        "feature": "deadlines",
        "fresh": "hard",
        "scheduler_allocation": "hard",
        "timetable_bridge": "not expressible",
    },
    {
        "feature": "concurrent capacity > 1",
        "fresh": "hard",
        "scheduler_allocation": "hard",
        "timetable_bridge": "emulated by duplicating a room per capacity unit",
    },
    {
        "feature": "availability windows",
        "fresh": "hard",
        "scheduler_allocation": "hard",
        "timetable_bridge": "hard on the 08:00-18:00 Mon-Fri grid",
    },
    {
        "feature": "horizon beyond one week",
        "fresh": "hard",
        "scheduler_allocation": "hard",
        "timetable_bridge": "not expressible above 100 slots",
    },
    {
        "feature": "per-task priority",
        "fresh": "objective tie-break",
        "scheduler_allocation": "objective tie-break",
        "timetable_bridge": "not expressible",
    },
    {
        "feature": "objective",
        "fresh": "makespan, then weighted completion",
        "scheduler_allocation": "makespan, then weighted completion",
        "timetable_bridge": "timetable score (unplaced, gaps, home room)",
    },
)


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _environment() -> dict:
    try:
        import ortools

        ortools_version = ortools.__version__
    except ImportError:  # pragma: no cover - the experiment's requirements pin it
        ortools_version = None
    scheduler = bridge.scheduler_root()
    return {
        "python": platform.python_version(),
        "ortools": ortools_version,
        "harness_commit": _git(REPO_ROOT, "rev-parse", "HEAD"),
        "harness_dirty": bool(_git(REPO_ROOT, "status", "--porcelain")),
        "scheduler_root": str(scheduler) if scheduler else None,
        "scheduler_commit": _git(scheduler, "rev-parse", "HEAD") if scheduler else None,
        "scheduler_dirty": bool(_git(scheduler, "status", "--porcelain")) if scheduler else None,
    }


def _summarize(result: dict, request: dict) -> dict:
    solved = result["status"] in {"optimal", "feasible"}
    assignments = result.get("assignments") or []
    payload = contract.load(request)
    violations = contract.validate(request, assignments) if solved else []
    objective = result.get("objective")
    if solved and objective is None and not violations:
        objective = contract.objective(request, assignments)
    return {
        "status": result["status"],
        "assigned": len(assignments),
        "task_count": len(payload["tasks"]),
        "violations": violations,
        "objective": objective,
        "reason": result.get("reason"),
        "optimized": result.get("optimized", True),
    }


def _timed(run, repeat: int) -> tuple[dict, list[int]]:
    samples: list[int] = []
    result = None
    for _ in range(repeat):
        result = run()
        samples.append(result["elapsed_ms"])
    return result, samples


def _latency(result: dict, samples: list[int]) -> dict:
    return {
        "median_ms": int(statistics.median(samples)),
        "min_ms": min(samples),
        "samples_ms": samples,
        "internal_elapsed_ms": result.get("elapsed_ms"),
    }


def _allocation_solver():
    from solver.allocation import solve_allocation

    return solve_allocation


def _compare_allocation(size: int, repeat: int, time_limit_s: float, optimize: bool) -> dict:
    request = fixtures.allocation_workload(size)
    entry = {
        "size": size,
        "workload": "allocation",
        "mode": "optimized" if optimize else "feasibility",
    }

    fresh_result, fresh_samples = _timed(
        lambda: model.solve(request, time_limit_s=time_limit_s, optimize=optimize), repeat
    )
    entry["fresh"] = {**_summarize(fresh_result, request), "latency": _latency(fresh_result, fresh_samples)}

    if bridge.load_scheduler() is None:
        entry["scheduler_allocation"] = {"status": "unavailable"}
        entry["agreement"] = {"both_solved": False, "reason": "Scheduler not found"}
        return entry

    try:
        solve_allocation = _allocation_solver()
    except Exception as error:  # noqa: BLE001 — an old revision is evidence, not a crash
        entry["scheduler_allocation"] = {
            "status": "unavailable",
            "reason": f"{type(error).__name__}: {error}",
        }
        entry["agreement"] = {"both_solved": False, "reason": "neutral allocation module missing"}
        return entry

    scheduler_result, scheduler_samples = _timed(
        lambda: solve_allocation(request, time_limit_s=time_limit_s, optimize=optimize), repeat
    )
    entry["scheduler_allocation"] = {
        **_summarize(scheduler_result, request),
        "latency": _latency(scheduler_result, scheduler_samples),
    }
    fresh, scheduler = entry["fresh"], entry["scheduler_allocation"]
    agree = {
        "both_solved": fresh["status"] in {"optimal", "feasible"}
        and scheduler["status"] in {"optimal", "feasible"},
        "both_valid": not fresh["violations"] and not scheduler["violations"],
        "status_equal": fresh["status"] == scheduler["status"],
    }
    if optimize and fresh["objective"] and scheduler["objective"]:
        agree["makespan_equal"] = (
            fresh["objective"]["makespan_slots"] == scheduler["objective"]["makespan_slots"]
        )
        agree["weighted_completion_equal"] = (
            fresh["objective"]["weighted_completion"]
            == scheduler["objective"]["weighted_completion"]
        )
    entry["agreement"] = agree
    return entry


def _compare_timetable(size: int, repeat: int, time_limit_s: float) -> dict:
    request = fixtures.timetable_workload(size)
    entry = {"size": size, "workload": "timetable-expressible"}

    fresh_result, fresh_samples = _timed(
        lambda: model.solve(request, time_limit_s=time_limit_s), repeat
    )
    entry["fresh"] = {**_summarize(fresh_result, request), "latency": _latency(fresh_result, fresh_samples)}

    translated = bridge.translate(request)
    entry["translation"] = {
        "ok": translated["ok"],
        "unsupported": translated["unsupported"],
        "sessions": len(translated["request"]["sessions"]),
        "rooms": len(translated["request"]["rooms"]),
    }
    timetable_result, timetable_samples = _timed(
        lambda: bridge.run(request, engine="heuristic"), repeat
    )
    entry["timetable_bridge"] = {
        **_summarize(timetable_result, request),
        "latency": _latency(timetable_result, timetable_samples),
        "scheduler_score": timetable_result.get("scheduler_score"),
        "scheduler_conflicts": len(timetable_result.get("scheduler_conflicts") or []),
    }
    fresh, timetable = entry["fresh"], entry["timetable_bridge"]
    entry["agreement"] = {
        "both_solved": fresh["status"] in {"optimal", "feasible"}
        and timetable["status"] == "solved",
        "timetable_valid": not timetable["violations"],
        "same_task_count": fresh["assigned"] == timetable["assigned"],
    }
    return entry


def build_report(sizes=DEFAULT_SIZES, repeat: int = DEFAULT_REPEAT, time_limit_s: float = DEFAULT_TIME_LIMIT_S) -> dict:
    if repeat < 1:
        raise ValueError("repeat must be >= 1")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": _environment(),
        "config": {"sizes": list(sizes), "repeat": repeat, "time_limit_s": time_limit_s},
        "allocation": {
            "feasibility": [_compare_allocation(size, repeat, time_limit_s, optimize=False) for size in sizes],
            "optimized": [_compare_allocation(size, repeat, time_limit_s, optimize=True) for size in sizes],
        },
        "timetable": [_compare_timetable(size, repeat, time_limit_s) for size in sizes],
        "constraint_coverage": list(CONSTRAINT_COVERAGE),
    }


def _print_table(report: dict) -> None:
    sections = [
        ("allocation/feasibility", report["allocation"]["feasibility"], "scheduler_allocation"),
        ("allocation/optimized", report["allocation"]["optimized"], "scheduler_allocation"),
        ("timetable", report["timetable"], "timetable_bridge"),
    ]
    for section, entries, column in sections:
        print(f"== {section} ==")
        for entry in entries:
            fresh, other = entry["fresh"], entry[column]
            fresh_latency = fresh["latency"]["median_ms"]
            other_latency = other.get("latency", {}).get("median_ms", "-")
            violations = len(fresh["violations"]) + len(other.get("violations") or [])
            print(
                f"{entry['size']:>4} tasks  fresh {fresh['status']:<10} {fresh_latency:>6} ms  "
                f"{column} {other['status']:<15} {other_latency!s:>6}  violations {violations}  "
                f"agreement {entry['agreement']}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default=",".join(str(size) for size in DEFAULT_SIZES))
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT_S)
    parser.add_argument("--json", type=Path, help="write the full report here instead of stdout")
    arguments = parser.parse_args(argv)
    sizes = tuple(int(part) for part in arguments.sizes.split(",") if part.strip())
    report = build_report(sizes, arguments.repeat, arguments.time_limit)
    _print_table(report)
    text = json.dumps(report, indent=2) + "\n"
    if arguments.json:
        arguments.json.write_text(text)
        print(f"wrote {arguments.json}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
