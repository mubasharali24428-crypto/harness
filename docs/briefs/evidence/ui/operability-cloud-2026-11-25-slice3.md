# UI operability, cloud evidence: slice 3 (approvals a non-expert can finish)

- Slice: 3, approvals
- Branch: `claude/ui-op-s3-approvals` (off `w28-s2-sprints1-4` at `fe2233c`)
- Code SHA: `5bc283f`
- Date: 2026-11-25 (cloud session)

## Commands and exit codes

Run from `section7/` after `npm ci` (exit 0).

| Command | Exit | Result |
|---|---|---|
| `npx tsc --noEmit` | 0 | clean |
| `npx vitest run src/components/workspace/approvalCard.test.tsx` | 0 | 1 file, 11 tests passed |
| `npx vitest run` (full) | 0 | 126 files, 1,285 tests passed (baseline 125 files, 1,274 tests) |

## What the tests lock in (`section7/src/components/workspace/approvalCard.test.tsx`)

- The card's heading says what approving does ("If you approve, the agent deletes src/old.py.") and the next line is one line of why (the server's `plain` sentence, else the summary's first line, never empty).
- File list and diff are inside a closed "Technical details" disclosure, one control away.
- `similarCases` renders as text only: no button, link, input or form inside it, and no button on the card mentions a rule, save or apply (CD-02).
- Approve and Deny are native buttons whose accessible names differ and name the change ("Approve deleting src/old.py" / "Deny deleting src/old.py"); nothing is decided on render; a role that cannot decide sees both disabled with the reason.
- After a mocked success the decision POST carries the digest, and the card replaces its buttons with a receipt built from the server's answer (Approved / Denied, who, when; an approved staged change says applying is a separate step).
- After a mocked error (409) the error text is an alert on that card and the buttons stay for a retry.
- A decided card keeps its receipt after the pending list drops it; a failed decision on a card the server no longer lists is not shown again.

## Scope

- Changed: the run column's inline approval card (`RunPrompts.tsx` → new `ApprovalCard.tsx`). The visible "Reject" label there is now "Deny", matching the phone triage view.
- Not changed: the Advanced pane's shared-run approvals (`sharedRunPanel.tsx`), the decision endpoint, risk tiers (still off), any auto-approve path (none exists).

## Not run

- Live RC / Mac: NOT RUN (cloud session, no stack).
- Playwright e2e and screenshots: NOT RUN (they need a running server).
