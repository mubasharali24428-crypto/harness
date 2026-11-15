# Decision: D-U1 — UI component stack

**Date:** 2026-11-13 · **Status:** ACCEPTED by the decision owner (Mubashar Ali, in session), 2026-11-13.
**Resolves:** UI overhaul plan §6 D-U1; FUTURE PLAN 2.0 §5A Monday decision.
**Current state:** Tailwind CSS v4 and `cmdk` are already dependencies
(`section7/package.json`); the shared primitives in `section7/src/components/ui/` are hand-written.
There is no Radix or shadcn/ui dependency yet.

## Options

1. **Tailwind v4 + shadcn/ui (Radix primitives, source copied into the repo) + cmdk.**
   Recommended by the UI plan. Gives accessible dialogs, menus and focus management without
   inventing them; components are owned source, not an opaque package.
2. **Keep hand-written CSS + add Radix primitives + cmdk only.**
   Smaller dependency change, but every new screen still needs bespoke CSS and the 1,178-line
   `globals.css` keeps growing.

## Recommendation

**Option 1**, introduced page by page beside the existing stylesheet, starting with the app shell.
Keep the existing tokens (`colors`, `fontSize`, `space`, `radius`) as the theme source so light and
dark stay in one place. Do not do a flag-day restyle: old pages keep working while they migrate.

## Consequences if accepted

- Add `shadcn` CLI-generated source under `section7/src/components/ui/` and Radix packages.
- Every migrated component must still pass axe and the 100 ms palette budget.
- No hard-coded hexes; map utilities to the existing CSS variables.
- Review cost: new source files in the repo, no runtime service.

**Owner decision:** ACCEPTED — Option 1 (Tailwind v4 + shadcn/ui + cmdk), introduced page by page.
**Date:** 2026-11-13 · **Rationale:** as recommended; no divergence.

---

## Amendment — 2026-11-15

**Status:** AMENDED by the decision owner (Mubashar Ali, in session), 2026-11-15.
**Decision now in force:** hand-written CSS on the existing tokens + Tailwind CSS v4 + `cmdk`.
**shadcn/ui and direct Radix dependencies are not adopted.**

**Why:** phases R1–R5 and the UI-week leftovers were built and verified on the hand-written
primitives in `section7/src/components/ui/` and the `ws-*` classes in `app/globals.css`. shadcn/ui
was never installed (`section7/package.json` has no shadcn or Radix entry). Installing it now would
mean re-skinning screens that already pass axe (WCAG 2.2 AA), the 375 px checks and the e2e
journeys on the RC stack, for no change a user can see.

**What stays from the original decision:**
- One token source for light and dark; no hard-coded hexes in components.
- Every new or changed screen passes axe and the keyboard checks before it ships.
- The palette keeps `cmdk`.

**What this amendment costs:** dialogs, menus and focus handling stay our own code. The native
elements in use (`<details>`, `<dialog>` via `cmdk`, `<datalist>`) and `src/lib/focus.ts` cover
today's screens. `globals.css` was 1,849 lines at commit `14961be` and has grown since; that
growth is the price of this choice.

**Revisit when:** a screen needs a primitive that is costly to build accessibly by hand (combobox
with async results, nested menus, date range picker). Adopt the single Radix primitive needed at
that point rather than the whole shadcn/ui set.
