All evidence gathered and verified against source. Here is the audit report.

---

# Audit Wave 2 — Product & UX Domains (Areas 22–28), `section7`

Scope: `/Users/user/untitled folder 2/harness/section7` (~5.3K LOC, 10 routes, 7 components). Read-only analysis; every claim verified against source. Findings numbered continuously; `[area]` tag per line.

## Core-journey measurement (login → merged PR)

Measured happy path: login (1 form) → Command Center → Meeting → *Start a live meeting* → type message → **Send @ai** → *Generate brief* → *Approve brief* → navigate to Working Area → … **dead end**: the "propose the plan" step the approval note names exists nowhere (PlanPanel is read-only, no propose/approve/start controls in the entire codebase). Even past that (backend-initiated plans), the Review page loads nothing unless the user hand-edits `localStorage.harness.prId`. Total: **~9 clicks across 3 pages, 2 circular pointers, 1 devtools edit**, and the Working Area page renders **without the app shell** (no nav, no sign-out) so leaving it requires browser-back.

## Findings

**[22] USABILITY**
1. [usability] Brief-approved note says "head to the Working Area to propose the plan" but no propose/start-plan button exists anywhere (grep: phrase only appears as text) → add explicit Propose-plan action or rewrite the note to what actually triggers planning.
2. [usability] PlanPanel is purely presentational — the mandatory human plan-approval gate has no approve/reject control in the UI → add approve/request-changes buttons wired to the orchestrator.
3. [usability] Review page reads `harness.prId` from localStorage and its own error says "set harness.prId first" — a devtools requirement shipped to users → add a PR list/picker fed by S8.
4. [usability] Nothing in the app ever calls `setPrId()` — the Review page is unreachable-by-navigation by construction → link PRs from runs (`pr_opened`) into Review.
5. [usability] Runs table rows aren't links to `/projects/{pid}/runs/{rid}` — users hand-assemble URLs → wrap run id in `<Link>`.
6. [usability] WorkingArea passes hardcoded `results={[]}` to TerminalPanel (line 67) — the entire structured test-results branch is dead code → feed exec-result events or remove the prop.
7. [usability] Comments & nudges panel is local-componentState only (its own subtitle admits it) — "redirect the agent" does nothing → persist via S6/S4 endpoint or label as scratchpad.
8. [usability] Checking "Human takeover" immediately POSTs a human-edit with path `"(unspecified)"` before the user types anything (Controls.tsx 53–58) → defer submission to explicit Save.
9. [usability] "Done, continue" submits `editContent` which is `""` unless the takeover editor was opened — done-signal ships an empty diff → require non-empty diff or pull it from the run.
10. [usability] Command Center empty state defers to "Working Area after approving a plan" — a second dead end chained to the first → make CC offer the next concrete action.
11. [usability] Meeting page keeps `meetingId` in component state only — refresh orphans the transcript and mints a new meeting on next send → persist meeting id (URL param/storage) + meeting list.
12. [usability] Empty chat draft auto-sends the hidden default `"Please review the plan."` (meeting `send()`) — accidental sends post unintended content → disable send on empty input.
13. [usability] Login fabricates a run id (`run-${Date.now(36)`}) and defaults project to `proj-test-1` — session opens pointing at resources that don't exist → start with honest null state + creation flow.
14. [usability] Sidebar project switcher writes storage but doesn't refetch/navigate the current page — views keep the previous project's data → reload view on switch.
15. [usability] Approved brief writes goal to localStorage (`setGoal`) — never displayed or transmitted anywhere; write-only field → show it in Working Area / drop it.
16. [usability] Each view fires 3 separate REST calls (status, plan, event-replay) — no aggregate endpoint → batched summary endpoint or derive more from the WS replay.
17. [usability] Every WS event triggers a full status+plan re-pull (WorkingArea 50–53) — chatty runs cause N×REST hammering → debounce or refetch only on relevant event types.
18. [usability] DiffPanel injects a multi-paragraph placeholder essay as CodeMirror content — indistinguishable from a real diff at a glance → render placeholders outside the editor surface.
19. [usability] Client duplicates risky-file detection with its own hardcoded `RISKY_FILES` list alongside server risk_flags — two sources of truth → rely on S8 flags, show client heuristic only as fallback with labeling.
20. [usability] `signalDone` returns reviewer findings that Controls discards — user waits blind for the WS echo instead of seeing instant verdict → render returned findings inline.
21. [usability] Working Area page renders **without AppShell** — no sidebar, project switcher, sign-out, or any exit navigation on the product's core page → wrap in AppShell.

**[23] ONBOARDING FRICTION**
22. [onboarding] Onboarding page is orphaned from nav — `AREAS` omits it; only entry is a text link buried in Settings' link list → add nav item (or first-login redirect).
23. [onboarding] Checklist items never auto-complete — no section reports completion when the user actually connects GitHub/creates a team → wire completion events from real actions.
24. [onboarding] Checklist steps have no action links — "Connect GitHub (Section 2)" describes but doesn't link to /settings → per-step deep links.
25. [onboarding] `src/lib/onboarding.ts` (guided demo, security-checklist acknowledge, telemetry prefs — 137 lines) has **zero importers**; a built first-run demo flow is unreachable → route a page to it or delete.
26. [onboarding] Post-login landing has no guided flow: possible "(no projects yet)" dropdown, no CTA, fabricated run context → add a first-run "create project/goal" wizard.
27. [onboarding] Every empty state lacks a create-CTA: "No runs match", "No provider keys yet.", "No members loaded.", "No events yet.", "No spend recorded", "No plan proposed yet" → add action buttons to each.
28. [onboarding] AppShell silently selects `items[0]` as active project — new users operate in an unnamed context highlighted only by a small dropdown → show current project prominently in the header.
29. [onboarding] Onboarding copy says Section 6 "not yet deployed" while the nav ships a live Meeting area — contradictory guidance → sync copy with deployment reality.

**[24] ACCESSIBILITY**
30. [a11y] No skip-to-content link; sidebar precedes `<main>` on every page → add visually-hidden skip anchor.
31. [a11y] Zero `aria-live` regions in the whole app (verified) — chat replies, status changes, blockers, disconnects are never announced → `aria-live="polite"` on chat/status/connection containers.
32. [a11y] Run-state pills encode meaning in color alone (green/yellow/red/blue classes) at 11px → add state icons or prefixes; enlarge hit/text size.
33. [a11y] Muted palette fails contrast: `#6b7280` on `#111418` ≈ 4.0:1 and `#8b949e`/`#9aa4b2` used widely for body text → raise muted colors to ≥4.5:1.
34. [a11y] No focus-visible styles defined anywhere (globals.css or inline) — keyboard focus is invisible → global `:focus-visible` outline.
35. [a11y] Login inputs are placeholder-only (email + OTP code, no labels) → add `<label>`/aria-label.
36. [a11y] Meeting chat input likewise unlabeled; message list keyed by index with no `role="log"` → proper roles + accessible name.
37. [a11y] Route changes never move focus — after navigation SR/keyboard users remain anchored on the clicked nav item → focus `h1`/main on pathname change.
38. [a11y] Disabled "Working" nav item communicates only via opacity + `title` tooltip → use real disabled semantics with visible explanation text.
39. [a11y] Busy buttons change label text only ("Signing in…") with no `aria-busy`/live announcement → add aria-busy.
40. [a11y] No `prefers-reduced-motion` handling for progress-bar/meter width transitions → gate transitions behind the media query.
41. [a11y] Onboarding progress bar lacks live announcement on completion changes → aria-live on the progress label.

**[25] CROSS-DEVICE**
42. [cross-device] Fixed 220px `position:fixed` sidebar + `margin-left:220px` content with no breakpoint — on phones the content column is ~40% viewport → collapse sidebar under 768px.
43. [cross-device] **Zero** `@media` queries in globals.css (verified count = 0) — no responsive behavior exists at all → add base breakpoints.
44. [cross-device] WorkingArea hardcodes `gridTemplateColumns: "1fr 1fr"` without wrap — CodeMirror panes squeezed to half-width on mobile → single-column stack under 900px.
45. [cross-device] Touch targets below 44px everywhere: `.btn` padding 6px/14px (~27px tall), selects 6px padding, native-size checkboxes → enforce min-height/size on interactive elements.
46. [cross-device] `.event-type { min-width:190px }` forces horizontal scroll inside the Activity card on narrow screens → allow wrapping.
47. [cross-device] Four data tables (runs/team/keys/cost) have no horizontal-scroll wrapper — wide tables break mobile layout → wrap in `overflow-x:auto`.
48. [cross-device] Long opaque ids (run ids, key hints, invite tokens) lack word-break in table cells → `word-break:break-word` on monospace cells.

**[26] EMPTY / ERROR / LOADING**
49. [empty-error-loading] WS reconnect is a fixed 1.5s loop with no backoff/jitter/cap (useEventStream) — S1 down ⇒ infinite reconnect + ticket-mint storm → exponential backoff with ceiling.
50. [empty-error-loading] Connection loss surfaces only as a tiny dot+label inside StatusPanel — users read stale data unknowingly → prominent banner + stale-data marker.
51. [empty-error-loading] Raw infrastructure errors shown verbatim including "(is S6 up on :8006?)" — ports/internal topology leak into end-user UI → map error codes to friendly copy.
52. [empty-error-loading] Page-level errors on Command Center and Review are bare red `<p>` with no `role="alert"` → convert to alert regions.
53. [empty-error-loading] CC swallows failures in three silent catch blocks and flips its pill to "live" merely because old events replayed → distinguish connected vs. historical data.
54. [empty-error-loading] Review page swallows `getReviewData` failure into the same empty state as "no PR" — S8 outage is indistinguishable from no-PR → separate error vs. empty states.
55. [empty-error-loading] No fetch timeout/AbortController anywhere — hung backends leave busy states forever → add timeouts with retry messaging.
56. [empty-error-loading] Settings page funnels OAuth `?error=` and transient fetch failures into one error slot — a failed connection-status GET masquerades as failed OAuth → separate concerns.
57. [empty-error-loading] Runs page has no loading affordance beyond `aria-busy` + disabled Refresh — no skeleton/spinner → visible pending state.
58. [empty-error-loading] Brief generation gives zero progress indication (static label, disabled button) while the model call runs → busy label/spinner.

**[27] SEARCH & NAVIGATION**
59. [search-nav] No search exists over runs, meetings, messages, or events — zero search inputs in the codebase → at minimum run-id/goal search on Runs.
60. [search-nav] Runs filtering is limited to project/status/page-size — no date range, text match, or sort control → extend filter bar.
61. [search-nav] No breadcrumbs on settings subpages — "Team"/"Provider keys"/"Billing" pages have no visible parent trail → breadcrumb back to Settings.
62. [search-nav] All three settings subpages pass `active="/settings"`, so the sidebar highlights "Settings" with no indication of which subpage you're on → sub-nav or distinct active mapping.
63. [search-nav] `/onboarding` passes `active="/onboarding"` matching no nav item — sidebar shows nothing selected → add nav entry (see #22).
64. [search-nav] Team switcher is a decorative fake: hardcoded single option "My team", `defaultValue="solo"` → hide until multi-team exists.
65. [search-nav] Sidebar brand "HARNESS" is a `<div>` not a link — no home escape hatch → make it a Link.
66. [search-nav] Working Area's only inbound link is localStorage-derived (`workingHref`); deep links exist but are undiscoverable and the page itself has no outbound nav (#21) → consistent shell + linked run history.

**[28] NOTIFICATIONS**
67. [notifications] No notification system of any kind — run completion/failure/blocking produce no email, push, or in-app alert (S1 notifier.py covers invites+OTP only) → add run-lifecycle notifications.
68. [notifications] Blocked-runs-awaiting-clarification — the highest-stakes human gate — are completely silent; the run sits until someone happens to look → priority alert channel.
69. [notifications] No notification preference center anywhere in Settings → add a preferences section when notifications land.
70. [notifications] No in-app awareness when tabbed away: `document.title` never changes, no favicon badge, no toasts for WS events arriving on other pages → title flash + toast layer.
71. [notifications] Meeting page never learns about @ai replies — messages fetched only after the next send; the agent's answer appears solely as a side effect of more user activity → poll/WS the transcript.

## Cross-cutting themes
- **The product's spine is unwalkable:** brief→plan→PR has two circular pointers (#1/#2) and one devtools-only hop (#3); everything downstream of "Approve brief" relies on knowledge of internal APIs.
- **Honesty over polish was chosen consistently** (panels admit their emptiness in prose) — good instinct, but the copy is doing the work that affordances should.
- **Accessibility debt is structural, not cosmetic:** no live regions, no focus styles, no labels, no breakpoints — a single shared primitives pass (shell, buttons, inputs, pills) would fix ~15 findings at once.

No files were created or modified; repository untouched.