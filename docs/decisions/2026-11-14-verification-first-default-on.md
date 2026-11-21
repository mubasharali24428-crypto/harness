# Decision: verification-first done-signals on by default

**Date:** 2026-11-14 · **Status:** ACCEPTED by the decision owner (in session), 2026-11-14.
**Resolves:** open question from `docs/briefs/evidence/glm/probe-glm-5.3-flash-handback.md` (mode 2); FUTURE PLAN 2.0 item 4.7 (I-09, S-03 rule).

## Decision

`HARNESS_VERIFICATION_FIRST` defaults to **on**. A done-signal is accepted only when the run's own recorded engine tool calls show a test going green (`review_summary.verification_evidence`). An operator can still turn it off explicitly with `HARNESS_VERIFICATION_FIRST=false`.

## Why

With the default off, the only evidence a done-signal needs is a non-empty diff (`section4/src/harness_orchestrator/signals.py:137`), and the reviewer's test verdict defaults to `"passed"` (`reviewer.py:32`). A run can reach DONE without any test having run. That contradicts plan item 4.7: "done requires test evidence from the executor, never a model claim".

## Consequences

- The golden conformance flow (`section4/tests/conformance/test_orchestrator_conformance.py`) records no live tests. Those tests must pin `HARNESS_VERIFICATION_FIRST=false` explicitly. The golden fixtures themselves do not change.
- `test_verification_first_default_off_preserves_flow` (`section4/tests/test_done_signal.py:543`) changes meaning: it becomes an explicit-off test, and a new test asserts the default is on.
- The owner's demo stack and any recorded demo replay that ends in a done-signal without recorded tests needs `HARNESS_VERIFICATION_FIRST=false` in its env at the next deploy, or its done-signal is refused. This is a deploy step for the reviewer, not a code change.
- `.env.example` text changes to describe the new default.
- With the setting on, a recorded result of `unknown` must not let the reviewer pass (today only `failed` fails, `reviewer.py:49-52`).

## Not decided here

Risk-tiered auto-approval stays off (D-U4). Model routing is unchanged.
