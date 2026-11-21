# hy4 batch 2b hand-back — A2

Task: A2 — Scope-request editor: keyboard and screen-reader basics
Status: DONE
Shorter alternative: **yes, but not worth it.** The ~200-line diff is 15 lines in
`ScopeRequestEditor.tsx`, 9 in `scopeRequestSection.tsx` and 176 in the new
test file. The tests are the bulk because each of the seven behaviours has its
own case (required: "each fails against the unfixed component"). They could be
collapsed into one table-driven case, but then a single mutation would fail
several assertions at once and the reviewer could not see which behaviour each
control kills. The production change is 24 lines; that is the smallest change
that satisfies the seven named behaviours.

Assumptions: node-env vitest only (no jsdom/RTL), so keyboard behaviour is
proven by walking the returned element tree and invoking handlers directly plus
SSR markup/source parity, matching `a11yAccessibleNames.test.tsx` and
`focus.test.ts`; the editor stays hook-free so batch 1's plain-function test
keeps working; the host `scopeRequestSection.tsx` is the editor's own wrapper
(not an unrelated component) and is the only place an unmount-time focus
restore can live; the "Edit scope" trigger is `ToolActivity.tsx`'s
`data-testid="scope-edit-open"` button; "Enter does not submit" means Enter
keeps its newline behaviour in the multi-line paths field and never reaches
`onConfirm` — no form or submit button exists to submit.

## Initial `git status --short` for this session (batch 2b)

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
 M section7/app/projects/[projectId]/runs/[runId]/controlApi.ts
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
?? docs/briefs/hy4-b2-handback-A1.md
?? docs/briefs/hy4-b2-handback-SUMMARY.md
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
?? docs/briefs/hy4-master-prompt-batch2b-resume-2026-11-05.md
?? docs/decisions/2026-11-05-intent-permissions.md
?? docs/runbooks/redeploy-worker-and-orchestrator.md
?? docs/yc/
?? scripts/e2e/m01_native_edit_drill.py
?? scripts/ops/
?? section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.test.tsx
?? section7/app/projects/[projectId]/runs/[runId]/synthesisPanel.tsx
?? section7/src/components/ScopeRequestEditor.test.tsx
?? section7/src/components/ScopeRequestEditor.tsx
?? section7/src/lib/scopeRequest.ts
```

HEAD `f5b298a`, branch `w28-s1-delegate`. All entries are known (batch 1 plus
batch 2's A1 and its hand-backs). Nothing was reverted, reformatted or tidied;
`docs/yc/` was left alone; no changes I do not recognise appeared.

## Files changed

Edited (batch-1 files, edited in place):
- `section7/src/components/ScopeRequestEditor.tsx` — added the pure
  `restoreTriggerFocus(el)` export; `onKeyDown` on the section (Escape
  preventDefault + `onCancel`, Enter ignored); `id="scope-request-warning"` on
  the warning paragraph; `aria-describedby="scope-request-warning"` on the Save
  scope button; `autoFocus` on the textarea. Still hook-free and form-free.
- `section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx` —
  `useRef<HTMLElement | null>` capturing `document.activeElement` while the
  component renders (before the autofocus commit) and an effect cleanup that
  calls `restoreTriggerFocus(triggerRef.current)` on unmount, so focus returns
  on every close path (Escape, Cancel, saved edit).

New:
- `section7/tests/unit/scopeRequestEditorA11y.test.tsx` — 7 vitest cases
  (accessible names + confirm warning, autofocus on open, focus restored on
  close, Escape cancels, Enter does not submit, warning association, keyboard
  open/edit/cancel).

No other component was touched. `ToolActivity.tsx` is read only (source
parity), not edited.

## Commands run (each with its result, copied, not summarised)

```
$ cd "/Users/user/untitled folder 2/harness" && mkdir -p /private/tmp/hy4-b2-a2 && cp "section7/src/components/ScopeRequestEditor.tsx" /private/tmp/hy4-b2-a2/ScopeRequestEditor.orig.tsx && cp "section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx" /private/tmp/hy4-b2-a2/scopeRequestSection.orig.tsx && ...
PRE-A2 COPIES SAVED
total 8
-rw-r--r--  1 user  staff  1976 2026-11-05 21:36 ScopeRequestEditor.orig.tsx
-rw-r--r--  1 user  staff  2112 2026-11-05 21:36 scopeRequestSection.orig.tsx
```

Tests FIRST, against the unfixed component:
```
$ cd "/Users/user/untitled folder 2/harness/section7" && npx vitest run tests/unit/scopeRequestEditorA11y.test.tsx --reporter=basic
 FAIL  ... > cancels on Escape without submitting
AssertionError: expected 'undefined' to be 'function'
 FAIL  ... > does not submit on Enter in the text field before the confirm step
AssertionError: expected 'undefined' to be 'function'
 FAIL  ... > associates the 'saving voids approvals' warning with the confirm control
AssertionError: expected '<button type="button" data-testid="sc…' to contain 'aria-describedby="scope-request-warni…'
 FAIL  ... > can be opened, edited and cancelled with the keyboard alone
AssertionError: expected '<label for="scope-request-paths" styl…' to contain 'autofocus'
 Test Files  1 failed (1)
      Tests  7 failed (7)
VITEST_RC=1
```
(7 failures; the tail above shows cases 4-7, cases 1-3 fail on the same
missing `id`/`aria-describedby`/`restoreTriggerFocus` artefacts.)

After the fix, two tests failed on my own helper picking the `<label>` instead
of the `<textarea>` (both contain `scope-request-paths`). I fixed the TEST's
marker to `scope-paths`, not the product:
```
$ cd "/Users/user/untitled folder 2/harness/section7" && npx vitest run tests/unit/scopeRequestEditorA11y.test.tsx src/components/ScopeRequestEditor.test.tsx --reporter=basic
 ✓ tests/unit/scopeRequestEditorA11y.test.tsx (7 tests) 14ms
 ✓ src/components/ScopeRequestEditor.test.tsx (6 tests) 14ms
 Test Files  2 passed (2)
      Tests  13 passed (13)
VITEST_RC=0
=== tsc ===
TSC_RC=0
```

Negative-control staging (fixed versions saved as `.mine`, as required by
amendment 2):
```
$ cd "/Users/user/untitled folder 2/harness" && cp "section7/src/components/ScopeRequestEditor.tsx" /private/tmp/hy4-b2-a2/ScopeRequestEditor.mine && cp "section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx" /private/tmp/hy4-b2-a2/scopeRequestSection.mine && ...
MINE COPIES SAVED
=== git diff --stat (before controls; both files are untracked, so empty) ===
(end of diff --stat)
```

```
$ make frontend        # plain command, default tool timeout
(empty)
Exit Code: 137
Signal: SIGTERM

$ cd "/Users/user/untitled folder 2/harness/section7" && npx tsc --noEmit
TSC_RC=0

$ cd ... && npx vitest run --minWorkers=1 --maxWorkers=2 --reporter=basic   # default tool timeout
(empty)
Exit Code: 137
Signal: SIGTERM

$ cd ... && npx vitest run --minWorkers=1 --maxWorkers=2 --reporter=basic   # 600 s tool timeout
 Test Files  101 passed (101)
      Tests  1090 passed (1090)
   Duration  244.40s
VITEST_RC=0

$ cd "/Users/user/untitled folder 2/harness" && make frontend             # 600 s tool timeout
 Test Files  101 passed (101)
      Tests  1090 passed (1090)
   Duration  258.37s
MAKE_FRONTEND_RC=0
```
The two 137s were the tool's 120 s ceiling with no output (the documented
batch-1 environment quirk), not code faults; the same commands pass with a
longer timeout. No `cp`/`mv`/redirect overwrite of an existing file was used.

```
$ cd "/Users/user/untitled folder 2/harness" && git status --short -- docs/briefs/evidence/w21/native-confinement-container.json
(empty means unchanged, no checkout needed)
=== A2 file line counts / diff ===
     79 section7/src/components/ScopeRequestEditor.tsx
     79 section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx
    176 section7/tests/unit/scopeRequestEditorA11y.test.tsx
=== cmp after full suite ===
CMP_BOTH_OK
```

## Proof the new tests fail without the change (four real negative controls)

Method per amendment 2: `.mine` copies saved first; each mutation was ONE
targeted change made with the file-edit tool; the test file was run at rc=1;
the file was then restored with the file-edit tool. No `cp`/`mv`/shell redirect
overwrote an existing file. Full output for each (grep of FAIL/summary lines):

```
CONTROL 1 — remove `autoFocus` from the textarea:
 FAIL  ... > moves focus into the editor when it opens
Expected: "autofocus"
Received: "<textarea id="scope-request-paths" data-testid="scope-paths" rows="4" style="...">"
 FAIL  ... > can be opened, edited and cancelled with the keyboard alone
 Test Files  1 failed (1)
      Tests  2 failed | 5 passed (7)
CONTROL1_RC=1

CONTROL 2 — remove the section's `onKeyDown`:
⎯⎯⎯⎯⎯⎯ Failed Tests 3 ⎯⎯⎯⎯⎯⎯
 FAIL  ... > cancels on Escape without submitting
 FAIL  ... > does not submit on Enter in the text field before the confirm step
 FAIL  ... > can be opened, edited and cancelled with the keyboard alone
 Test Files  1 failed (1)
      Tests  3 failed | 4 passed (7)
CONTROL2_RC=1

CONTROL 3 — remove `aria-describedby` from the Save scope button:
⎯⎯⎯⎯⎯⎯ Failed Tests 2 ⎯⎯⎯⎯⎯⎯
 FAIL  ... > gives every control an accessible name, and the confirm control its warning
 FAIL  ... > associates the 'saving voids approvals' warning with the confirm control
 Test Files  1 failed (1)
      Tests  2 failed | 5 passed (7)
CONTROL3_RC=1

CONTROL 4 — remove the host's triggerRef + unmount cleanup effect:
⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯
 FAIL  ... > hands focus back to the Edit scope button when the editor closes
 Test Files  1 failed (1)
      Tests  1 failed | 6 passed (7)
CONTROL4_RC=1
```
Union of failing cases: all 7. Every new behaviour is covered by a control.

Restore proof after the four mutations:
```
$ cmp "section7/src/components/ScopeRequestEditor.tsx" /private/tmp/hy4-b2-a2/ScopeRequestEditor.mine; echo "CMP_EDITOR_RC=$?"
=== cmp (must print nothing) ===
CMP_EDITOR_RC=0
$ cmp "section7/app/projects/[projectId]/runs/[runId]/scopeRequestSection.tsx" /private/tmp/hy4-b2-a2/scopeRequestSection.mine; echo "CMP_SECTION_RC=$?"
CMP_SECTION_RC=0
$ git diff --stat -- <both files>
(end of diff --stat)        # same as before the controls: both are untracked
$ npx vitest run tests/unit/scopeRequestEditorA11y.test.tsx src/components/ScopeRequestEditor.test.tsx --reporter=basic
 Test Files  2 passed (2)
      Tests  13 passed (13)
RESTORE_VITEST_RC=0
```
`cmp` printed nothing (rc=0) for both files; `git diff --stat` is empty before
and after; the tests are green again. No mutated file remains in the tree.

## What is NOT verified

- Real browser focus behaviour. Node-env vitest cannot run `useEffect` or DOM
  focus; "focus returns to the Edit scope button" is proven by a hand-built
  element fake for `restoreTriggerFocus` plus source parity on the host effect.
  A browser/Playwright check would be needed to see the actual focus move.
- `aria-describedby` association as read by a real screen reader; only the
  markup pairing is proven.
- Escape/Enter were invoked as handler calls with synthetic key events, not
  through a real keyboard event dispatched into a DOM.
- Nothing was deployed; no live model run, no paid call.

## Unrelated findings (one line each)

- Batch 1's `ScopeRequestEditor.test.tsx` calls the component as a plain
  function; adding hooks there would break it, which is why focus management
  lives in the host section — worth remembering before "simplifying" the editor.
- `make frontend` needs a tool timeout above 120 s on this host (244-258 s);
  batch 1 recorded the same 137 SIGTERM behaviour.

## Risks the reviewer should check first

1. Whether editing `scopeRequestSection.tsx` counts as "other components" under
   A2's out-of-scope line. It is the editor's own host and the only place an
   unmount-time focus restore can live; no unrelated component was touched.
2. `useRef` reads `document.activeElement` during render. If a future opener
   mounts the editor without first focusing a control (e.g. a timer), focus
   would return to `<body>`'s active element rather than the Edit scope button.
3. The Escape handler sits on the `<section>`; it relies on keydown bubbling
   from the focused textarea/buttons. It does not prevent Tab from leaving the
   editor (no focus trap) — the task did not ask for one.
