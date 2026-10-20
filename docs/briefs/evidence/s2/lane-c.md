# Session 2 lane C - shared UI + Playwright proof (parts 12, 24) - 2026-10-18

## Files
- section7/app/projects/[projectId]/runs/[runId]/collabModel.ts: approvalLabel, fileStatuses, describeDecision, reopenedApprovals, mergeEvents (dedupes overlapping polls), isOpenApproval; describeEvent handles `leader.takeover` and never prints payload of unknown kinds.
- .../sharedRunPanel.tsx: "Pending decisions (N)" with "Decision needed from <leader>" per request (or "no session leader"); "Changed files" with per-file status in words; "Decision history"; goal line from GET /runs/{id}/plan; "proposal version" (digest prefix); "earlier approval no longer applies" notice when an approved proposal returns to pending with a new digest; aria-live region, groups, aria-describedby on Approve/Reject, focus parked on the card after acting, focusable scrollable diff, aria-invalid/status on the form message.
- section7/app/dashboard/planGate.tsx: removed the false "(no goal recorded)" line on joined runs.
- .../collab.test.tsx: +14 Vitest cases (responsible person, goal, file status, history, keyboard/native controls, status language, re-approval, no private reasoning, event merge).
- section7/e2e/multiplayer.e2e.ts: three `test.step` legs on a second run held open (goal P1_SLOW) after the original flow.
- deploy/pilot/run_smoke.py: file-level assertion now expects two run workspaces, exactly one with gone.txt removed (run 1) and run 2's gone.txt still present (the old "no gone.txt anywhere" check could not hold with two runs).

## Commands (all run, this session)
- `npx tsc --noEmit` (section7): clean. `npx vitest run`: 59 files, 540 passed (baseline 526).
- `python3 deploy/pilot/run_smoke.py --otp` x4: run1 Playwright 1 passed but smoke assertion failed (old gone.txt check); run2 Playwright passed, assertion failed (hidden .baseline/.staged dirs counted); run3 exit 0; run4 (final code) exit 0, "1 passed (45.3s)", retries=0, `faults` array empty (no console/page/network errors outside the reconnect window). Logs: docs/briefs/evidence/s2/logs/lane-c-run{1..4}.log. Overwrote docs/briefs/evidence/p1/* and section7/test-results (expected); new screenshot section7/test-results/multiplayer-run2.png.

## Playwright legs (real OTP sign-in, two browser contexts, real services; actions the UI lacks go through real HTTP with the user's own token)
- (a) changed proposal re-requires approval: PASS. Peer files a proposal (POST /approvals), leader sees "Decision needed from p1-a (you)", peer sees no Approve button; leader approves BY KEYBOARD (focus + Enter, focus lands on the card); peer amends (PUT) -> page shows "Waiting for p1-a", notice "earlier approval no longer applies", new digest, Approve button again, decision-history entry gone; stale-digest decision is refused 409 DIGEST_MISMATCH; approval of v2 works.
- (b) cross-run isolation: PASS. Run 2 page shows none of run 1's history/goal/file; run 2's approvals list is empty; run-1 approval id via run 2's route -> 404; run 1's page shows none of run 2's data; workspace file check in run_smoke.
- (c) refresh/reconnect: PASS. Mid-run (state executing) both pages reloaded: leader, roster, run state, approval, decision history, and ordered history rows identical; peer taken offline -> alert shown, leader sends guidance, peer back online catches up without reload and alert clears.

## Verdict
- Part 12: meets the 9/10 bar for items 002-008 and 010 within the polling data path. Not claimed: item 009 (terminal details) and responsive layout were not touched or tested.
- Part 24: 002-009 covered; 010 (errors/cancellation for non-engineers) only partly: server error messages are shown verbatim, no plain-language error mapping was added.

## Gaps and risks
- (Resolved in Session 11, `PROJECT-STATE.md` §3g-7: `decidedAt` is returned and shown, and `GET /approvals` flags `amendedAfterApproval` from `approval.amended` events.) Approvals API had no decision timestamp or amend event, so decisions are listed in request order and "changed after approval" is detected client-side (lost on reload; the pending state and new version id remain). Backend would need decided_at and a collab event on amend.
- No UI for filing or amending a proposal (e2e uses HTTP for those two steps); approval cards for human proposals stay in "Pending decisions" as "Approved" because there is no apply path for them.
- A11y not verified with a real screen reader or contrast tooling; Pill colours not audited; vitest is static-render (no DOM), keyboard proven only in the Playwright leg. Offline leg tolerates console/network errors only inside its window. Delivery of OTP email still not proven (log capture).
