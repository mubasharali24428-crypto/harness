# C-07 pilot feedback template — visible disagreement on assignment plans

Fill in one copy per pilot session. Do not paste secrets, customer data or private member task text.
Leave a field blank rather than guessing; write "not observed" where nothing happened.

## Session

- Date (UTC):
- Pilot team / session id:
- Harness build (image tag or git SHA):
- Facilitator:
- Participants (roles only: admin / member / viewer):

## What C-07 does (reference)

When an admin rejects an assignment-plan version, a non-blank note is required (422 `NOTE_REQUIRED`
otherwise). The note is stored on the new version and shown in the admin version history as
"Review note:". Approve stays note-optional. Source: `docs/briefs/evidence/sprint4/task-26-phase7-c05-c07.md`.

## Observations

| # | Question | Answer (yes / no / not observed) | Evidence (screenshot name, run id, quote) |
|---|---|---|---|
| 1 | Did an admin reject a plan version during the session? | | |
| 2 | Was the note requirement understood without help? | | |
| 3 | Was the Reject button's disabled state and hint clear? | | |
| 4 | Did the note appear in version history where the admin expected it? | | |
| 5 | Did a member or the planner understand why the plan was rejected from the note alone? | | |
| 6 | Did anyone try to reject with a blank or whitespace-only note? What happened? | | |
| 7 | Did a disagreement between admin and member become visible to the other party? | | |

## Free-text feedback

- What worked:
- What was confusing:
- Wording changes suggested for the note prompt or the hint:
- Anything that looked like it leaked another member's task content (report immediately):

## Outcome

- [ ] No change needed
- [ ] Wording / UX change proposed (describe above)
- [ ] Defect found (link issue):

## Honesty rule

Only record what was actually observed in a live session. A simulated or scripted run is not pilot
feedback and must be labelled as such.
