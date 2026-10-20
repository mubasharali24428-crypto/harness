# Facilitator guide: measuring the pilot baseline

Use this before day 1. Ask the supervisor to nominate one recorder and one reviewer. You are measuring how the team works without the harness, then describing how a comparable set of pilot tasks went. You are not running a controlled experiment. Never enter the fictional example as real evidence.

## Choose the tasks before seeing results

Select at least five tasks without the harness and plan at least five comparable pilot tasks. Prefer consecutive eligible tasks in a fixed date window. Write the selection rule first: similar type of maintenance work, repository, expected size, review requirements and team experience. Record exclusions and their reasons before timing. Include unsuccessful tasks; do not keep only quick wins. Avoid repeating the identical task after someone has already learned its solution. If task sizes differ substantially, use separate groups and explain the difference instead of combining them into a speed claim.

## Decide what the clock means

Recommended manual protocol: start when a fully specified task is assigned and ready to begin; stop when the reviewer records the final acceptance/rejection decision or the team explicitly abandons it. Record timestamps and elapsed wall-clock minutes, including review waits and rework. Record unusual interruptions separately; do not silently subtract them. Apply the same policy to every manual task. If a task remains open, keep its sheet open rather than inventing an elapsed time. State the cutoff and report unfinished work separately.

The tool's pilot clock is DIFFERENT: it measures run creation to the latest qualifying completion/failure/PR event or `run.stopped` audit event. It excludes negative durations. This may stop before human acceptance or cover several stages/restarts. Record pilot task/run IDs and human review times separately so the mismatch remains visible. Five created runs are not enough: `pilotN` counts valid elapsed durations. The tool accepts `--since YYYY-MM-DD --until YYYY-MM-DD` to select runs by UTC creation time (since inclusive, until exclusive); it has no individual task-selection argument. Without either flag it includes all team history. All activity associated with selected runs remains included, even after the window. Coordinate the cohort with the supervisor and disclose the selection. Do not secretly drop inconvenient runs.

“Accepted” on the manual sheet means the named reviewer says the prewritten acceptance checklist is met. Include rework before that decision. Rejected/abandoned is false. The pilot tool counts finished runs that have at least one `review.approved` audit row (distinct runs, so the rate cannot exceed 1). It is still an audit-row proxy; do not relabel it as independently verified human acceptance.

## Record and enter the baseline

Use the sheet below for every task. Then copy `pilot-baseline-template.json` to your own baseline file and replace its placeholder values. Use exactly these fields:

- `teamId`: your actual team identifier, checked with the supervisor.
- `capturedFrom` / `capturedTo`: ISO dates, such as `2026-10-01`, covering the observations.
- `method`: selection rule, start/stop definitions, wait/rework policy, reviewer, exclusions, and any mismatch with pilot timing.
- `tasks`: one object per completed observation, with a unique string `id`, finite nonnegative numeric `minutes`, and boolean `accepted` (`true` or `false`, without quotes).

Minutes may be decimals. Do not enter hours as minutes, use 0 for missing data, quote boolean values, include trailing commas, or include secrets. Keep detailed timestamps/reviewer notes on the separate sheets. Before querying the database, the CLI validates the required metadata, exact team id, strict date range, non-empty method, unique task IDs, finite non-negative minutes, and boolean acceptance values. It does not decide whether tasks are truly comparable or independently verify the human-entered observations; the facilitator and reviewer own those judgments.

Ask an operator to run, with the intended database configured:

```
HARNESS_DATABASE_URL=... section4/.venv/bin/python -m harness_orchestrator.pilot_metrics TEAM_ID --baseline baseline.json
```

`baselineN` counts nonnegative durations; baseline acceptance uses every listed task. `claimable` is false unless baselineN AND pilotN are each at least five. A true value only unlocks descriptive median/acceptance comparisons. It does not establish causation, productivity improvement, significance or ROI. A zero baseline median produces no percentage change. Different tasks, selection, review waits, familiarity and tool timing all confound interpretation.

## Printable recording sheet (one task; print this section)

**Team:** __________________ **Task ID:** __________________ **Recorder:** __________________

**Dates/window:** __________________ **Workflow: without harness / pilot** __________________

**Task description and size/category:** __________________________________________________

_______________________________________________________________________________________

**Why comparable / selection rule:** _____________________________________________________

**Acceptance checklist, agreed before start:** ____________________________________________

_______________________________________________________________________________________

**Reviewer:** __________________ **Pilot run ID(s), if any:** ______________________________

**Start date/time/timezone:** __________________ **Stop date/time/timezone:** _______________

**Elapsed minutes:** __________ **Included waits/rework:** _________________________________

**Interruptions / deviations:** __________________________________________________________

**Final decision: accepted / rejected / abandoned / still open** ___________________________

**Decision date and reviewer initials:** __________________________________________________

**Exclusion, if any, and when/why decided:** _______________________________________________

**Entered into JSON / checked by:** ______________________________________________________

---

## FICTIONAL worked example and validation

`evidence/pilot-prep/baseline-example-FICTIONAL.json` contains five invented tasks, 40/55/45/70/50 minutes, four accepted. The fictional baseline median is 50 minutes. It is training material only; no actual pilot or baseline was measured.

Validation command from repo root: `section4/.venv/bin/python /tmp/harness-codex-validate-baseline.py` (exit 0). The temporary script checks fields against the unchanged template, string/date types, unique task IDs, finite nonnegative numeric minutes and boolean acceptance, then calls the actual `harness_orchestrator.pilot_metrics.compare` with fictional reports at n=4 and n=5. Output retained in `evidence/pilot-prep/baseline-validation.log`:

```
VALID: template fields/types, dates, unique task IDs, 5 finite nonnegative durations, boolean acceptance
```

Actual consumer output at n=4: `claimable: false`, verdict `insufficient data (need at least 5 tasks on each side); no claim`.
At n=5 with a fictional 40-minute pilot median: `claimable: true`, `medianTimeChangePct: -20.0`, verdict `descriptive only: different tasks, not a controlled comparison`.

For a durable consumer check without the temporary script, load the example with `json.load`, import `compare` from `harness_orchestrator.pilot_metrics`, and pass `{'elapsedSeconds': {'n': 5, 'median': 2400}, 'runs': {'finished': 5, 'accepted': 4}}`. This uses the same function as the CLI; it does not require a database. The template remains unchanged; Task H adds date filtering without changing baseline comparison behavior.

Sources: `section4/src/harness_orchestrator/pilot_metrics.py` (`MIN_N`, `compare`, `collect`, `main`), `docs/briefs/pilot-baseline-template.json`, and the release plan's Baseline section. Manual timing/selection/acceptance rules above are facilitator recommendations, not features enforced by the software.

## Claim/source appendix (rechecked 2026-10-18)

| Claim | File:line checked | Result |
|---|---|---|
| Baseline fields and task structure | `docs/briefs/pilot-baseline-template.json:2` | Confirmed; example structure, not enforced schema |
| Five valid durations per side; descriptive verdict; zero median handling | `section4/src/harness_orchestrator/pilot_metrics.py:40,43` | Confirmed |
| Acceptance is truthiness across all baseline tasks; pilot acceptance counts distinct finished runs with an approval audit row | `section4/src/harness_orchestrator/pilot_metrics.py:47,113,126` | Confirmed; not unique human acceptances |
| Run clock and negative-duration exclusion | `section4/src/harness_orchestrator/pilot_metrics.py:74,81,116`; `section4/src/harness_orchestrator/runs_index.py:77` | Confirmed |
| CLI loads baseline without validating metadata/types | `section4/src/harness_orchestrator/pilot_metrics.py:145` | Confirmed |
| Selection/timing/recording-sheet rules | Manual facilitator protocol above | Recommendations, not software guarantees |
| Fictional example calculations | `docs/briefs/evidence/pilot-prep/baseline-example-FICTIONAL.json:1`; `section4/src/harness_orchestrator/pilot_metrics.py:43` | Rechecked with actual compare function; no measured baseline |
| Creation-window flags and all-history default | `section4/src/harness_orchestrator/pilot_metrics.py:81,145` | Confirmed by Task H tests; baseline behavior preserved |
