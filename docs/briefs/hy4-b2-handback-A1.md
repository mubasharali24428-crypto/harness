# hy4 batch 2 hand-back — A1

Task: A1 — Front end: a read-only synthesis panel (W28, "reviewable synthesis")  
Status: BLOCKED  
Shorter alternative: **no** — the diff is ~307 lines (28 added in `controlApi.ts`,  
13 in `page.tsx`, 131 new in `synthesisPanel.tsx`, 135 new in  
`synthesisPanel.test.tsx`). The panel is one presentational component plus one  
fetching wrapper; the test file is the bulk. Collapsing it would drop the  
explicit loading/empty/error/null-result/"0" states the task names, or the  
negative-control evidence. The `synthesisPanel.tsx` body could be trimmed by  
replacing the `<table>` with definition lists (about 25 lines saved) but that  
is a restyle, not a smaller behaviour, and A2 is out of scope for restyling.

Assumptions: the synthesis response shape is exactly `delegation.py:171-214`  
(`spentUsd` is the API's decimal string, including a real `"0"`; `result` and  
`runStatus` may be null; `conflicts[].assignmentIds` holds two assignment ids);  
`controlApi.call` / `root` is the sanctioned fetch path; node-env vitest only  
(no jsdom/RTL), so states are proven with `renderToStaticMarkup` and direct  
element-tree/API calls, not DOM events; "render it only when the run has  
delegated children or the request succeeds with children" is read as "reveal  
the panel on the first successful response with children, and keep it visible  
for that run afterwards" — an ordinary run gets no empty panel.

## Why BLOCKED

The batch's STOP CONDITIONS say: "Stop and write BLOCKED: in the relevant  
hand-back if ... the same sandbox permission is rejected once, in which case  
do not retry it or look for a way around it." WORKING RULES say "After each  
task write its hand-back, then continue unless it is BLOCKED." Attempting the  
required real negative control (rule 7: copy the file aside, temporarily  
revert, restore, prove with `cmp`) hit exactly that case:

```
Command: cd "/Users/user/untitled folder 2/harness/section7" && D="app/projects/[projectId]/runs/[runId]" && cp "$D/synthesisPanel.tsx" /private/tmp/hy4-b2-a1/synthesisPanel.fixed.tsx && cp "$D/controlApi.ts" /private/tmp/hy4-b2-a1/controlApi.fixed.ts && cp /private/tmp/hy4-b2-a1/controlApi.orig.ts "$D/controlApi.ts" && echo "SAVED_FIXED_AND_REVERTED_CONTROLAPI" && ls -l /private/tmp/hy4-b2-a1/
Stdout: Brokered host copy overwrite refused by file policy: prompt
Exit Code: 13
Permission request rejected by user:
  delete /Users/user/untitled folder 2/harness/section7/app/projects/[projectId]/runs/[runId]/controlApi.ts (rule: **)
Do NOT retry this command or attempt equivalent alternatives.
```

The first two `cp` calls (saving the FIXED files aside under `/private/tmp`)  
succeeded; the third (overwriting an existing tracked file to stage the  
pre-change state) was denied. I did **not** retry it, did **not** substitute an  
edit/sed/mv workaround, and did **not** look for another way to stage the  
revert. A read-only check afterwards confirms the rejected `cp` did not land:  
`getSynthesis` is still present in `controlApi.ts`, `synthesisPanel.tsx` still  
contains `REVIEW_LINE`, and no stub was written. So A1's implementation and  
its tests are in the tree and green, but **the required negative control was  
not run, and A1 is not accepted**. Tasks A2, A3, B1, B2 and B3 were not  
started.

## Initial `git status --short` (saved in this first hand-back, as required)

```
 M .env.example
 M docs/eval/corpus.json
 M scripts/e2e/m07_daemon_drill.py
 M scripts/e2e/w27_denial_drill.py
 M section4/src/harness_orchestrator/loop_guard.py
 M section4/src/harness_orchestrator/service.py
 M section4/tests/unit/test_lifecycle_recovery.py
 M section4/tests/unit/test_loop_guard.py
 M section4/tests/unit/test_loop_guard_wiring.py
 M section7/app/projects/[projectId]/runs/[runId]/page.tsx
 M section7/app/projects/[projectId]/runs/[runId]/runDetailAggregate.tsx
 M section7/app/runs/runsApi.ts
 M section7/src/components/ToolActivity.tsx
 M section7/src/components/WorkingArea.tsx
 M section7/src/lib/deriveView.ts
 M section7/tests/unit/deriveView.test.ts
 M section7/tests/unit/runAggregate.test.ts
 M section7/tests/unit/runDetailPage.test.tsx
 M section7/tests/unit/toolActivity.test.tsx
 M section7/tests/unit/userStatus.test.tsx
?? docs/briefs/evidence/pilot-prep/m01-native-edit.json
?? docs/briefs/hy4-handback-A1.md
?? docs/briefs/hy4-handback-A2.md
?? docs/briefs/hy4-handback-A3.md
?? docs/briefs/hy4-handback-A4.md
?? docs/briefs/hy4-handback-B1.md
?? docs/briefs/hy4-handback-B2.md
?? docs/briefs/hy4-handback-B3.md
?? docs/briefs/hy4-handback-B4.md
?? docs/briefs/hy4-handback-PROGRESS.md
?? docs/briefs/hy4-handback-SUMMARY.md
?? docs/briefs/hy4-master-prompt-batch2-2026-11-05.md
?? docs/decisions/2026-11-05-intent-permissions.md
?? docs/runbooks/redeploy-worker-and-orchestrator.md
?? docs/yc/
?? scripts/e2e/m01_native_edit_drill.py
?? scripts/ops/
?? section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx
?? section7/src/components/ScopeRequestEditor.test.tsx
?? section7/src/components/ScopeRequestEditor.tsx
?? section7/src/lib/scopeRequest.ts
```

41 entries (20 modified, 21 untracked) at HEAD `f5b298a` on branch  
`w28-s1-delegate`. All are batch 1's known work; none was reverted, reformatted  
or tidied. `docs/yc/` was left alone. No changes I do not recognise appeared.

## Files changed

Edited (both named in A1's "Look at" list; `page.tsx` is a batch-1 file edited  
in place):

- `section7/app/projects/[projectId]/runs/[runId]/controlApi.ts` — added  
  `SynthesisChild`, `SynthesisConflict`, `SynthesisView` and the one-line  
  `getSynthesis = (runId, t) => call<SynthesisView>(\`${root(runId)}/synthesis\`, t)\`.  
  No new fetch layer, no new HTTP route.
- `section7/app/projects/[projectId]/runs/[runId]/page.tsx` — one import and  
  `<SynthesisSection runId={runId} token={token} />` inside the `overview` tab.

New:

- `section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.tsx` —  
  `SynthesisPanel` (pure: one row per child with title, agentName/agentVersion,  
  delegatedBy, scope paths, state, exact `spentUsd`, runStatus, result; a  
  conflicts block naming the two titles and the paths; the fixed line  
  "Review required. Nothing is merged automatically." in every state; explicit  
  loading, error, "no delegated children" and "no result yet" text) and  
  `SynthesisSection` (GETs through `getSynthesis`, polls at 10000 ms — the  
  agent-tree panel's interval — and reveals itself only once children have  
  been seen). No button, form, POST, PUT or any other write.
- `section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx` —  
  8 vitest cases (children + conflicts + exact spend, empty, null result,  
  error, loading, always-present line, no write controls, GET helper).

## Commands run (each with its result, copied not summarised)

```
$ cd "/Users/user/untitled folder 2/harness" && mkdir -p /private/tmp/hy4-b2-a1 && cp "section7/app/projects/[projectId]/runs/[runId]/controlApi.ts" /private/tmp/hy4-b2-a1/controlApi.orig.ts && cp "section7/app/projects/[projectId]/runs/[runId]/page.tsx" /private/tmp/hy4-b2-a1/page.orig.tsx && echo "saved originals:" && ls -l /private/tmp/hy4-b2-a1/
saved originals:
total 24
-rw-r--r--  1 user  staff   8721 2026-11-05 21:09 controlApi.orig.ts
-rw-r--r--  1 user  staff  10207 2026-11-05 21:09 page.orig.tsx
```

```
$ cd "/Users/user/untitled folder 2/harness/section7" && npx vitest run "app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx" --reporter=basic
The CJS build of Vite's Node API is deprecated. See https://vite.dev/guide/troubleshooting.html#vite-cjs-node-api-deprecated for more details.
 RUN  v2.1.9 /Users/user/untitled folder 2/harness/section7
 ✓ app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx (8 tests) 17ms
 Test Files  1 passed (1)
      Tests  8 passed (8)
   Start at  21:10:57
   Duration  17.38s (transform 325ms, setup 0ms, collect 858ms, tests 17ms, environment 0ms, prepare 1.26s)
Exit Code: 0
```

```
$ cd "/Users/user/untitled folder 2/harness/section7" && npx tsc --noEmit
TSC_RC=0
```

```
$ cd "/Users/user/untitled folder 2/harness" && echo "=== git status --short (post-rejection) ===" && git status --short && ... (full output omitted here for length; the 41 known entries plus the 4 A1 files are listed by name in "Files changed")
=== getSynthesis present in controlApi? ===
1
=== panel file intact? ===
2
=== saved copies ===
total 44
-rw-r--r--  1 user  staff  9649 2026-11-05 21:12 controlApi.fixed.ts
-rw-r--r--  1 user  staff  8721 2026-11-05 21:09 controlApi.orig.ts
-rw-r--r--  1 user  staff 10207 2026-11-05 21:09 page.orig.tsx
-rw-r--r--  1 user  staff  5445 2026-11-05 21:12 synthesisPanel.fixed.tsx
```

```
$ cd "/Users/user/untitled folder 2/harness" && git diff --numstat -- "section7/app/projects/[projectId]/runs/[runId]/controlApi.ts" "section7/app/projects/[projectId]/runs/[runId]/page.tsx" && wc -l <two new files>
28  0  section7/app/projects/[projectId]/runs/[runId]/controlApi.ts
12  1  section7/app/projects/[projectId]/runs/[runId]/page.tsx
131 section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.tsx
135 section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx
```

Rejected command and its output are quoted verbatim under "Why BLOCKED".

## Proof the new tests fail without the change

**Not run.** This is the block. The staging step for the rule-7 temporary  
revert was denied by the sandbox (`delete .../controlApi.ts (rule: **)`), and  
the batch forbids retrying it, using an equivalent, or looking for a way  
around it. The 8 new tests pass against the finished component and  
`npx tsc --noEmit` is rc=0, but I make no claim that they are falsifiable:  
that claim needs the negative-control run that did not happen. The saved  
copies for that revert (`controlApi.orig.ts`, `page.orig.tsx`,  
`controlApi.fixed.ts`, `synthesisPanel.fixed.tsx` under `/private/tmp/hy4-b2-a1/`)  
are left in place, unmodified, so a reviewer can run the control themselves.

## What is NOT verified

- The negative control for every new A1 test (see above). NEEDS-REVIEWER-RUN.
- `make frontend` (full 99-file vitest + tsc), `cd section7 && npm run build`,  
  and `make static-gates` — none was run. The batch header says the reviewer


re-ran these green before this batch, but they have **not** been run against  
this diff. Exact commands: `make frontend`,  
`cd "/Users/user/untitled folder 2/harness/section7" && npm run build`,  
`make static-gates`. Expected: rc=0 for each. NEEDS-REVIEWER-RUN.

- No test proves the browser-side mount (`SynthesisSection` inside `page.tsx`):  
  node-env tests render the pure `SynthesisPanel` only. The 10 s poll and the  
  "reveal only after children are seen" gating are pinned by a source-parity  
  assertion, not by running the effects.
- No live `/synthesis` call was made; the API helper test uses a stubbed  
  `fetch`. Nothing was deployed.

## Unrelated findings (one line each)

- `agentTree.tsx:41-46` already shows a child's title, `agentName vN`, state  
  pill, remaining budget, progress and last tool call, but **not** delegatedBy,  
  scope paths, metered spend, runStatus or result — the new panel is the first  
  place those appear.
- `delegation.py:209-212` computes conflicts from declared scope paths, not  
  files actually written; the panel says "Overlapping scopes", not "conflicting  
  writes" (an honest label, not a new finding).

## Risks the reviewer should check firstx

1. The stop condition itself: confirm that a `cp`-overwrite denial should end  
   the whole batch. If you waive it, the saved copies in `/private/tmp/hy4-b2-a1/`  
   are sufficient to stage the negative control, and A2/A3/B1/B2/B3 are still  
   untouched.
2. Whether revealing the panel only after a successful response with children  
   matches "render it only when the run has delegated children". Loading and  
   error states are therefore not visible on the very first request for a run  
   whose children have not yet been observed; they are visible on every later  
   poll. All four states are rendered and tested on `SynthesisPanel` itself.
3. `spentUsd` is rendered as the raw API string (e.g. `0.002512`, `0`). If a  
   currency prefix is wanted, that is a display decision, not a data change.
4. The panel mounts in the `overview` tab; batch 1 put scope requests in  
   `working`. Confirm that placement.

## Superseded

The reviewer ran the negative control and the three acceptance checks that this
hand-back recorded as NOT RUN, and accepted the A1 code as written. This section
closes those four items; nothing in the record above is rewritten or removed.

- Negative control: seven separate mutations (null-result text removed, review
  line removed, `"0"` rendered as a dash, conflicts block removed, empty-state
  text removed, wrong URL, a Merge button added) each made exactly one of the 8
  tests fail; files were restored and checked with `cmp`, and the 8 tests pass
  again. The "Proof the new tests fail without the change" gap above is closed
  by the reviewer, not by this engineer.
- `make frontend` rc=0 (1083 tests).
- `cd section7 && npm run build` rc=0.
- `make static-gates` rc=0.
- The three open questions under "Risks the reviewer should check first" are
  decided: revealing the panel only after a successful response with children
  is accepted; `spentUsd` stays the raw API string; the overview-tab placement
  is accepted.

A1's status in `hy4-b2-handback-SUMMARY.md` is therefore "DONE by reviewer,
your code accepted", not BLOCKED. The rejected-permission history above is kept.
