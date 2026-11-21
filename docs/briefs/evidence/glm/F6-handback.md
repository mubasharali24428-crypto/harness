# Hand-back: F6
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: f7af86d
Status: PARTIAL
Token use (estimate): 140000

## Files changed (uncommitted)

- `section7/src/components/PhoneTriage.tsx` (new) — phone-width inbox. Lists ONLY pending plan approvals. Approve and Deny are the only actions.
- `section7/src/components/PhoneTriage.test.tsx` (new) — 12 tests for the pending set, the two controls, and wire parity with the desktop clients.
- `docs/briefs/evidence/glm/F6-handback.md` (new) — this file.

No other file was edited. Route wiring was not changed (needs reviewer approval).

## Checks run

`cd section7 && npx vitest run` (last 15 lines):

```
 ❯ tests/unit/uiPrimitives.test.tsx:223:23
    221|   it("token palette matches globals.css :root exactly", async () => {
    222|     const { colors } = await import("@/components/tokens");
    223|     expect(colors.bg).toBe("#0a0b0e");
    224|     expect(colors.panel).toBe("#12161e");
    225|     expect(colors.panel2).toBe("#191e28");
    ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[2/2]⎯
 Test Files  2 failed | 105 passed (107)
      Tests  1 failed | 1161 passed (1162)
   Start at  10:28:57
   Duration  4.40s
   Transform 3.60s
   Collect  10.94s
   Tests    3.77s
   Setup    0ms
   Prepare  8.15s
```

`src/components/PhoneTriage.test.tsx` alone: **12 passed**.

Isolated `tsc --noEmit` on the new files plus their imports (`PhoneTriage.tsx`, `PhoneTriage.test.tsx`, `States.tsx`, `tokens.ts`, `ui/Button.tsx`, `lib/auth.ts`, `lib/format.ts`, `lib/triage.ts`, `app/runs/runsApi.ts`, `app/dashboard/planGateApi.ts`): **exit 0**.

## Checks not run

- Repo `npx tsc --noEmit` **cannot exit 0**. Two pre-existing failures, neither in F6 files:
  1. `src/components/AppShell.tsx` is already broken in the working tree (unclosed JSX / stray `}` at `AppShell.tsx:90`). `tsc` and `vitest` both fail to transform it. `AppShell.test.tsx` collects 0 tests. Not touched.
  2. `tests/unit/uiPrimitives.test.tsx:223` expects `tokens.bg === "#0a0b0e"`; working-tree `tokens.ts` is `#0c0d0f`. Pre-existing palette drift. Not touched.
- Pass-count delta vs. a clean baseline is therefore not a single number. Last good published count was 105 files / 1134 tests (kimi handback 2026-11-13). This tree now: 107 files / 1162 tests, 1 pre-existing fail + 1 pre-existing transform fail. F6 adds 1 file and 12 passing tests.
- 375px Playwright (`e2e/phone.e2e.ts`) — **NEEDS-REVIEWER-RUN**. No stack started.
- No docker, no live approve/deny, no model calls.

## Decisions I made (reviewer may overrule)

1. **Scope of the list.** Only `awaiting_plan_approval`. Blocked, `pr_opened` and working runs stay off the screen (U5: "approve or deny only, nothing else").
2. **Already-approved plan.** Desktop `triageRuns` still counts it as needs-you (`Start the approved run`). The phone screen shows it as Start text, not a third button. Reviewer may prefer to hide it.
3. **planId.** `RunDto` has no `planId`. The component reads it as an extra field on the list item. If the orchestrator omits it, approve/deny are disabled and the item is honest about it. A follow-up could add `planId` to `RunItem` (not done — that file is outside F6).
4. **Wire path.** `approvePlan` / `rejectPlan` from `app/dashboard/planGateApi.ts`, the same clients `PlanGateCard` uses (`POST {ORCH}/api/v1/orchestrator/plans/{id}/approve` → 200 `{runId}`, `POST .../reject` → 202). Tests pin URL, method, bearer and `{}` body against the desktop calls.
5. **List source.** `listRuns({ limit: 100 }, token)` + `triageRuns`, same as `HomeTriage.tsx:100`.
6. **`items` prop.** `renderToStaticMarkup` never runs effects. Tests pass a prebuilt list. Production callers omit it and the component loads itself.
7. **No route.** Component is unused until the reviewer wires it.
8. **Notice.** Status/error lines are token-colored `<p>`s, not `ui/Notice`, because working-tree `ui/Notice.tsx:66` already fails `tsc` on `space.block` (pre-existing).

## Open questions

- Wire a phone route (`/phone` or a shell-less home variant)? Needs reviewer approval.
- Should the server include `planId` (and maybe `goal`) on `GET /runs` items so a phone screen can act without a second round-trip?
- Hide or keep the already-approved "Start" item?

## Unrelated issues seen

- `section7/src/components/AppShell.tsx` — working-tree edit removed the titlebar block and left broken JSX (`AppShell.tsx:90`, EOF at 238). Breaks `tsc` and `AppShell.test.tsx`.
- `section7/src/components/tokens.ts` vs `tests/unit/uiPrimitives.test.tsx:223` — `bg` is `#0c0d0f`, test wants `#0a0b0e`. `globals.css` was also edited in this tree.
- `section7/src/components/ui/Notice.tsx:66` — `space.block` does not exist on the current `tokens.ts` space scale.

## Known limits of this change

- Not mounted on any route.
- Cannot approve/deny if `GET /runs` omits `planId`.
- Already-approved plans are not deniable from this screen (correct: the desktop gate is also closed).
- No 375px screenshot; layout is a single 480px-max column of cards, no tables, no sidebar.
- Tests cannot click a button (no jsdom). Click handlers call `decidePlan`, and `decidePlan` is pinned to the desktop fetch. A Playwright pass would still be the real click proof.
