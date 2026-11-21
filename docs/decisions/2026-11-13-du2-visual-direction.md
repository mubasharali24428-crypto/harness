# Decision: D-U2 — visual direction

**Date:** 2026-11-13 · **Status:** ACCEPTED by the decision owner (Mubashar Ali, in session), 2026-11-13.
**Resolves:** UI overhaul plan §6 D-U2; FUTURE PLAN 2.0 §5A Monday decision.

## Context

The shipped app is dark-first (GitHub-dark palette, blue accent) and already has a light theme
(`globals.css` `:root[data-theme="light"]`) that passes the existing contrast tests. The client
trust portal (U4) is the surface most likely to be shown to a non-engineering buyer.

## Options

1. **Dark-first with one accent**, keeping light as a supported secondary theme. Recommended by
   the UI plan; matches the existing UI and the Linear/Vercel family the plan references.
2. **Light-first, warmer look** for the client portal, with dark as the operator theme. More work:
   two visual systems to maintain and screenshot.

## Recommendation

**Option 1.** Keep one design system and both themes; do not fork the portal's look. The portal can
use the same tokens with a read-only, lower-density layout. This avoids a second palette, a second
contrast audit and a second screenshot set.

## Consequences if accepted

- Every new surface ships in both themes; screenshots and axe run in both.
- The portal (U4, gated on P-17) is scoped as a layout over the same tokens, not a new theme.
- Accent remains one colour; status colours keep their existing semantic mapping.

**Owner decision:** ACCEPTED — Option 1 (dark-first, light secondary).
**Date:** 2026-11-13 · **Rationale:** as recommended; no divergence.
