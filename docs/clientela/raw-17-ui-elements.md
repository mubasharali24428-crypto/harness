# Sweep A — UI Element Findings (attempt 5)

Pure expansion of the seeded issues into element-level findings. **92 findings across 12 page groups** (target 85+). Format: `[PAGE] [element] [lens] issue → fix`. No repo modifications — analysis only.

## LOGIN (7)

- [LOGIN] [project-id input] [correctness] Accepts any locally-minted projectId that was never issued by the backend → validate the ID against the projects API before navigating; reject unknown IDs with an inline error.
- [LOGIN] [run-id input] [correctness] runId is fabricated client-side, so deep links point at runs that don't exist → populate runId from a real runs lookup scoped to the selected project.
- [LOGIN] [error banner] [ux] Wrong-email and network-failure surface identical generic copy → map API error codes to distinct messages ("No account for this email" vs "Can't reach the server").
- [LOGIN] [error banner] [a11y] Failure text is neither announced nor associated with its field → role="alert" on the banner + aria-describedby linking errors to inputs.
- [LOGIN] [password field] [ux] Pressing Enter does nothing; users must mouse-click Sign In → wrap fields in a `<form>` with onSubmit handling.
- [LOGIN] [otp resend button] [ux] Resend is immediately spam-clickable with no cooldown signal → 30–60s disabled countdown ("Resend in 30s").
- [LOGIN] [otp input] [a11y] Missing autocomplete="one-time-code", so SMS autofill never triggers → add the attribute plus inputmode="numeric".

## ROOT (10)

- [ROOT] [live pill] [truth-in-ui] Pill flips to "live" after a single successful REST poll and stays green while data goes stale → drive the pill from a real WS/SSE connection state machine (connecting / live / reconnecting / offline).
- [ROOT] [data feed] [perf] View relies on one-shot polling instead of a stream → subscribe via WebSocket/SSE with server push and automatic resubscribe on drop.
- [ROOT] [dashboard assembly] [perf] Three sequential API calls stitched per view multiply latency and partial-failure risk → single aggregated endpoint, or parallel fetches combined with Promise.allSettled.
- [ROOT] [dashboard sections] [resilience] One failed call strands sibling widgets showing mixed old/new data → per-section error + retry state instead of blending outputs.
- [ROOT] [pointer help text] [copy] Caption restates itself circularly ("this panel shows panel info") → rewrite as a concrete outcome ("Shows the last 24h of agent activity").
- [ROOT] [activity stream] [empty-state] Empty list renders as bare whitespace with no explanation → "No activity yet — launch your first run" with a CTA to the runner.
- [ROOT] [activity stream] [feedback] No loading state before the first events arrive, compounding the broken feel → skeleton rows during initial fetch.
- [ROOT] [est-cost chip] [trust] Shows "$0.000" before metering data exists, reading as broken or as "it's free" → render "—" with tooltip "Cost appears after first billed usage".
- [ROOT] [plan-approval row] [ux] Row reads "pending" before any plan exists, implying a stuck approval → hide/disable the row with "No plan yet" until a plan artifact is present.
- [ROOT] [plan-approval row] [copy] "Pending" is ambiguous between awaiting-user and awaiting-system → split into "Waiting for you" vs "Processing".

## MEETING (10)

- [MEETING] [@ai echo bubble] [ux] Assistant replies echo the user's own text verbatim, looking like a broken bot → show a pending "thinking…" bubble that swaps to the real response.
- [MEETING] [ai response pipeline] [correctness] Echo path ships raw prompt text as assistant content → delete the echo branch; render model output only.
- [MEETING] [invoke-ai handler] [correctness] Duplicate invokeAi fires two identical generations per send → disable send while in-flight and add a server-side idempotency key.
- [MEETING] [transcript] [persistence] Entire meeting is lost on refresh because state lives only in memory → autosave the transcript (server or localStorage) and hydrate on mount.
- [MEETING] [post-refresh state] [ux] After reload there's no notice that prior history existed → "Restore last meeting?" prompt with timestamp.
- [MEETING] [participant list] [collab] No presence indicators; impossible to tell who's connected → presence dots fed by WS membership events.
- [MEETING] [composer] [collab] No typing indicator, so turns collide blindly → broadcast throttled typing events to other participants.
- [MEETING] [message list] [perf] Every message ever sent renders into the DOM (unbounded) → virtualize the list with windowed rendering + sticky scroll-to-bottom.
- [MEETING] [message list] [perf] Long sessions grow node count until scrolling janks → lazy-load older history above the viewport.
- [MEETING] [send button] [feedback] No visual pending state between send and AI reply → spinner/disabled affordance tied to the request lifecycle.

## REVIEW (6)

- [REVIEW] [pr-id input] [usability] Review pane is dead unless the user hand-edits prId in code/state → PR picker listing open PRs, auto-seeded from route/context.
- [REVIEW] [review pane] [empty-state] Cryptic blank surface when no prId is present → explainer "Select a pull request to review" with the picker inline.
- [REVIEW] [request-changes button] [validation] Submits with an empty comment body, producing noise reviews → block submit until a non-empty summary exists; inline counter/error.
- [REVIEW] [comment composer] [ux] No indication of why submit did nothing when the comment is empty → helper text "Add a summary (required)" bound to button state.
- [REVIEW] [approve button] [correctness] Enabled before the diff finishes loading, permitting blind approvals → gate on diff-fetch success; disabled label "Loading diff…".
- [REVIEW] [diff viewer] [feedback] No distinction between an unloaded diff and a genuinely empty one → skeleton while fetching, then explicit "Diff failed to load — Retry".

## RUNS (8)

- [RUNS] [run row] [affordance] Rows visually suggest links but aren't clickable despite a detail route existing → onClick navigation + cursor-pointer + hover state.
- [RUNS] [run row] [a11y] No keyboard path into run details → focusable rows (tabindex=0, role="link") with Enter activation.
- [RUNS] [relative-time cell] [perf] Per-tick updates re-render the whole table ("2m ago" churn) → memoize cells; advance timestamps on one coarse shared interval.
- [RUNS] [clock source] [arch] Each row owns its own timer logic → single ticker in the parent passed via context; rows stay pure.
- [RUNS] [created column] [consistency] Raw UTC ISO strings clash with localized dates elsewhere in the app → Intl.DateTimeFormat with user locale/timezone everywhere.
- [RUNS] [timestamp hover] [ux] No way to see the exact absolute time behind relative stamps → title/tooltip carrying the full localized datetime.
- [RUNS] [status filter] [ux] Dropdown lists bare statuses with no sense of volume → append counts ("Running (3) · Failed (12)").
- [RUNS] [filter state] [persistence] Selected status resets on reload and isn't shareable → mirror the filter into URL query params.

## ONBOARDING (8)

- [ONBOARDING] [nav entry] [discovery] Page is orphaned — unreachable from sidebar or root → add a "Get started" nav entry plus a dismissible root banner linking to it.
- [ONBOARDING] [deep link] [discovery] Users who finish setup never learn the page exists → surface the checklist on first login and link it from relevant empty states.
- [ONBOARDING] [checklist items] [logic] Items never auto-complete even after the underlying action succeeds → subscribe to completion events (first run, first key, …) and tick automatically.
- [ONBOARDING] [checklist state] [persistence] Progress resets because completion lives in component state → persist completed flags server-side per user.
- [ONBOARDING] [manual checkbox] [trust] Items can be hand-checked without doing the work, defeating the checklist's purpose → remove the manual toggle; verification-driven only.
- [ONBOARDING] [step cards] [cta] Steps describe tasks without any action link → per-step primary button ("Create a key →") routing straight to the feature.
- [ONBOARDING] [completed checklist] [lifecycle] A fully-completed checklist lingers unchanged, reading stale/broken → collapse to "All set ✓" or auto-hide with undo.
- [ONBOARDING] [stale banner] [lifecycle] Completed steps keep pre-completion copy ("Set up your key") → per-item past-tense done state.

## SETTINGS / TEAM (7)

- [SETTINGS] [page header] [navigation] No breadcrumb trail; users lose orientation in nested settings → "Settings › Team" breadcrumb with working parent links.
- [SETTINGS] [mobile header] [navigation] No back affordance on small screens where the sidebar is hidden → back arrow to the settings index below 768px.
- [TEAM] [member row] [functionality] Roster is read-only — members can't be removed → row overflow menu with Remove, gated to owners/admins.
- [TEAM] [role select] [functionality] Roles can't be changed after invite → inline role dropdown with permission-guarded save.
- [TEAM] [remove flow] [safety] Removal would have no confirmation step → confirm dialog naming the member and the consequences.
- [TEAM] [invite copy button] [feedback] Copying the invite link gives zero confirmation → transient "Copied ✓" swap or toast.
- [TEAM] [invite copy button] [a11y] Clipboard feedback must be perceivable without sight → announce via an aria-live="polite" region.

## KEYS (6)

- [KEYS] [secret hint] [disclosure] One-time secret hint appears once with no prior warning that it will vanish → interstitial modal "You'll see this once — copy it now" before reveal.
- [KEYS] [secret reveal] [feedback] No copy-to-clipboard paired with the one-shot value → auto-copy on reveal plus a "Copied ✓" confirmation.
- [KEYS] [masked key value] [usability] Existing keys expose neither prefix nor metadata to tell them apart → show `sk-…last4`, created date, and last-used.
- [KEYS] [revoke confirm] [safety] Revocation is instant and irreversible with no undo → soft-revoke with a 30-day grace period or a timed undo toast.
- [KEYS] [revoke dialog] [copy] Dialog doesn't state consequences or permanence → explicit copy: "Apps using this key stop working immediately."
- [KEYS] [key list] [scalability] Unbounded table renders every key ever issued → pagination + search/filter by name and status.

## BILLING (4)

- [BILLING] [flagged-off card] [copy] Disabled billing card offers no explanation of why it's off or how to turn it on → "Billing is off for this workspace — an owner can enable it in Settings" + docs link.
- [BILLING] [feature-flag source] [transparency] Card state hinges on an unseen flag with no self-serve path → show flag state and a "Request access" CTA to owners/admins.
- [BILLING] [usage meter] [correctness] Percentage hits 100% below the real quota due to a rounding edge → round consistently (half-up), clamp, and match backend computation.
- [BILLING] [meter label] [consistency] Displayed % disagrees with the used/quota numbers beside it → derive both from one formatter/shared source.

## COST (6)

- [COST] [usage chart] [empty-state] Empty chart canvas reads as broken with no explainer → "No spend recorded yet" + note on metering delay/sampling window.
- [COST] [chart toolbar] [feedback] No refetch action when data looks stale or empty → Refresh button with a last-updated timestamp.
- [COST] [export] [functionality] No way to extract raw cost data → "Export CSV" backed by a cost-breakdown endpoint honoring current filters.
- [COST] [date range] [usability] Fixed implicit window with no range control → preset ranges (7d/30d/custom) persisted in the URL.
- [COST] [sparkline] [robustness] NaN points silently collapse the line, hiding outages → break the line into segments around nulls (explicit gap rendering).
- [COST] [sparkline tooltip] [feedback] Hovering a NaN gap yields "NaN$"-style values → human fallback ("no data") in the tooltip.

## WORKING AREA (10)

- [WORKING AREA] [page shell] [arch] Renders outside AppShell, so nav, sign-out, and global context vanish on this route → mount the route inside the standard shell layout.
- [WORKING AREA] [sign-out path] [navigation] Without global chrome there's no way to sign out or switch project from here → restore header/sidebar within the shell wrapper.
- [WORKING AREA] [reconnect loop] [resilience] Hardcoded 1.5s retry hammers the server with no backoff → exponential backoff with jitter and a max-delay ceiling.
- [WORKING AREA] [connection banner] [feedback] Reconnect attempts are invisible; the UI silently churns → persistent "Reconnecting in Ns…" banner with a manual Retry button.
- [WORKING AREA] [diff viewer] [functionality] Diff is read-only with no per-hunk actions → hunk-level accept/reject bar wired to the patch-application API.
- [WORKING AREA] [pause/resume buttons] [state] Buttons are enabled in impossible states (Pause while paused, Resume while running) → derive visibility/enabled from a single run-state machine.
- [WORKING AREA] [stop button] [safety] Stop is clickable before any run exists, firing doomed calls → gate on active-run state; hide entirely when idle.
- [WORKING AREA] [human-edit entry] [discovery] The ability to hand-edit the plan is undiscoverable → visible "Edit" pencil affordance on plan/artifact surfaces.
- [WORKING AREA] [edit mode hint] [copy] Nothing tells users whether/how edits take effect → helper microcopy stating edit semantics and save state.
- [WORKING AREA] [unsaved edits] [persistence] Hand-edits would be lost on refresh with no dirty indicator → autosave drafts + "unsaved changes" guard on navigation.

## APPSHELL / GLOBALS (10)

- [APPSHELL] [sidebar] [responsive] Fixed 220px sidebar breaks layout under 768px, squeezing content → collapse to an icon rail or overlay drawer below the 768px breakpoint.
- [APPSHELL] [main content] [responsive] Content grid has no min-width guards, causing horizontal scroll on phones → fluid widths + overflow-wrap/table-scroll rules.
- [GLOBALS] [stylesheet] [responsive] Zero @media queries exist anywhere in the CSS → define shared breakpoints (480/768/1024) as tokens for reuse.
- [APPSHELL] [skip link] [a11y] Keyboard users must tab through the entire nav on every page → "Skip to main content" as the first focusable element, visible on focus.
- [GLOBALS] [toasts/status updates] [a11y] Dynamic updates (toasts, async completions) are never announced to screen readers → aria-live="polite" region hosting transient messages.
- [APPSHELL] [status pill] [a11y] Meaning is carried by color alone in tiny text → add shape/icon + aria-label and enlarge to a readable size.
- [APPSHELL] [pill legend] [parity] Color-blind users get no alternate decoding of pill colors → tooltip/legend spelling out each state.
- [GLOBALS] [muted text #9aa4b2] [contrast] Token sits at borderline AA (~4:1) for small text → darken to a ≥4.5:1 variant and re-audit dependents.
- [GLOBALS] [focus styles] [a11y] Focus outlines are removed with no replacement → global :focus-visible 2px ring using the accent token.
- [GLOBALS] [keyboard traversal] [a11y] Custom controls (pills, rows, menus) have no consistent focus treatment → shared focus token applied across all interactive primitives.
