# E0 route inventory — all 29 page.tsx routes — 2026-11-21

Phase E0 of `docs/briefs/ui-ux-enhancement-master-prompt-2026-11-21.md` (§7 E0a).
Checkout: `/Users/user/untitled folder 2/harness`, tip `b9890e1` (plus pre-existing uncommitted evidence PNGs).
Stack observed: RC `harness-frontend:vv90r6` on `:3011`.

Columns:
- **Owner** — the page component, or the component it delegates to on first render.
- **Data** — where on-screen values come from (API client / event stream), or "static" for marketing pages.
- **States** — which of loading / empty / error / signed-out are explicitly designed today.
- **Primitives** — which `src/components/ui/*` primitives the page (or owner) renders.
- **globals.css families** — the CSS rule families the page's class names resolve to (what E1 must replace per page).
- **Notes** — observations feeding the E0 audit.

Every shell page gets `AppShell.tsx`: `.shell/.topbar/.sidebar/.scrim`, brand, project switcher, nav, workspace run-list groups (Needs you / Working / Done), footer menu, keyboard-help panel. Theme via `:root[data-theme]`.

| # | Route | Owner | Data | States | Primitives | globals.css families | Notes |
|---|---|---|---|---|---|---|---|
| 1 | `/` | `LandingView` + `Composer` + `PlanGateCard` | static marketing; session/auth only until compose | signed-out hero; composer disabled without session | Card | `.landing-*`, hero grid, `.composer` | Only route with the display type size |
| 2 | `/login` | `app/login/page.tsx` | `login`, `requestLoginOtp`, `verifyLoginOtp`, `ssoStart` | submit loading, error (Notice), OTP flip | Button, Notice, Brand | `.auth-*`, form controls | Real form drives dev-bypass and OTP paths |
| 3 | `/auth/sso/callback` | page itself | `ssoComplete` + `saveSession` | loading, error + retry link | Button, Notice, Brand | `.auth-*` | No shell by design (transient) |
| 4 | `/demo` | `app/demo/page.tsx` | `seedDemo`, `demoLandingHref` | seeding loading, error (Notice) | Button, Notice, Brand | `.demo-*`, `.auth-*` | REPLAY entry; ≤2-click promise walk-verified 11-21 |
| 5 | `/pricing` | `app/pricing/page.tsx` | static `lib/pricing` | n/a | none (raw markup) | `.pricing-*`, tables | Honesty copy pinned by tests |
| 6 | `/docs` | `app/docs/page.tsx` | static | n/a | none | `.docs-*` | Long page, no in-page anchors |
| 7 | `/onboarding` | page + `checklistView`, `readinessView` | `getReadiness`, `getChecklist` | loading, ErrorState, milestones-empty | Card | `.checklist-*`, `.onboard-*` | Progress label; skip affordance weak |
| 8 | `/meeting` | page + `RefAutocomplete`, `ContextPreviewPanel` | `getMessagePage`, `getBrief`, `sendWithAi` | ai-thinking loading, load-older, Notice errors | Button, Card, Pill, Notice | `.meeting-*` | Heaviest non-workspace page (415 ln) |
| 9 | `/notifications` | `InboxScreen` | inbox API | handled inside InboxScreen | (inbox styles) | `.inbox-*` | Thin delegating page |
| 10 | `/notifications/unsubscribe` | page | `unsubscribe` | loading, done, error | Button, Notice | `.auth-*` | Standalone, no shell |
| 11 | `/portal` | `app/portal/page.tsx` | `listRuns` via `apiFetch` | LoadingState, EmptyState, ErrorState | States | `.portal-*` | Admin view; guest /report journey unverified (§6 gap) |
| 12 | `/projects/:p/runs/:r` | `WorkingArea` (RunColumn, ReviewPane, RunEvidence, PresenceAvatars, PlanPin, Composer, InlineApprovals, NextStepBox) | `useRun` + event stream WS | LoadingState, ErrorState, signed-out; per-pane states | Button + workspace components | `.run-*`, `.ws-*`, `.composer`, `.plan-pin`, panes | The three-pane workspace; E2–E5 centre |
| 13 | `…/runs/:r/checkpoints` | `CheckpointsPanel` | checkpoints API | panel-internal | (panel styles) | `.checkpoints-*` | Thin route |
| 14 | `…/runs/:r/report` | page + `DiffPane`, `RunEvidence` | `useRun` + verdict facts | LoadingState, ErrorState | Card | `.report-*`, diff styles | Read-only report (guest target, E7) |
| 15 | `/projects/:p/tree` | `RepoTreeView` | tree API route handler | view-internal | (tree styles) | `.tree-*` | Thin route |
| 16 | `/review` | page + `PrEmptyState` | `getReviewData`, `approvePr`, `requestChanges`, `useEventStream` | loading, PrEmptyState, errors | Button, Card, Pill, Notice | `.review-*` | Pre-R4 page; duplicates pane review, still reachable |
| 17 | `/runs` | `RunsTable` | `listRuns`, `listProjects` | LoadingState, EmptyState, ErrorState | Table, Pill, States | `.runs-*`, tables | Footer count fixed R0; client-side search over loaded pages (§6 gap) |
| 18 | `/dashboard/cost` | page + `costApi` | `listProjects` + cost endpoints | loading, empty chart, error | Button, Card, Pill, Table | `.cost-*` | One headline number; by-day chart only with data |
| 19 | `/settings` | `app/settings/page.tsx` | `connectGithub`, `getGithubConnection` | loading, error, connected | Card | `.settings-*` | Overview + GitHub connect |
| 20 | `/settings/account` | `ErasureCard` | `runErasure` | confirm flow in card | Card | `.settings-*` | Data-erasure request |
| 21 | `/settings/billing` | page + `billingApi` | billing endpoints | plan + upsell cards | Notice | `.settings-*` | testids `billing-plan`, `billing-upsell` |
| 22 | `/settings/governance` | page + `governanceApi` | governance endpoints | loading, form states | Notice | `.settings-*` | Largest settings page (474 ln) |
| 23 | `/settings/integrations` | page + `integrationsApi` | integrations endpoints | form + secret reveal | Notice | `.settings-*` | testid `signing-secret` |
| 24 | `/settings/keys` | page + `keysApi` | keys + `fetchModelCatalog` | mint/revoke flows | Notice, Pill | `.settings-*` | "team key set" mark limited (§6 gap) |
| 25 | `/settings/models` | `ModelPrefsForm` | model prefs + catalog | form states | (panel styles) | `.settings-*` | Thin over ModelPrefs |
| 26 | `/settings/notifications` | `PrefsScreen` | notification prefs | form states | (prefs styles) | `.settings-*` | Thin |
| 27 | `/settings/project` | `RulesEditor` | project rules API | editor states | Notice | `.settings-*` | Thin |
| 28 | `/settings/team` | page + `teamApi` | team endpoints + `runOptimistic` | ErrorState, optimistic rows | Table, Pill, Notice | `.settings-*` | Only optimistic-hook consumer |
| 29 | `/triage` | `PhoneTriage` | pending plans (plan id + goal) | empty; per-item disabled | (phone styles) | `.triage-*` | Phone inbox |

## Totals

- 29 routes: 18 rendered inside AppShell chrome; 3 marketing/static (`/`, `/pricing`, `/docs`); 2 auth (`/login`, `/auth/sso/callback`); 1 standalone action (unsubscribe); plus demo/portal/triage/onboarding/meeting/cost/runs/review.
- Full loading+empty+error designed on 6 routes (portal, runs, run page, report, onboarding, cost); ~13 routes have no explicit empty/loading design (thin delegating pages, settings forms).
- Primitives: interactive routes use the hand-written `ui/*` set (Button 21 routes, Notice 12, Card 9, Pill 6, Table 3; Modal selective). No Radix/shadcn anywhere yet — E1 scope.
- `globals.css` is 1,919 lines serving all 29 routes. E1 deletes per migrated page.

## Why the "no previewData in shipped routes" guard needed a split, not a ban

`src/components/workspace/previewData.ts` (175 ln) contains **no sample data** — it is pure grouping/diff-parsing helpers misnamed from the R1 preview era, and shipped routes import from it: `runRowTitle` (run page, report, portal, AppShell), `parseUnifiedDiff`/`fileStatLabel` (DiffPane), `groupRuns` (useRunList). E0 splits it: pure functions move to `runRows.ts` and `diffParse.ts`; `previewData.ts` remains only as a test-only barrel; a vitest guard fails if any `app/**` page or non-test `src/**` module imports `previewData`.
