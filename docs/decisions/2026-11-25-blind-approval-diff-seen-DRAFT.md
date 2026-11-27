# X-D3 — what counts as "diff seen" for the blind-approval KPI — DRAFT (unsigned)

- **Status:** DRAFT, finalized wording (2026-11-25). The rule below is **already applied in code**
  (Task 18 / Task 32, merge of `master` into PR 27) as an engineering default. It is **not an owner
  decision** until signed below. Nothing here enables anything.
- **Date:** 2026-11-25 · **Related:** X-07 (approval rendering), X-08, X-D1; KPI "blind-approval rate".
- **Origin:** the only code conflict when `master` (R4 "since last iteration", PR 29) merged into
  `w28-s2-sprints1-4`.

## Context

The run page's review pane sends `diffSeen` with every Approve: true when the diff was on screen
(IntersectionObserver, 25% visible) before the click. Section 8 counts an approval as **blind**
when `viewedDetail` is `false`.

R4 made the Diff pane open on **Since last iteration** (the newest checkpoint's hunks only), with
**Full pull request** one switch away. A reviewer can therefore approve having seen only the newest
hunks. The question: does that count as having seen the diff?

## Options

1. **Count only a view of the whole change (applied, recommended).** "Diff seen" is true only if
   (a) the **Full pull request** scope was on screen, or (b) the **Since last iteration** scope
   was on screen **and it covered every hunk of the full PR** (every PR file appears in the
   since-last view and every PR hunk's added/removed lines appear as a hunk of the same file).
   An empty or unloaded PR diff never counts.
   - Consequence: an approve after seeing only the newest hunks of a multi-iteration change is
     reported as blind. The KPI is stricter and shows a higher blind rate. A first-iteration PR,
     where since-last equals the whole change, still counts.
2. **Count either view.** Any diff on screen counts.
   - Consequence: better-looking numbers; weaker claim. A reviewer can approve a large PR after
     seeing only the last small hunk and be counted as informed.
3. **Count the full-PR view only, even for a first iteration.**
   - Consequence: strictest and simplest to explain, but marks honest first-iteration approvals as
     blind and forces an extra click.
4. **Require an explicit "I reviewed the full diff" acknowledgement instead of measuring.**
   - Consequence: shifts from measured to self-declared; easy to click through; not comparable to
     the existing measured KPI.

## Recommended default

Option 1, as applied. It protects the trust claim and is reversible in one place.

## Consequences

- Code: `section7/src/lib/diffScope.ts` (`sinceLastCoversFullPr`, `diffViewCountsAsSeen`),
  `IterationDiffScope.tsx` (`onIterationFiles`), `ReviewPane.tsx` (observer arms only while the view
  counts). Switching to Option 2 changes `diffViewCountsAsSeen` to also return true for
  `"since-last"`; nothing else moves.
- Honesty limits: the signal is "on screen", not "read". A reviewer can scroll past quickly.
  Blind rate is **UNPROVEN**: there is no pilot data. Not driven in a browser with R4 yet (unit
  tests only: `section7/tests/unit/diffScope.test.ts`).
- Does not change who may approve, any risk tier or any gate (P-D6 stays not enabled).

## Still missing

Owner signature. A browser run of the KPI path with R4 (owner Mac or approved cloud run), which
this record does not claim.

## Sign-off (owner only — leave blank)
- Decision: ______________________________________
- Signature: ____________________________________  Date: ______________
- ACCEPTED record to be created at: `docs/decisions/______________________`
