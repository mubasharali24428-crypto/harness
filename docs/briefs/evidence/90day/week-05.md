# Week 05 — engineering delivery and independent acceptance

Opened 2026-11-12. Base checkout: `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5`,
branch `w28-s1-delegate`, with uncommitted changes. **Not a clean CI-certified release.**
The user assigned test execution to another agent. The build agent ran only a syntax
compile of changed files — no pytest, `make gate`, real-PG probe, paid model run,
security drill or live pilot was executed in this pass.

## 1. Scope and states

| Work | Role | State | Evidence / limitation |
|---|---|---|---|
| P-09 both partners live, supervised | R (owner) | **Not started — owner-gated** | Requires ≥2 signed design-partner SOWs, consent, support contact and a live supervised pilot. No signed partners exist; engineering cannot recruit, sign or run a partner pilot. |
| P-10 fix top-3 friction items from W5 logs | S+B | **Blocked — no W5 logs** | The plan says "fix **only** the top-3 friction items from W5 logs. No speculative features." There are no W5 logs because no live pilot has run. Inventing fixes would violate the plan and the evidence rule. |
| Measurement substrate (P-09/P-12 capture) | S+B | **Verified-existing (code read, not executed)** | The instrumentation to capture friction already ships — see §3. Read at `7c1acf4`; not run against a live team here. |

**Week 5 build-side engineering is complete to the extent it can be without a pilot.**
P-10 is deliberately left Blocked, not fabricated. The honest next step is the owner
running a real supervised pilot (P-09), which generates the logs P-10 consumes.

## 2. Why P-10 is Blocked, not "done"

P-10 is evidence-gated by design: it fixes the *top-3 friction items observed in W5
logs*, and forbids speculative features. The dependency chain in the plan
(§7) is `P-05 SOWs → P-07 onboard → P-09…P-12 measured pilot`. P-05 (two signed SOWs)
and P-09 (live supervised pilot) are owner/sales tasks that have not happened. With no
partner runs, there are no interventions, denials or friction items to rank. Producing
"3 fixes with before/after evidence" now would mean inventing both the friction and the
evidence. That is exactly the failure mode the evidence discipline exists to prevent, so
this pass does not do it.

## 3. Measurement substrate that already exists (read at 7c1acf4)

The pilot does not need new instrumentation to be measurable; these shipped modules
already capture what P-09/P-10/P-12 consume:

- `section4/src/harness_orchestrator/pilot_metrics.py`
  - `collect()` (`:147`) derives per-team counts from product tables only: runs
    created/finished/accepted/stopped, `elapsedSeconds` median/max, `redirects`
    (`:171`, `run_collab_events` kinds `redirect.%` — an intervention proxy),
    `approvals.byState` and `decisionLatencySeconds` (`:204`, `high_impact_approvals`),
    and `cancelConfirmed`. Acceptance is an `audit_log` `review.approved` proxy, not
    independent confirmation a human accepted the task (stated in the module docstring).
  - `compare()` (`:109`) compares against a hand-measured baseline; `MIN_N = 5` (`:43`)
    means below 5 tasks on either side it reports "insufficient data; no claim". It makes
    no productivity/ROI claim by itself.
- `section4/src/harness_orchestrator/review_summary.py` — `build_review_summary` records
  per-run test runs, failures and explicit `uncertainty` codes (`NO_TESTS_RUN`,
  `TEST_RESULT_UNKNOWN`, `EDITED_AFTER_LAST_TEST`); unknowns are never defaulted to a
  favourable value. `verification_evidence` (`:167`, added in W6/I-09) derives
  green / red→green from the same recorded tool calls; `green` requires every latest
  command to pass (defect-fix 1 — a single red command blocks it).
- `agent_tool_calls` (migration `section1/migrations/0044_agents.sql:95`) — durable record
  of every engine tool call (tool, target, outcome `ok/error/denied/unavailable`,
  `reported_by`, exit status in summary). Denials/refusals are observable here.
- `section4/src/harness_orchestrator/loop_guard.py` — repeated-action / repeated-denial
  stop driven by `COUNT(*) FROM agent_tool_calls` (W27 lineage).
- `cost_ledger` (`cost_usd NUMERIC(12,6)`) — cost/task actuals, joined through the run's
  project for team scoping (see `artifacts.py:1304`).

These are read evidence that the substrate exists. They are **not** a claim that any
pilot has been measured; no team data was queried in this pass.

## 4. What the owner must supply to unblock Week 5

1. Two signed design-partner SOWs + consent + named support contact (P-05, still open).
2. A live supervised pilot: both partners running, daily intervention log, weekly review
   call, target ≥10 real tasks/week/team (P-09).
3. The resulting W5 logs (interventions, denials, cost/task). Only then can P-10 rank the
   top-3 friction items and fix them with before/after evidence at a revision.

## 5. Week-5 measurement handoff — separate test agent / owner

Run only against a real pilot team's data (or a disposable scratch team), never customer
data without consent. From the repo root with section4's venv and `HARNESS_DATABASE_URL`
set (do not print the DSN):

```sh
./section4/.venv/bin/python -m harness_orchestrator.pilot_metrics TEAM_ID \
  --since 2026-XX-XX --until 2026-XX-XX
# Optional hand-measured baseline (template: docs/briefs/pilot-baseline-template.json):
./section4/.venv/bin/python -m harness_orchestrator.pilot_metrics TEAM_ID \
  --baseline baseline.json --since 2026-XX-XX --until 2026-XX-XX
```

Record the raw JSON: runs created/finished/accepted/stopped, elapsedSeconds, redirects
(interventions), approvals + decisionLatencySeconds, cancelConfirmed, and the
baselineComparison `claimable` flag. Below MIN_N the comparison must be reported as
insufficient — do not turn it into a speed-up claim. Capture cost/task from `cost_ledger`
for the same runs. Preserve the team identity and consent basis.

## 6. Owner gates that engineering cannot complete

- Signed partner SOWs, consent and support contacts (P-05).
- Running the live supervised pilot and producing W5 logs (P-09).
- Ranking and approving the top-3 friction fixes once logs exist (P-10 input).
- Independent test acceptance and release go/no-go.

## 7. Defect-fix pass test results (2026-11-12)

P-10 itself adds no tests (it is Blocked on pilot logs). The defect-fix pass did run the
section4 focused suite that exercises the `verification_evidence` substrate cited in §3,
from section4's own directory (host darwin arm64, Python 3.13, source `7c1acf4` working tree):

```text
$ cd section4 && .venv/bin/python -m pytest tests/unit/test_review_summary.py tests/test_done_signal.py -q -rxXs
81 passed, 11 warnings in 1.17s
EXIT_CODE=0
```

`pilot_metrics.collect/compare` were read, not executed against a live team (no pilot data
exists). The full results for the W5/W6 defect-fix pass are in `week-06.md` §8.

**Open defect (scripts/ops):** the `1 xfailed` in that suite is
`test_main_should_exit_nonzero_when_docker_ps_fails` (`scripts/ops/test_stack_tags_exit.py`,
`xfail(strict=True)`) — `stack_tags.main()` exits 0 when `docker ps` fails, but the test asserts
it should exit non-zero. Recorded as an OPEN defect, not an expected/benign skip; the zero exit
is documented by design in `stack_tags.py`, so the 2026-11-13 pass left it unchanged
(`week-06.md` §9, item 4).

No commits, pushes, partner messages, customer-data uploads, signatures or pilot runs
were made in this pass. P-10 remains Blocked on real pilot evidence.