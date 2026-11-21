# Hand-back: F1
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: f7af86d
Status: DONE
Token use (estimate): 0.15M (retry session; first attempt hit the run cap after 141k)

## Files changed (uncommitted)
- `docs/briefs/evidence/ui/u0-inventory-2026-11-14.md` — inventory: 27 routes + layout, old/new counts, nine settings pages listed separately, reproducible commands.
- `docs/briefs/evidence/glm/F1-handback.md` — this file.

## Checks run
- `find section7/app -name "page.tsx" | sort` — 26 pages + `app/layout.tsx`.
- `wc -l section7/app/globals.css` — 1207 lines, 93 top-level class defs (40 unique class names matched).
- Classification pass (python, quoted in the inventory's Reproducible commands): per-page component imports from `from "...components/..."`, then per-component `className=` match against globals class names vs `tokens[|var(--`.
- Every route from the dispatcher's verified list appears in the table. No guessed rows.

## Checks not run
- npx vitest / tsc — read-only packet, no code changed. NEEDS-REVIEWER-RUN only if the reviewer wants a frontend health check.

## Decisions I made (reviewer may overrule)
- Old/new classification at the component-file level: old = file's `className` matches a top-level globals.css class; new = file uses `tokens[` or `var(--`; mixed = both; shared = neither (ui/ primitives, tokens.ts).
- `AppShell.tsx` counted OLD (uses `className="shell"`, `titlebar`, `topbar`… and no tokens) — this is the long pole for migration.
- `docs/page.tsx` and `pricing/page.tsx` import only `tokens.ts` → status `partial (no component styling)`.
- First attempt hit the 20-minute run cap mid-table with no files; this retry used the dispatcher's pre-anchored route list and wrote the inventory before the hand-back.

## Open questions
- Whether `ui/index.ts` barrel imports should count each primitive (Brand/Notice/Button/…) separately — currently counted via the barrel + classified through `src/components/ui/`.

## Unrelated issues seen
- Owner commits were landing during this run (04b819b → f7af86d, UI runs-list work) — none touched my two files.

## Known limits of this change
- Classification is file-level (a component is one style bucket), not per-JSX-node.
- `docs`/`pricing` pages have no component imports beyond tokens.ts — flagged, not deep-read.
