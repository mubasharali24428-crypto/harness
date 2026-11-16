# Open items from the leftovers report — worked through 2026-11-15

Branch `w28-s1-delegate`, nothing pushed. Each item is from the "Not done, not verified" list of
`u-leftovers-report-2026-11-15.md`.

## 1. Phone triage with real pending plans — done, and it was broken

Run on `:3011` with two real proposed plans (`e2e/triage.e2e.ts`). The first run showed Approve
and Deny **disabled on every item**: the runs list carries neither the plan id nor the goal, so on
a real stack nothing could be decided and every item read "Plan awaiting your decision". The
component's 12 unit tests passed because they hand it a plan id.

Fixed: the page reads the plan id and goal for pending runs (the same two calls the desktop makes).
Now passes on `:3011`: approve one plan, deny the other, no sideways scroll at 375 px.

Observed, not changed: a denied plan's run stays in the list with its buttons off, because the
server still reports the run as awaiting a plan.

## 2. `e2e/multiplayer.e2e.ts` — rewritten and passing

Run with `deploy/pilot/run_smoke.py` (own scratch database, stand-in worker, no model spend).
**Passes twice in a row, ~32 s, zero console or network faults.** The spec now signs in to the new
home, proposes through the composer, approves and starts from the plan pin, reads the shared-run
panel in the Advanced pane, checks "Done, continue" in the Terminal pane, and stops with `/stop`.

Product defects it exposed, fixed:
- **A closed model picker and a closed "Start with a screenshot" card still called the model
  service** on every page load. Both now load on first open.
- **Theme hydration warning** on every page in development (`data-theme` differs between server
  and client by design); the root element now declares that.

Observed, not investigated: with a 15 s wait inserted before the handoff step, the *second* run
came up "Blocked: awaiting leader approval" three times out of three; without the wait it passes.
That points at a timing dependence in the orchestrator (engine start versus a pending approval),
not in these pages. The presence check sits at the end of the spec for that reason.

## 3. Screen-reader journey — automated substitute only

`section7/scripts/sr-journey.mjs` walks sign-in → demo → propose → approve with the keyboard and
records what the browser gives assistive technology (focus target, role, name, live regions):
`sr-journey-2026-11-15.md`. **It is not a screen-reader recording; that still needs a person.**

Its first run reported 7 findings, all fixed, and the rerun reports 0:
- focus fell to the page body after every in-app page change → it now moves to the main region;
- after Approve plan, focus fell to the body and nothing announced the approval → focus moves to
  Start run and a status line says "Plan approved. Start the run when you are ready."

## 4. Presence (U6) — header slice only

The presence backend, roster and mute already existed in the Advanced pane. Added: initials of the
people on the run now, in the run header; clicking opens the roster. Checked in the multiplayer
spec with two real people. Not built: anything beyond what the existing presence API offers. The
multiplayer go/no-go (P-D7) is still the owner's.

## 5. Client portal (P-17) — a read-only report, not the portal

Added `/projects/{project}/runs/{run}/report`: plan, recorded facts (approver, tests, risk flags),
cost, trace, diff; no controls. Linked from the Evidence pane. On `:3011` (`e2e/report.e2e.ts`): a
member reads it, axe clean, no sideways scroll at 375 px, and **a person from another team gets
"Run not found"**.

**This is not P-17.** An end-client outside the team has no role to sign in with; that scoped
read-only role is backend work and an owner decision. P-17 acceptance is not met.

## 6. shadcn/ui (D-U1) — not touched

The decision file still records shadcn/ui and it is still not installed. Installing it now would
mean re-skinning finished, verified screens; amending the decision is the owner's call.

## Verification actually run

| Check | Result |
|---|---|
| `npm run typecheck`, `npm test` (section7) | pass, **1201 passed** |
| `deploy/pilot/run_smoke.py` (multiplayer) | **1 passed, twice in a row** |
| `:3011`, frontend rebuilt (`vv90r4`): triage, report, leftovers | **3 of 3** |
| `:3011`: composer, review, workspace (70 s later, under the login rate limit) | **8 of 8** |
| `node scripts/sr-journey.mjs` on `:3011` | 0 findings (7 before the fixes) |

Only the RC frontend was rebuilt. `PhoneTriage.tsx` was changed (see 1).

## 2026-11-16: backend gaps closed (not yet on `:3011`)

| Gap | Change |
|---|---|
| Propose returned no run id | `POST /plans` now returns `runId` and `projectId`. The composer opens the run from the response; the search through runs awaiting approval is deleted. |
| Runs list had no goal or plan id | `GET /runs` items carry `planId` and `goal` (newest plan, one extra query per page). The sidebar and phone triage no longer make one status and one plan request per row. |
| "Took effect" was not live | `GET /runs/{id}/collab-history` returns `deliveredUpTo` (the engine's delivery cursor). The composer polls it every 5 s while direction is waiting and then says "Delivered to the agent." |
| Picker could not say which models have a key | The picker reads the team's keys and marks a provider "team key set". It cannot see platform keys, so it never says "no key". |

Checks run: section4 `pytest` 2392 passed, 60 skipped (3 new assertions); section7 typecheck, 1201 unit
tests and build pass. **Not verified in a browser**: `:3011` needs the orchestrator and frontend
rebuilt. The "Delivered" line also needs a live run, which costs model calls.

Still not built: per-step plan progress (the engine emits no step events) and "since last
iteration" in review (the review service stores one diff per pull request).
