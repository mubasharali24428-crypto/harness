# W31 — Scheduler planning experiment

**Status:** experiment, not product code.
**Authority:** everything here proposes assignments only. It does not queue,
dispatch, approve, retry or widen permissions. The Harness run queue remains
authoritative, and no real task is executed.

This is the W31 comparison required by
[`docs/m2-continuation-plan.md`](../../docs/m2-continuation-plan.md): adapt the
Scheduler's timetable-specific contract to synthetic agent workloads, compare it
with a fresh OR-Tools model, and record whether the adapter is worth shipping.

## What is compared

| Path | What it is |
|---|---|
| `w31/model.py` | Harness-owned, time-indexed CP-SAT model. One Boolean per `(task, resource, start)`; capacity is a per-slot load. Independent of Scheduler code. |
| `solver/allocation.py` (Scheduler repo) | New neutral allocation model beside the frozen timetable shapes. Interval CP-SAT, same request shape. |
| `w31/bridge.py` | Translates the neutral request into the Scheduler's frozen `Session`/`Room` timetable request, calls its public `solve()`, and maps placements back. |
| `w31/contract.py` | Independent validator. Checks coverage, duration, capability, availability, dependency, deadline and capacity from the request alone. Shares no solver internals. |
| `w31/explain.py` | Deletion-based explanation: removes tasks while the remainder stays infeasible, so the survivor is irreducible by single-task deletion. |

The Scheduler is imported by path (`SCHEDULER_ROOT`, default
`/Users/user/scheduler`) and is not vendored: that repository has no `LICENSE`
file, so copying its code into the Harness would be a licensing decision that
W31 is not allowed to make.

## Run

```bash
cd experiments/w31-scheduler
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

PYTHONPATH=. .venv/bin/python -m pytest tests -q
SCHEDULER_ROOT=/Users/user/scheduler PYTHONPATH=. .venv/bin/python w31/compare.py --json report.json
```

`make test`, `make compare` and `make lint` in this directory do the same with
the active interpreter. The experiment is deliberately not wired into the
Harness CI matrix: it needs `ortools` plus an external repository whose license
is unresolved, and adding either to product CI would imply an adoption decision
that has not been made.

## Measured results

From [`report.json`](report.json), 2026-11-04, Python 3.13.13, OR-Tools
9.15.6755, Scheduler `77d895e`. Latency is the median of three runs and includes
input validation and model construction.

### Feasibility mode (no objective; first complete assignment)

| Tasks | Fresh time-indexed | Scheduler interval | Ratio |
|---:|---:|---:|---:|
| 10 | 279 ms | 6 ms | 47x |
| 50 | 2,557 ms | 25 ms | 102x |
| 100 | 4,505 ms | 56 ms | 80x |
| 200 | 6,514 ms | 127 ms | 51x |

Both models returned a complete, independently valid assignment at every size.

### Optimized mode (10-second budget, makespan then weighted completion)

| Tasks | Fresh | Scheduler | Fresh makespan | Scheduler makespan |
|---:|---|---|---:|---:|
| 10 | optimal, 530 ms | optimal, 28 ms | 4 | 4 |
| 50 | feasible, 10.2 s | feasible, 10.0 s | 40 | 54 |
| 100 | feasible, 10.4 s | feasible, 10.0 s | 100 | 98 |
| 200 | **unknown, no assignment** | feasible, 10.0 s | — | 100 |

At 10 tasks both models proved the same optimum and the same weighted
completion (61). At 50 the fresh model found a better makespan; at 100 the
Scheduler model found a slightly better one. At 200 the fresh time-indexed model
could not produce a first assignment inside the budget while the Scheduler model
could. Optimization quality at a fixed budget is therefore workload-dependent,
not a clean win for either encoding.

### Timetable bridge (expressible workload only)

| Tasks | Fresh (same workload) | Timetable bridge | Bridge violations |
|---:|---|---:|---:|
| 10 | optimal, 1,257 ms | solved, 65 ms | 0 |
| 50 | feasible, 10.4 s | solved, 348 ms | 0 |
| 100 | feasible, 10.8 s | solved, 629 ms | 0 |
| 200 | unknown | solved, 1,035 ms | 0 |

The bridge is fast because the Scheduler's default timetable engine is a
constructive heuristic, not because the contract is more expressive. Its
placements passed the allocation validator at every size.

## What the timetable contract cannot express

The bridge reports these instead of dropping them:

| Neutral feature | Timetable |
|---|---|
| arbitrary duration | only 60-180 minute blocks |
| dependencies | no field |
| deadlines | no field |
| per-task priority | no field |
| horizon beyond one week | 100 slots (Mon-Fri, 08:00-18:00) |
| concurrent capacity > 1 | emulated by duplicating the room per capacity unit |
| capabilities | `required_features` / room `features` |
| availability | day/time windows on the fixed week grid |

Capacity duplication is a lossy adaptation: two copies of one room are two
independent timetable resources. The mapping back reuses the original resource
id, so the allocation validator still sees one capacity-2 resource and checks
overlap correctly.

## Failures found and resolved

1. **Scheduler cycle walker crashed on an unknown dependency.**
   `KeyError: 'ghost'` while building the explanation-side graph. Unknown ids
   are already reported as contract drift, so the walker now skips them.
   Regression test: `test_unknown_dependency_is_contract_drift`.
2. **Scheduler feasibility mode reported an unconstrained makespan.** With no
   objective, the solver may leave the makespan variable anywhere above the last
   end; it reported 100 slots for a 13-slot schedule. The objective is now
   derived from the returned assignments in both modes.
3. **Bridge turned a full-day window into 08:00-08:00.** The per-day split used
   modulo arithmetic, so a window ending at a day boundary collapsed to zero
   width. It now uses day-relative offsets; the window test pins the result.
4. **`solver.engine` is not exported by `solver/__init__.py`.** The bridge now
   calls the public `solve()` for the heuristic path and imports
   `solver.engine.solve_current` only when another engine is requested.
5. **Timeout never fabricates a plan.** At a 10 ms limit both allocation models
   return `unknown` with zero assignments and a reason; a timeout is not reported
   as a partial or feasible schedule.

## Verdict

See [`docs/decisions/2026-11-04-w31-scheduler.md`](../../docs/decisions/2026-11-04-w31-scheduler.md).
