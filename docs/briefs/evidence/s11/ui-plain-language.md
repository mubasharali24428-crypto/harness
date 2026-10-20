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
* `tests/unit/stateLabel.test.ts` (3): takes the states from section 7's own `RUN_STATUSES` mirror of `AgentState` (it must not read section 4's source:
  the isolation law); every state has a label; waiting states say who must act; stopped/cancelled/unknown handled.
* Full section7 after the later browser-check fixes: `tsc --noEmit` clean; vitest 72 files, 831 tests passed (see `browser-check.md`).

## Honest limits

* These tests are unit tests over pure functions and a source-wiring check. A later pass drove the run page in Chromium (`browser-check.md`) with
  synthetic data; it did not read these sentences aloud. No screen reader was used and no visual check was made.
* The runs index page (`app/runs/page.tsx`) still prints raw states; only the run header changed.
* `done` cannot distinguish "finished" from "stopped" when the orchestrator reports a stop as `done`; the label says "Finished". The `stopped` label is used
  only when the state itself is `stopped`.
* English only (part 56).
