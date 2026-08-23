All six Mission‑10 surfaces walked (plus AppShell, lib/api, and the four local API clients). Audit below — 20 findings, each with file:line, severity, scenario, and fix.

# Mission‑10 New‑Surface UX Audit — `/settings/keys`, `/settings/billing`, `/dashboard/cost`, `/runs`, `/onboarding`, `/settings/team`

**Verdict:** Solid contract-honest foundations (real error envelopes, honest empty states, once-only secrets), but the surfaces are *disconnected*: nothing links to anything, most pages have exactly one shot at loading, and two flagship pages (Runs, Cost) dead-end for a brand-new user.

---

## HIGH

### H1. Runs table rows do nothing — no drilldown to the run detail page that already exists
- **Where:** `app/runs/page.tsx:257–277` (plain `<tr>`, no onClick/Link); detail route exists at `app/projects/[projectId]/runs/[runId]/page.tsx:1–10`
- **Scenario:** New user opens Runs, sees their first run `a1b2c3d4…` in `executing`. They click it. Nothing. The data needed to navigate (`r.projectId`, `r.id`) is sitting in the row and the destination page is already built (WorkingArea). Users will assume the app is broken.
- **Fix:** Wrap run cell in `<Link href={`/projects/${r.projectId}/runs/${r.id}`}>`; make the whole row clickable (`onClick` + `cursor:pointer` + keyboard: `tabIndex=0`/Enter). Keep the id short-text but make it obviously a link (accent color).

### H2. Team page has no member removal and no role change — roster is read-only decoration
- **Where:** `app/settings/team/page.tsx:153–184` (members table: Name/Email/Type/Role, zero actions column); `app/settings/team/teamApi.ts:33–97` (only list/mint-invite/revoke-invite/redeem exist)
- **Scenario:** Admin invited someone with the wrong role (`guest` instead of `engineer`). The person redeemed. There is no way to fix it from the UI — ever. Offboarding a departed teammate is impossible too. The page's title promises "roster **with roles**"; it delivers display only.
- **Fix:** Add Actions column (admin-only): role `<select>` → `PATCH /api/v1/teams/{id}/members/{memberId}` and Remove → `DELETE` (with the same inline confirm pattern keys page already uses at `keys/page.tsx:273–293`). Guard last-admin removal client-side with a clear message.

### H3. Cost dashboard empty state will read as broken for real users
- **Where:** `app/dashboard/cost/page.tsx:176–178` (`<p>No spend recorded in this period.</p>` — one muted line); `costApi.ts:80–88`
- **Scenario:** Known context: `/spend` returns zeros until metering is wired to dsh runs. A real user who just finished a guided-demo run lands here and sees "$0.0000 … No spend recorded." That is indistinguishable from "the app ignored everything you did," and it's a single gray sentence where the rest of the viewport is blank. It fails the "does it feel broken?" bar.
- **Fix:** Upgrade to a proper empty-state card: (a) headline "No spend recorded in the last N days"; (b) explicit two-cause copy — "either no model calls ran, or this deployment isn't metering dsh runs yet"; (c) CTA button → `/` (Command Center) to start a run, secondary link → `/onboarding` guided demo; (d) keep the existing yellow 503 "metering not wired" notice distinct (that part is genuinely good).

---

## MEDIUM

### M1. Onboarding checklist goes stale: no auto-complete, no polling, no per-step actions
- **Where:** `app/onboarding/page.tsx:42–44` (fetch once on mount), `:80–89` (manual Refresh is the only update path), `:126–158` (items render title/description only — no link to *do* the step)
- **Scenario:** User completes GitHub connect over in `/settings`, comes back to onboarding: still ⬜ until they think to hit Refresh. Worse, the step that says "Connect GitHub" doesn't link anywhere — the checklist describes work instead of enabling it.
- **Fix:** Poll `getChecklist()` every ~15s while visible (or refetch on window focus). Make each item's title a deep link to where the step happens (`/settings` for GitHub, `/settings/team` for invites, `/` for demo). Show a subtle "updated just now" timestamp so staleness is visible.

### M2. Onboarding is an orphan page — absent from nav, nothing highlights, no way back
- **Where:** `src/components/AppShell.tsx:10–17` (`AREAS` has no `/onboarding`; `active="/onboarding"` matches nothing); `app/settings/page.tsx:72` is the only inbound link; `app/onboarding/page.tsx:9` even admits "Nav link … wired by ATLAS at merge" — it wasn't
- **Scenario:** First-run user finishes login, lands on `/`. The single most important page for them is unreachable unless they happen to open Settings and read the link list. From onboarding, the sidebar highlights nothing.
- **Fix:** Add `{ href: "/onboarding", label: "Get started" }` to `AREAS` (or pin a dismissible banner on `/` while the checklist is incomplete). Until then, at least pass `active="/settings"` so something highlights.

### M3. No breadcrumbs/back links on any settings subpage
- **Where:** `app/settings/team/page.tsx:139–140`, `app/settings/keys/page.tsx:133–134`, `app/settings/billing/page.tsx:93–94` (all just `<h1>`), `app/onboarding/page.tsx:51–52`
- **Scenario:** User deep-links (or is invited) straight to `/settings/billing`. Sidebar shows "Settings" highlighted but there's no visible path back to the settings index or sibling pages; browser Back is the only exit.
- **Fix:** One shared `<Breadcrumbs items={[["Settings","/settings"],["Billing"]]} />` under the `<h1>` on all four subpages. Cheap, consistent, fixes orientation everywhere.

### M4. Four different date formats across six pages
- **Where:** `app/settings/team/page.tsx:263` `new Date(...).toLocaleString()` (locale-dependent); `app/settings/keys/page.tsx:44–47` own `fmtDate` → `toLocaleString()`; `app/runs/page.tsx:268–275` + `runsApi.ts:123–136` relative-only ("4m ago") with absolute buried in `title`; `app/dashboard/cost/page.tsx:281` raw ISO `2026-08-21` (and `costApi.ts:27` allows literal `"unknown"` as a day value)
- **Scenario:** Same user, one session: invites expire "8/23/2026, 4:00 PM", keys created "8/23/2026, 3:12 PM", runs happened "4m ago", spend was on "2026-08-21". Feels like four apps.
- **Fix:** One `formatDateTime`/`formatDay` util in `src/lib` (e.g. `Aug 23, 16:04` + ISO in `title`) used everywhere; keep runs' relative time but append absolute on hover *visibly* (tooltip already exists — fine) and standardize cost table through `formatDay`, dropping `"unknown"` rows into a labeled "(untimestamped)" group.

### M5. No toast/notification system; inline messages fight each other and drift stylistically
- **Where:** Every page hand-rolls `notice`/`error` divs; `app/settings/team/page.tsx:147` actively *hides* the success notice whenever any error appears (`{!error && notice && …}`) — mint an invite then fail a revoke and your "share this token now" context is gone; `app/settings/page.tsx:56–57` hardcodes hex colors (`#34d399`, `#f87171`) outside the theme-var system every Mission‑10 page uses
- **Scenario:** Success feedback lives until the next action randomly clears it, or vanishes because an unrelated call failed. There's no transient confirmation pattern anywhere, so pages invent their own.
- **Fix:** Introduce a minimal toast store (context + `role="status"` stack, auto-dismiss 5s, errors sticky) and migrate the five pages; keep destructive-action results inline. Delete the hardcoded hexes in favor of `var(--green)`/`var(--red)`.

### M6. Errors are raw strings with no retry, and 401 doesn't route to login
- **Where:** `app/settings/billing/page.tsx:61,75`, `app/settings/team/page.tsx:73,92,107`, `app/settings/keys/page.tsx:86,110,126`, `app/dashboard/cost/page.tsx:77` — all `String((e as Error)?.message ?? e)`; no retry affordance on billing/team/keys (runs has Refresh `:222–231`, onboarding has Refresh, cost recovers via range change); `src/lib/api.ts:37–67` produces messages like `Request failed with status 500` / browser `Failed to fetch` (`keys/page.tsx:110` on network down)
- **Scenario:** Identity service restarts while a user is on `/settings/team`: red pill says "Failed to fetch", and their only recovery is a full page reload. If the token expired, every page just shows the raw 401 message forever — no "session expired, log in".
- **Fix:** Shared `<ErrorState onRetry>` component wired to each page's existing refresh fn; special-case `ApiError.status === 401` globally (in AppShell or a wrapper) to clear session and `router.push("/login?expired=1")`.

### M7. Members table conflates loading, empty, and failed — "No members loaded." during in-flight request
- **Where:** `app/settings/team/page.tsx:175–181` (renders whenever `members.length===0`, including before first resolve); contrast the correct pattern one lane over: `app/settings/keys/page.tsx:310–315` (`loaded ? "No provider keys yet." : "Loading…"`), and runs' guarded empty rows `app/runs/page.tsx:279–285`
- **Scenario:** Slow identity service: new admin sees "No members loaded." for seconds, concludes the roster is empty or the feature broke, and emails the operator.
- **Fix:** Copy keys page: add a `loaded` flag; show skeleton/loading row until resolved, distinct "This team has no members yet — invite someone above ↓" with anchor link when genuinely empty.

### M8. Keys list: no pagination, no search/filter, unbounded table
- **Where:** `app/settings/keys/page.tsx:236–318`; `app/settings/keys/keysApi.ts:70–74` (`listKeys` accepts only `teamId` — no limit/offset/query params)
- **Scenario:** A team accumulating provider keys over months (including soft-revoked rows kept "for audit") eventually renders hundreds of rows; finding a specific revoked key means scrolling and eyeballing. Revoked and active rows aren't even separable.
- **Fix:** Minimum viable: client-side filter input (label/provider) + "Hide revoked" toggle + status filter. Proper: `limit/offset` params mirroring the runs contract (`runsApi.ts:63–81` is the in-repo template).

### M9. Team members & invites: no pagination, no search, no sorting
- **Where:** `app/settings/team/page.tsx:155–183` (members), `:247–285` (invites); `teamApi.ts:58–67` (both list endpoints return unbounded `items`)
- **Scenario:** Same growth problem as keys; additionally there's no way to find "is bob@… invited?" beyond scanning, and expired invites are indistinguishable from live ones (see M10).
- **Fix:** Search input filtering email/displayName client-side; paginate at ≥50 rows; sort invites by `expiresAt`.

---

## LOW

### L1. Single shared `busy` flag freezes unrelated forms; submit buttons give no in-flight feedback
- **Where:** `app/settings/team/page.tsx:45,223,268,303` — one `busy` gates Create-invite + every Revoke + Redeem simultaneously; `app/settings/keys/page.tsx:209` and `billing/page.tsx:200` — buttons disable but label never changes (contrast onboarding's good `"Refreshing…"` at `onboarding/page.tsx:88`)
- **Scenario:** Contrary to the rumor mill, submits **do** disable while pending — but a slow invite mint also locks the redeem box, and a user can't tell "Add key" is working versus frozen, so they click elsewhere or reload mid-write.
- **Fix:** Per-form busy state (`inviteBusy`, `redeemBusy`, per-row revoking id); label swap ("Adding…", "Applying…") while in flight.

### L2. Once-only secrets have poor capture ergonomics; keys comment contradicts its own code
- **Where:** `app/settings/keys/page.tsx:214–230` and `app/settings/team/page.tsx:228–244` — hint/token shown as bare `<code>`, no copy-to-clipboard button, gone forever on navigation/reload; `keys/page.tsx:65–66` comment claims the hint is cleared "when the next add **or revoke** starts" but `onRevoke` (`:116–130`) never calls `setMintedHint(null)` — doc drift
- **Scenario:** Admin mints an invite token, gets interrupted, switches tabs to paste it into Slack — comes back, block is still there (fine), but one accidental reload and the token is unrecoverable; they must revoke and re-invite. No copy button invites manual-selection errors on long tokens.
- **Fix:** Add a "Copy" button (`navigator.clipboard.writeText` + "Copied ✓" state) beside both blocks; add "Done — hide this" dismissal; fix the comment or the code.

### L3. No bulk actions on keys or invites
- **Where:** `app/settings/keys/page.tsx:269–306` (per-row Revoke only), `app/settings/team/page.tsx:264–275` (per-row Revoke only)
- **Scenario:** Rotating a leaked provider means revoking several keys one confirm-dialog at a time; cleaning out a batch of stale invites likewise.
- **Fix:** Row checkboxes + "Revoke selected (n)" appearing in an action bar, reusing the same confirm pattern. Low priority until lists grow (depends on M8/M9).

### L4. Loading states are bare text; layout pops when data arrives
- **Where:** `app/settings/billing/page.tsx:96–98` ("Loading billing state…" paragraph), `app/dashboard/cost/page.tsx:144–148` (inline "Loading…" span, then three stat cards + two tables pop in), `app/settings/keys/page.tsx:313`, `app/settings/page.tsx:85,127`
- **Scenario:** First paint on cost is an empty viewport with a word; then the whole dashboard snaps in — noticeable jank and momentary "is anything happening?" doubt.
- **Fix:** Gray placeholder boxes matching final card/table geometry (pure CSS pulse; no library needed). At minimum reserve min-heights on the cost cards.

### L5. Empty states mostly lack CTAs to where the data comes from
- **Where:** `app/settings/keys/page.tsx:313` "No provider keys yet." (no pointer to the add form 90px above or to provider docs); `app/runs/page.tsx:279–285` "No runs match the current filters." (zero-data new user gets no hint a run is started from Command Center); `app/settings/billing/page.tsx:111–123` upsell correctly names `HARNESS_BILLING` but gives no docs link and no "check again" after the operator flips the flag (state fetched once at `:52–62`)
- **Fix:** Each empty state gets one verb-link: keys → scroll-to/add-form anchor; runs → "Start your first run →" `/`; billing → docs link + a "Re-check" button calling `refresh()`.

### L6. Cost page: no export, unbounded by-day table, unlabeled totals period
- **Where:** `app/dashboard/cost/page.tsx:180–289` — no CSV/JSON download despite perfectly tabular `byModel`/`byDay` data already in state; `:279–286` renders all 90 rows at "Last 90 days"; total cards `:182–195` don't restate the selected period
- **Scenario:** Manager wants monthly spend for finance: copy-pasting an HTML table is the only option. Scrolling 90 rows to reach the pager-less end is tedious.
- **Fix:** "Export CSV" button serializing `summary` (10 lines of code, data's already client-side); cap table with "showing first N days" collapse; put "· last 30 days" in each card title.

### L7. Onboarding a11y edge: progressbar declares `aria-valuemax=0` on an empty checklist
- **Where:** `app/onboarding/page.tsx:98–111` with `total = items?.length ?? 0` (`:47`) — when the backend seeds late, ARIA gets `valuemin=0 valuemax=0 valuenow=0` (invalid), plus the label reads "0/0 steps complete"
- **Fix:** Render the meter only when `total > 0`; otherwise show the existing seeding copy in its place.

### L8. Billing admin check reads localStorage during render; flag-off card is fully static
- **Where:** `app/settings/billing/page.tsx:88` `isAdmin = loadSession()?.principal.role === "admin"` executed in the render body (localStorage access per render — hydration-flicker risk and inconsistent with the effect-based reads elsewhere, cf. `:56–62`); combined with L5, a freshly-enabled flag requires a manual full reload to appear
- **Fix:** Move role into state set inside the mount effect; add a lightweight poll (60s) or manual "Refresh status" on the upsell card.

---

## What's already good (keep)
- Honest 503-vs-error split on cost (`cost/page.tsx:71–81`), shape-guarded runs client (`runsApi.ts:107–119`), clamped quota meter with proper `aria-valuetext` (`billing/page.tsx:141–160`), inline revoke-confirm on keys (`keys/page.tsx:273–293`), once-only secret semantics, and the ticking relative-clock on runs (`runs/page.tsx:73–77`). The audit rumors that "submits don't disable" and "status enum drifted" are both false in the current tree.

**Priority order if fixing top-down:** H1 → H2 → H3 → M1 → M2 → M7 → M6 → rest.