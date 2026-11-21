# hy4 hand-back — A3

Shorter alternative: **no**, not without dropping required behaviour. The diff is
~146 changed lines plus 4 new files (~300 lines) because acceptance asks for five
distinct vitest cases, a confirm-gated editor, a read-only GET + single PUT
container, and two independently-nullable cost fields. The pure helpers were
split into `src/lib/scopeRequest.ts` and the editor is hook-free specifically so
the node-env suite (no jsdom) can drive the confirm gate; collapsing those into
one hook-based component would make "submits nothing until confirmed" untestable
here. No styling beyond the existing `@/lib/styles` tokens was added.

Task: A3 — Front end: scope request and the two costs
Status: PARTIAL — code and all new tests done; two of the four required checks
  are blocked by the environment (see "What is NOT verified")
Assumptions: the run detail/Overview tab is rendered under `section7/app/` (not
  `src/`), the event stream is the only source of `scopeRequest`, the existing
  intent PUT client is reusable, and the TS mirror needs no regeneration — all
  verified by reading HEAD `f5b298a`.

## Where the Overview tab lives (answer to the prompt's note)

`section7/app/projects/[projectId]/runs/[runId]/page.tsx:56` defaults the tab to
`"overview"`; `:191` opens `<RunTabPanel id="overview">`; `:203-204` renders
`<RunDetailAggregate>` from
`section7/app/projects/[projectId]/runs/[runId]/runDetailAggregate.tsx`.
The Overview content is under `section7/app/`, **not** `section7/src/`.
The scope-request activity lives in the `working` tab's `ToolActivity`
(`section7/src/components/ToolActivity.tsx`), because `scopeRequest` only
arrives on the WebSocket `tool_result` event stream
(`service.py:490-496`, `loop_guard.py:68-82`); `service.py:3135-3148`
`_step_wire` forwards only `reason`, so the aggregate's `steps[]` do not carry it.

**Conflict with STATUS, recorded rather than forced:** `STATUS.md:974`/`:978`
say no intent editor exists in section7. At HEAD there already is one —
`intentSection.tsx` (`IntentForm`, `RunIntentSection`) and
`controlApi.ts:109-118` (`IntentView`, `getIntent`, `putIntent`). This task reuses
that PUT client and does not modify it.

## Files changed

NEW:
* `section7/src/lib/scopeRequest.ts` — pure `scopeEditorPathsText`,
  `scopeEditorPaths`, `submitScopeEdit` (the only write path, `put` injectable).
* `section7/src/components/ScopeRequestEditor.tsx` — hook-free, form-free
  presentational editor; warning text; `Save scope` is the only `onConfirm`.
* `section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx` —
  container: GET intent on open, prefill current scope + requested path, PUT only
  on confirm, closes on success, shows errors. No retry, no model change.
* `section7/src/components/ScopeRequestEditor.test.tsx` — 6 vitest cases.

EDITED:
* `section7/src/lib/deriveView.ts` — `ScopeRequestView`, `ToolCallView.scopeRequest`,
  defensive `parseScopeRequest` on `tool_result`.
* `section7/src/components/ToolActivity.tsx` — `needs scope: <path>` prompt +
  `Edit scope` button + optional `onEditScope`.
* `section7/src/components/WorkingArea.tsx` — forwards `onScopeRequest`.
* `section7/app/projects/[projectId]/runs/[runId]/page.tsx` — owns `scopePath`
  state, renders the container in the `working` panel.
* `section7/app/runs/runsApi.ts` — `estimatedCostUsd: number | null` on
  `RunAggregateDto` + light shape guard in `getRunAggregate`.
* `.../runDetailAggregate.tsx` — two cost lines; `not metered yet` /
  `no plan estimate` words for null, `formatCostUsdExact` for numbers.
* Tests: `runDetailPage.test.tsx` (fixture + 4 cost cases), `runAggregate.test.ts`
  (2 mapping cases), `toolActivity.test.tsx`, `deriveView.test.ts`,
  `userStatus.test.tsx` (new required DTO field / source-pin assertion).

No backend code was changed; no contract regeneration was needed
(`payload` is `Record<string, unknown>`, and `estimatedCostUsd` lives on the
hand-written `RunAggregateDto`).

## Commands run (verbatim results)

Resolved the paused session's stop condition first:

```
$ cd "/Users/user/untitled folder 2/harness" && rm -f gtv_sofc && echo "removed_rc=$?"
removed_rc=0
```

`gtv_sofc` was 4 bytes `b"blah"`, mode 0600, created 11:22 by Python
`tempfile`'s probe during the read-only `generate.py --check`; I identified it,
removed it, and did not hide it from the record. `git status --short` after
removal matched the recorded tree (only my task files + expected `docs/yc/`).

```
$ npx --prefix section7 tsc --noEmit -p section7/tsconfig.json
TSC_RC=0
```
(first run: `TSC_RC=2`, one error — `toolActivity.test.tsx:9` needed the new
`scopeRequest: null` fixture field; fixed, then rc=0)

```
$ cd section7 && npx vitest run --minWorkers=1 --maxWorkers=2
VITEST_LOWWORKERS2_RC=0
 Test Files  99 passed (99)
      Tests  1075 passed (1075)
   Duration  531.87s
```

```
$ cd "/Users/user/untitled folder 2/harness" && python3 contracts/ts/generate.py --check
TS_MIRROR_RC=0
TS mirror up to date.

$ make static-gates
STATIC_GATES_RC=0
sh -n section7/scripts/ci_route_module_guard.sh
GUARD[routes] OK: 26 route module(s) checked — every app/**/page.tsx is default-export-only.
sh -n section7/scripts/ci_route_module_guard.sh
GUARD[isolation] OK: 282 src file(s) across 10 section(s) —
                 no cross-section tree/package/sys.path/star-reexport edges.
sh -n deploy/entrypoint.sh
docker compose -f deploy/docker-compose.yml config -q
python3 contracts/ts/generate.py --check
TS mirror up to date.
section4/.venv/bin/python scripts/spec_drift.py
orchestrator specs and UI calls match the router.
python3 scripts/eval/run_eval.py --check
{"ok": true, "tasks": 39, "heldOut": 6, "implemented": 39}
python3 scripts/check_env_knobs.py
env knob catalogue complete: 60 runtime reads, all in .env.example
```

```
$ make frontend     (run to completion as a managed background task)
MAKE_FRONTEND_BG_RC=1
 Test Files  2 failed | 97 passed (99)
      Tests  2 failed | 1073 passed (1075)
   × visible surface pins > SSR skeleton stays idle when nothing is persisted (honest empty state) 5002ms
     → Test timed out in 5000ms.
   × login page accessible names (M15-S3) > legacy mode: email input resolves via <label for> (getByLabelText parity) 5102ms
     → Test timed out in 5000ms.
make: *** [frontend-vitest] Error 1
```
Both are pre-existing load-sensitive 5 s timeouts in files I did not touch
(login page / meeting restore, both use dynamic `import()`). Proven unrelated:
```
$ cd section7 && npx vitest run tests/unit/a11yAccessibleNames.test.tsx
A11Y_ALONE_RC=0   (9 tests passed; that test took 1055ms)

$ cd section7 && npx vitest run tests/unit/meetingRestore.test.tsx ... (7-file focused run)
 ✓ tests/unit/meetingRestore.test.tsx (13 tests) 7243ms   [SSR skeleton 4406ms]
```
Two earlier foreground `make frontend`/`npx vitest run` attempts were killed by
the environment (exit 137 SIGTERM, no summary emitted) before completion.

```
$ cd section7 && npm run build
NEXT_BUILD_RC=1
   ▲ Next.js 15.5.23
> Build error occurred
Error: Broker request outcome unknown: write EPIPE
    at createBrokerOutcomeUnknownError (.../shim/node-brokered-fs-shim.cjs:282:26)
    at async Object.wrappedPromisesMkdir [as mkdir] (...:909:9) {
  code: 'CODEBUDDY_BROKER_OUTCOME_UNKNOWN',
  [cause]: Error: write EPIPE ... }
```
Retried once, same `EPIPE`; the retry also produced a sandbox permission
request (delete of `section7/.next/server/app/settings/team/page_client-reference-manifest.js`)
which the user **rejected**. I did not retry again or work around the shim.

Negative control (real, not just by reading): temporarily replaced
`call.scopeRequest = parseScopeRequest(payload);` in `deriveView.ts` with a
comment, then:
```
$ cd section7 && npx vitest run src/components/ScopeRequestEditor.test.tsx
NEGATIVE_CONTROL_RC=1
 × tool_result scopeRequest > renders the 'needs scope: <path>' prompt and an Edit scope button
   → expected '<section style="background:var(--pane…' to contain 'data-testid="scope-request"'
 Test Files  1 failed (1)
      Tests  1 failed | 5 passed (6)
```
Restored the line; post-restore focused run:
```
$ cd section7 && npx vitest run <5 affected files>
 Test Files  5 passed (5)
      Tests  74 passed (74)
```

## Proof the new tests fail without the change

* **Real run** (above): removing the `scopeRequest` assignment makes the prompt
  test fail. Restoring it makes all 6 pass.
* By reading HEAD (the prompt permits this): `ToolCallView` had no
  `scopeRequest` field and `ToolActivity` had no prompt/button, so the markup
  and `toEqual({path, grants})` assertions could not pass; `getRunAggregate`
  returned no `estimatedCostUsd`, so `toBe(13.75)` would be `undefined`; and
  `runDetailAggregate.tsx:83-91` rendered **no** cost line at all on null, so
  `toContain("not metered yet")` / `toContain("no plan estimate")` would fail.
* The confirm-gate test imports two NEW modules (`@/lib/scopeRequest`,
  `ScopeRequestEditor`); without the change the file would not even resolve.

## What is NOT verified

* `cd section7 && npm run build` — **NEEDS-REVIEWER-RUN**. Exact command and
  expected result: `cd "/Users/user/untitled folder 2/harness/section7" && npm run build`
  → `next build` completes with `Compiled successfully` / route table, rc=0.
  Blocked by the WorkBuddy file-broker `EPIPE` on `mkdir` and a rejected
  `.next` delete permission request. The `next` compile itself never got far
  enough to report a code error; `tsc --noEmit` (the build's type half) is rc=0.
* `make frontend` at default worker parallelism — rc=1 on two unrelated 5 s
  timeouts. The identical suite with `--minWorkers=1 --maxWorkers=2` is rc=0
  (99 files / 1075 tests). A reviewer on a less loaded machine should see rc=0.
* No browser/visual check of the new UI (no jsdom here, and no deploy allowed).

## Unrelated findings (one line each)

* `STATUS.md:974`/`:978` claim no intent editor exists in section7; `intentSection.tsx` + `controlApi.ts` prove otherwise at HEAD.
* `tests/unit/meetingRestore.test.tsx` and `tests/unit/a11yAccessibleNames.test.tsx` have 5 s timeouts that expire under full-suite parallel load on this host; both pass in isolation — left untouched.
* The WorkBuddy Node file-broker shim can drop its IPC mid-`next build` with `write EPIPE`; left untouched.
* `python3` sitecustomize can leave a 4-byte `tempfile` probe in the repo root when no temp dir is usable; the one from A3 reconnaissance was removed and recorded above.

## Risks the reviewer should check first

* Re-run `npm run build` on a host/permission setting where `.next` cleanup is allowed; that is the one unverified acceptance check.
* Confirm the overview/working tab split matches intent: the two costs are on **overview** (`RunDetailAggregate`), the scope prompt and editor are in **working** (`ToolActivity`), because only the event stream carries `scopeRequest`.
* The container closes the editor after a 202 `queued` response the same as after an applied change; a person may want to see "queued" surfaced. It currently shows no such note.
* `estimatedCostUsd` is `number | null` end-to-end; a server-sent `0` renders `$0.000000`, not the null text (covered by a test).
* `make lint` was not re-run for A3 (no Python files changed here); `RUFF_NO_CACHE=true make lint` was rc=0 for A2 on this host.
