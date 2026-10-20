# Parts 12 and 24: plain-language errors and run states

Directive 24.010 ("make errors and cancellation state understandable to non-engineers") and 12.006 / 12.010 ("actionable errors", "clear status language") had two
visible gaps: the shared-run panel printed the server's engineer-style refusal message, and the run header printed the raw state (`awaiting_plan_approval`).

## Changes (section7)

* `collabModel.ts`: `PLAIN_ERRORS` (one sentence for each of the 26 codes in `docs/collaboration-contract.md` section 9) and `plainError(e)`.
  `sharedRunPanel.tsx` uses it at both places a failure becomes text (refresh and mutation); unknown codes still show the server message; stale-view and
  rate-limit texts (`mutationError`) are unchanged.
* `runsApi.ts`: `stateLabel(state)` for every `AgentState` plus `completed|stopped|cancelled|failed`; unknown states are humanized, never dropped.
  `runDetailAggregate.tsx` shows the label in the header pill and keeps the raw value in its `title`.

## Tests

* `plainErrors.test.ts` (4): reads the contract's error index; the table has exactly those codes; no text contains a code or underscore; ApiError mapping;
  the panel calls `plainError` at both sites.
* `tests/unit/stateLabel.test.ts` (3): reads `AgentState` from `state_machine.py`; every state has a label; waiting states say who must act;
  stopped/cancelled/unknown handled.
* Full section7: `tsc --noEmit` clean; vitest 69 files, 824 tests passed.

## Honest limits

* No real-browser, screen-reader or visual check was made; these are unit tests over pure functions and a source-wiring check.
* The runs index page (`app/runs/page.tsx`) still prints raw states; only the run header changed.
* `done` cannot distinguish "finished" from "stopped" when the orchestrator reports a stop as `done`; the label says "Finished". The `stopped` label is used
  only when the state itself is `stopped`.
* English only (part 56).
