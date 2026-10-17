# A4-S5 — Workflow library, accessibility, i18n, status and feedback

Baseline 31bf407, branch `codex/harness-a4`. Nothing pushed.

## Built
- Service (`integrations/editors/service`, `extras.py`): prompt/workflow library (F135), run-linked feedback (F140), `/status` (F139).
- section7: `commandPalette.ts` + `CommandPalette.tsx` (F136), `i18n.ts` (F138), `StatusView.tsx`, inbox strings moved onto the catalog, axe + keyboard Playwright suite (F137). `axe-core@4.10.2` added as a devDependency.

## Defects found by tests and fixed
- Feedback upsert silently duplicated: SQLite treats NULLs as distinct in UNIQUE; unlinked columns now store '' and map back to null.
- `Intl.DateTimeFormat` rejects `dateStyle` with `timeZoneName`; switched to explicit fields.
- axe: `kbd` inside the active palette option failed contrast under page CSS; now inherits colour.

## Results (`evidence/`)
Service 53 pass (6 new); section7 vitest + tsc, Playwright suite incl. 6 new a11y/keyboard tests across en/es/ar.

## Honest limits
- Automated accessibility only, on new surfaces. No screen-reader pass, no audit of pre-existing screens.
- Spanish and Arabic text is machine-authored; needs human translators.
- Palette and status view are components, not yet mounted in the shell (NEEDS INTEGRATION). `/status` assumes each upstream serves `/health`.
- Feedback can only link runs this lane has seen.
