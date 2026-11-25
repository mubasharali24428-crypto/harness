# Task 37 — Denial-learning UI: read-only rule offer on the denial stop (G-I08-1)

- Date (UTC): 2026-11-25
- Base: `66847e9` (Task 32) on `w28-s2-sprints1-4` (cloud, no Docker)
- Status: **Verified (unit + static render)**. Not browser-run, not live-stack-run.

## The gap

I-08 (P-23, week 12) made a repeated-denial stop compute its lesson as a project rule and Task 14
(Sprint 2) stored it in `case_memory` (0080). The run page never showed it: the `engine_failed` step
reason is clamped to 200 characters server-side, so the "Suggested project rule — LEARNED FROM
DENIALS …" tail was cut, and `_suggested_rule` lived only on the in-memory run record.

## What changed

| Piece | Change |
|---|---|
| `GET /api/v1/orchestrator/runs/{runId}/learned-rule` (section4 `api.py`) | new **read-only** route, `RunReader` (same team-ownership/no-oracle 404 as every run read; viewers may read). Returns `{offer: {failureClass, paths, suggestedRule, recordedAt, status: "proposed"} \| null, similarCases}` |
| `collab.learned_rule_for_run` | reads the run's newest `case_memory` row with `team_id` filtered again on top of authz; `similarCases` reuses `similar_cases_for_run`. Pre-0080 schema / DB error → `{offer: null, similarCases: []}`, never an error |
| Specs + SDK | path added to `section4/openapi.json` and the sdk-gen orchestrator spec (only that hunk); Python/TS SDK + TS dist regenerated |
| `section7/src/components/workspace/LearnedRuleOffer.tsx` | `stoppedOnDenials(steps)` gates the fetch (only a `engine_failed` step whose reason starts `denials_repeated`); `LearnedRuleView` shows the rule in a focusable read-only `<pre>`, the paths, "**not saved** … nothing changes unless a person adds it to the project rules", **Copy rule text**, a link to `/settings/project`, and up to 3 similar past denials |
| Run page | `LearnedRuleOffer` sits under the inline approvals in the centre column; `.ws-learned-rule` wraps long text (no sideways scroll at 375 px) |

**CD-02 held:** no new write path. The component makes one GET; the only way a rule is saved is the
person's own edit + Save on `/settings/project` (existing `PUT /projects/{id}/rules`). No auto-apply,
no "Save" / "Apply" button on the offer (asserted in the static-render test).

## Verification

```
section4: pytest tests/unit/test_learned_rule_api.py                         → 5 passed
section4: pytest tests/ -k "policy or route or authz or openapi or spec or similar
          or denial or case_memory or learned"                               → 235 passed, 1 skipped
section4: ruff check src tests                                               → All checks passed!
section7: npx vitest run src/components/workspace/LearnedRuleOffer.test.tsx  → 5 passed
section7: npx vitest run                                                     → 122 files / 1243 tests passed
section7: npx tsc --noEmit                                                   → clean
scripts/spec_drift.py                                                        → orchestrator specs and UI calls match the router.
make check-ts-dist                                                           → 26 tracked files match a fresh build
make guard-route-law                                                         → routes + isolation OK
```

Backend tests pin: offer read-only (case count unchanged), `status: "proposed"`, viewer can read,
foreign team 404 `NOT_FOUND`, a mis-attributed other-team row never answers, pre-0080 schema → no
lesson, other team's similar case never leaks.

## Not done / honest limits

- No Playwright run of the offer (mocked or live). A live proof needs a real repeated-denial stop on
  the compose stack — same Mac lane as Task 35 (two denials → `similarCases`).
- Repeat-denial count → 0 on pilot tasks: still **not measured** (owner/pilot-gated, week-12 row).
