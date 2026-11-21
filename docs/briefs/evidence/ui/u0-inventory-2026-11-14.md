# U0 route and component inventory — 2026-11-14

Revision: `f7af86d`. Stylesheet: `section7/app/globals.css` (1207 lines, 93 top-level class defs).

Purpose: list every route and the components it imports, classified old-style (class names defined in `globals.css`) vs new-style (tokens). Feeds the Tue U0 migration order.

Method: scripted `grep` of `from "@/components/` (and `from "../../` / `from "../` variants) per page, then one scripted classification pass over `section7/src/components/`.



## Reproducible commands

```sh
# 1. Route list
find section7/app -name "page.tsx" | sort
# 2. Old-style class defs in globals.css (top-level .class)
grep -E '^\s*\.[A-Za-z_]' section7/app/globals.css | wc -l
# 3. Per-page component imports (from "@/components/" and relative variants)
grep -R -n 'from "[^"]*components/' section7/app --include='*.tsx'
# 4. Token usage per component
grep -R -n 'tokens\[|var(--' section7/src/components
# 5. Classification script used for the table (run from section7/)
#    old-style: component file contains className= matching a globals.css top-level class
#    new-style: component file contains tokens[ or var(--
#    shared: neither (ui/ primitives, tokens.ts)
```

## Route table

| Route | Components imported | Old-style | New-style | Status |
|---|---|---|---|---|
| `app/auth/sso/callback/page.tsx` | ui/Brand.tsx [new], ui/Notice.tsx [new] | 0 | 2 | migrated |
| `app/dashboard/cost/page.tsx` | AppShell.tsx [old] | 1 | 0 | old |
| `app/demo/page.tsx` | ui/Brand.tsx [new], ui/Notice.tsx [new] | 0 | 2 | migrated |
| `app/docs/page.tsx` | tokens.ts [shared] | 0 | 0 | partial (no component styling) |
| `app/login/page.tsx` | ui/Brand.tsx [new], ui/Notice.tsx [new] | 0 | 2 | migrated |
| `app/meeting/page.tsx` | AppShell.tsx [old], ui/Notice.tsx [new] | 1 | 1 | partial |
| `app/notifications/page.tsx` | AppShell.tsx [old] | 1 | 0 | old |
| `app/notifications/unsubscribe/page.tsx` | ui/Notice.tsx [new] | 0 | 1 | migrated |
| `app/onboarding/page.tsx` | AppShell.tsx [old], States.tsx [old] | 2 | 0 | old |
| `app/page.tsx` | CommandCenter.tsx [mixed], Landing.tsx [mixed] | 2 | 2 | partial |
| `app/pricing/page.tsx` | tokens.ts [shared] | 0 | 0 | partial (no component styling) |
| `app/projects/[projectId]/runs/[runId]/checkpoints/page.tsx` | CheckpointsPanel.tsx [new] | 0 | 1 | migrated |
| `app/projects/[projectId]/runs/[runId]/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new], ChangeReview.tsx [shared], RunTabs.tsx [new], States.tsx [old], WorkingArea.tsx [mixed] | 3 | 4 | partial |
| `app/projects/[projectId]/tree/page.tsx` | AppShell.tsx [old] | 1 | 0 | old |
| `app/review/page.tsx` | AppShell.tsx [old], ui/Notice.tsx [new] | 1 | 1 | partial |
| `app/runs/page.tsx` | AppShell.tsx [old], States.tsx [old] | 2 | 0 | old |
| `app/settings/account/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new] | 1 | 1 | partial |
| `app/settings/billing/page.tsx` | AppShell.tsx [old] | 1 | 0 | old |
| `app/settings/governance/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new] | 1 | 1 | partial |
| `app/settings/integrations/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new] | 1 | 1 | partial |
| `app/settings/keys/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new] | 1 | 1 | partial |
| `app/settings/models/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new], ModelPrefs.tsx [new] | 1 | 2 | partial |
| `app/settings/notifications/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new] | 1 | 1 | partial |
| `app/settings/page.tsx` | AppShell.tsx [old] | 1 | 0 | old |
| `app/settings/project/page.tsx` | AppShell.tsx [old] | 1 | 0 | old |
| `app/settings/team/page.tsx` | AppShell.tsx [old], Breadcrumbs.tsx [new], States.tsx [old] | 2 | 1 | partial |
| `app/layout.tsx` | lib/theme (THEME_BOOT_SCRIPT), globals.css | — | — | layout (not a page; imports globals.css directly) |

`ui(missing)` in the raw pass = `src/components/ui/index.ts` barrel imports (Brand, Notice, Button, Card, Modal, Pill, Table) — classified via `src/components/ui/`.

## Nine settings pages (decision's separate list)

1. `app/settings/page.tsx` — old
2. `app/settings/account/page.tsx` — partial
3. `app/settings/billing/page.tsx` — old
4. `app/settings/governance/page.tsx` — partial
5. `app/settings/integrations/page.tsx` — partial
6. `app/settings/keys/page.tsx` — partial
7. `app/settings/models/page.tsx` — partial
8. `app/settings/notifications/page.tsx` — partial
9. `app/settings/project/page.tsx` — old
10. `app/settings/team/page.tsx` — partial

(11 entries because `settings/page.tsx` (index) is listed first, then the nine sub-pages, then `team`. The nine sub-pages: account, billing, governance, integrations, keys, models, notifications, project, team.)

## Notes for the frontend lead

- `AppShell.tsx` is the long pole: it is imported by almost every non-landing page and is still old-class (`shell`, `titlebar`, `topbar`, `sidebar-toggle`, …). Migrating AppShell flips most `old`/`partial` rows.
- `States.tsx` is old-class (`skel`) and sits on onboarding, runs, team.
- `CommandCenter` / `Landing` / `WorkingArea` are mixed (token + some old class hits) — inspect those three after AppShell.
- `docs/page.tsx` and `pricing/page.tsx` import only `tokens.ts` (shared) — they may be inline-styled or unstyled; not counted as component work.
