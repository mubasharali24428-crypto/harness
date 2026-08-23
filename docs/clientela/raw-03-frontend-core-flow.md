# Section 7 Frontend — Core-Flow UX Brutal Audit

Scope: `app/login`, `app/meeting`, `app/page.tsx`, `app/review`, working area (`app/projects/[projectId]/runs/[runId]` + `src/components/*`), `src/lib/api.ts`, `src/lib/auth.ts`, `src/lib/useEventStream.ts`, `app/globals.css`. Verified against real backend routes in `../section4` (orchestrator) and `../section6` (meeting). All findings walked as a first-time user through: login → project → meeting @ai → brief → approve → plan propose/approve/start → execution → done/review → PR.

**Verdict: the hero journey is a dead end three times over.** You can log in, chat with a parrot, approve a brief — and then hit a wall: no surface in the entire app can propose, approve, or start a plan; the Review page can never load a PR because nothing ever writes `harness.prId`; and the run/project ids stored at login are fabricated, so every dashboard query silently 404s. Severity: P0 = blocks core flow, P1 = major breakage/confusion, P2 = quality/a11y.

---

## P0 — Core flow blockers

### 1. The plan gate is unreachable from ANY screen (propose/approve/start missing everywhere)
- **Where:** `src/lib/api.ts` (whole orchestrator section, lines 94–163: only `pause/resume/stop/human-edit/done-signal` exist — no `proposePlan`, no `approvePlan`, no `startRun`); `src/components/Controls.tsx:65–102`; `app/page.tsx:108`; `src/components/PlanPanel.tsx:9`.
- **User-visible:** Backend has `POST /runs/{runId}/plan`, `/plan/approve`, `/plan/reject`, `/start` (verified in `section4/src/harness_orchestrator/api.py:330–394`). The UI ships zero callers. A new user approves the brief, is told "head to the Working Area to propose the plan," arrives, and finds Pause/Resume/Stop/"Done, continue" — all useless pre-run. Command Center says "start one in the Working Area after approving a plan" (page.tsx:108) — a circular pointer between two screens that both lack the control. The run can never leave `idle`.
- **Fix:** Add the three API functions to `api.ts`; add a `PlanGate` card to WorkingArea (Propose → Approve/Reject → Start, state-driven off `status.state`: `idle→planning→awaiting_plan_approval→executing`); put a deep-link button on Command Center and on the meeting page's post-approval note.

### 2. Login mints fake `projectId`/`runId` disconnected from the backend
- **Where:** `app/login/page.tsx:23–24` (`setProjectId("proj-test-1")`, `setRunId(\`run-${Date.now().toString(36)}\`)`); echoed fallback at `app/meeting/page.tsx:45`.
- **User-visible:** Fresh user signs in → lands on Command Center → `getRunStatus("run-lz3k4…")` 404s silently (Promise.allSettled swallow, page.tsx:44–49), project name lookup fails ("cosmetic" catch, page.tsx:35–37), Activity Stream empty, Working nav links to `/projects/proj-test-1/runs/run-lz3k4…` which 404s data everywhere. Nothing explains why; the ids look real.
- **Fix:** After login fetch `listProjects()`; if empty, route to a create-project step; store a REAL project id. Delete both fabricated fallbacks. Run ids should come from the backend only.

### 3. Review page can never load a PR — nobody ever writes `harness.prId`
- **Where:** `app/review/page.tsx:15` (`const prId = getPrId()`); repo-wide search confirms `setPrId` exists (`src/lib/auth.ts:79`) but is called only by unit tests.
- **User-visible:** Run finishes, `pr_opened` fires, agent pushed a branch — the human gate of the "hero journey" still shows "No pull request selected / Open a PR from an approved run…" forever. The only way in is hand-editing localStorage. The final flow step is ornamental.
- **Fix:** Derive PR from the run: watch for `pr_opened` events (already streamed by `useEventStream`) or expose `GET /runs/{id}` → `prId`, call `setPrId` there; also link rows in `/runs` to `/review?pr=…`.

### 4. `@ai` answers are canned template echoes, not an LLM — and the client fires a redundant second invocation
- **Where:** `app/meeting/page.tsx:85–86` (posts `@ai ${content}`, then separately `invokeAi(id, uid, content)` — without the prefix); root cause in `section6/src/harness_meeting/service.py:112–114` (`elif command == "ask": response_text = f"Regarding your question: {args}"`) and `ai_participant.py` (pure regex rules engine, no model anywhere).
- **User-visible:** User asks "@ai How do I structure the auth module?" → chat displays literally `Regarding your question: How do I structure the auth module?` Looks like a broken bot; trust in the whole product dies here. Bonus waste: the second `invokeAi` round-trip returns `{reply: undefined}` (no `@ai` prefix → `is_ai_invoked` false) and its result is ignored anyway.
- **Fix:** Wire an actual LLM behind `/ai` (keys page exists — BYO providers). Client-side: one code path — either rely on the server auto-reply on `postMessage` or call `invokeAi` and render its reply with a "thinking…" pending state; delete the dead duplicate call.

### 5. Token expiry mid-flow is indistinguishable from any other error
- **Where:** `src/lib/api.ts:69–92` (no 401 branch in `apiFetch`); `src/lib/auth.ts:37–40`; every page catch (e.g. `app/meeting/page.tsx:38–42`).
- **User-visible:** Session expires while you're mid-brief → every click yields raw strings like `"Invalid or expired token"` (or worse, `"…(is S6 up on :8006?)"` — pointing you at the wrong service). No logout, no redirect, infinite failure loop until you think to Sign out yourself.
- **Fix:** In `apiFetch`, on `res.status === 401`: `clearSession()` + `window.location.href = "/login?expired=1"`; login page shows "Session expired, sign in again." Guard remaining pages against retry storms.

---

## P1 — Major breakage / confusion

### 6. Command Center's "live" pill is a lie — one-shot REST replay, no WebSocket at all
- **Where:** `app/page.tsx:20,53–62,72` (`replayEvents` once at mount → `setSource("live")` permanently; `source` has no other writer).
- **User-visible:** Pill says green "live" while the activity stream is a frozen snapshot from page-load; events that arrive afterward never appear; there's no reconnect indicator because there's no connection. Working area already has the real hook (`useEventStream` + 4-state `CONNECTION_LABEL`) — this page just doesn't use it.
- **Fix:** Use `useEventStream(projectId, token)` and map `connectionState` honestly (Connecting/Live/Reconnecting/Disconnected).

### 7. Empty chat input silently sends "Please review the plan." on your behalf
- **Where:** `app/meeting/page.tsx:83` (`const content = draft.trim() || "Please review the plan."`).
- **User-visible:** New user fat-fingers Send on an empty box → transcript now contains a statement they never wrote, which then poisons the generated brief. Fabricated user intent.
- **Fix:** Disable Send/@ai when `draft.trim() === ""`; never synthesize content.

### 8. "Request changes" records a review decision with an empty comment list
- **Where:** `app/review/page.tsx:49` (`requestChanges(prId, [], reviewer, token)`).
- **User-visible:** Reviewer clicks Request changes; the agent (and audit log) receives "changes requested" with zero explanation. There is no comment field anywhere on the page. The iterate loop can't converge.
- **Fix:** When "Request changes" is clicked, reveal a required textarea; submit `comments: [{path?, body}]`.

### 9. Controls: destructive/unconditional actions with no guardrails
- **Where:** `src/components/Controls.tsx:83–89` (Stop: no confirmation, kills a paying run), `:90–101` ("Done, continue" is enabled in EVERY state — including `idle`/no-plan — and submits whatever's in the takeover editor, often `""` as the diff), `:53–58` (checking "Human takeover" immediately POSTs a human-edit with path `"(unspecified)"` and empty content — server now records a junk file edit).
- **User-visible:** One misclick stops a long-running execution irreversibly; "Done" on a fresh run produces a baffling 4xx or, worse, a bogus done-signal; toggling the checkbox to *look* at the editor already mutated backend state.
- **Fix:** `confirm()`/modal for Stop; gate Done on `state === "awaiting_done_signal"` + non-empty diff; make takeover toggle purely local and require a real path before any submit.

### 10. Every incoming WS event triggers an un-debounced status+plan refetch — stale-response race included
- **Where:** `src/components/WorkingArea.tsx:49–53` (`events.length > 0 → refresh()`; terminal_output streams can be dozens/sec), `refresh` at `:36–40` has no ordering guard.
- **User-visible:** Bursts of events hammer `GET /status` + `/plan` concurrently; a slow earlier response can land after a newer one and overwrite fresher state — UI flips backward (e.g. status flickers executing→planning). Also wasted battery/API quota.
- **Fix:** Debounce ~300ms trailing; keep only the latest request (`let latest = 0; … if (mine !== latest) return`) or derive status optimistically from events and reconcile.

### 11. Event stream: unguarded `JSON.parse` and a naive fixed-interval reconnect loop
- **Where:** `src/lib/useEventStream.ts:55` (`JSON.parse(evt.data)` — one malformed/garbage frame throws inside the handler), `:7,66,76` (hard-coded 1500 ms retry, no jitter/backoff cap; if the ticket endpoint is down it loops forever, silently, forever minting attempts).
- **User-visible:** A single bad frame kills message processing for that socket (exception in handler) and spams the console; a backend blip produces a metronome of reconnect attempts with no exponential backoff and zero user-facing escalation past the tiny "Reconnecting…" dot.
- **Fix:** try/catch around parse (drop bad frame + console.warn once); exponential backoff with cap/jitter; after N failures show an actionable banner with a Retry button.

### 12. Project switcher: "Working" link goes stale + sidebar overwrites your selection with `items[0]`
- **Where:** `src/components/AppShell.tsx:38–41` (`workingHref` computed once at mount, never updated on switch), `:43–51` (`items.find(saved) ?? items[0]` then `setProjectId(valid.id)` — silently replaces whatever was stored, racing with page-level `getProjectId()` reads).
- **User-visible:** Switch project in the sidebar → click "Working" → you land in the PREVIOUS project's run. Meanwhile Command Center (which read the old id before the async project list resolved) shows data for project A under the header of project B. Classic stale-state sandwich.
- **Fix:** Derive `workingHref` from `selected` state (recompute on change); don't auto-overwrite a saved id — show "(saved project not found)" instead.

### 13. Sign-out wipes only the token — the next account inherits your run/project/pr/goal
- **Where:** `src/lib/auth.ts:37–40` (`clearSession` removes only `harness.session`; `KEYS.projectId/runId/userId/prId/goal` persist), combined with `login/page.tsx:23–31` which deliberately KEEPS existing values.
- **User-visible:** Teammate signs in on the same machine → suddenly "their" Command Center shows the previous user's run, Working jumps to the other person's run, Review loads their PR. Cross-account state bleed.
- **Fix:** `Object.values(KEYS).forEach(k => localStorage.removeItem(k))` in `clearSession()` (session key included), and stop preserving stale ids across logins.

### 14. Errors are raw exception strings — inconsistently announced, leaking infrastructure
- **Where:** `app/login/page.tsx:47,61,75,99`; `app/meeting/page.tsx:38–42` (literally appends `"(is S6 up on :8006?)"` — internal port + section numbering to an end user); `app/page.tsx:51,167`; `app/review/page.tsx:52,156`; contrast: newer pages (`app/runs/page.tsx:236`, onboarding, settings) correctly use `role="alert"`.
- **User-visible:** `"Request failed with status 500"`, FastAPI internals, port numbers. Screen readers announce none of it on the older four screens (plain `<p>`), sighted users get mystery meat.
- **Fix:** One `<ErrorBanner>` component: `role="alert"`, maps known `ApiError.code`s to human copy ("Your session expired", "The meeting service is offline — try again shortly"), hides ports/topology behind a details toggle.

### 15. Meeting exists only in React state — refresh destroys it
- **Where:** `app/meeting/page.tsx:19` (`useState<string | null>(null)` for `meetingId`; never persisted, not in URL).
- **User-visible:** Accidental F5 mid-meeting → "Start a live meeting" again; the old transcript (still alive server-side) is orphaned with no meeting list to return to. Brief approval progress vanishes with it.
- **Fix:** Put `?m=<meetingId>` in the URL (shareable!), persist to `KEYS`, and add a `GET /meetings?projectId=` recent-list dropdown.

### 16. No responsive design whatsoever — fixed 220px sidebar, hard 2-column grid
- **Where:** `app/globals.css:31–42` (`position:fixed; width:220px` sidebar), `:102–106` (`margin-left:220px`), and the file contains ZERO `@media` queries (grep-verified); `src/components/WorkingArea.tsx:62` inline `gridTemplateColumns: "1fr 1fr"` ignoring the responsive `.grid` class.
- **User-visible:** Below ~700px the fixed sidebar consumes a third of the screen and can't be collapsed; WorkingArea's two forced columns render CodeMirror + Controls at ~160px each — unusable on tablet/phone. An "agent command center" you can't read on mobile.
- **Fix:** One breakpoint (<900px): sidebar → top bar/hamburger; `.content` margin-left:0; WorkingArea grid → `repeat(auto-fit, minmax(min(100%, 420px), 1fr))`.

### 17. Render-time `localStorage` read in Command Center → hydration mismatch
- **Where:** `app/page.tsx:74` (`{!getToken() && <div>Welcome…}` — `getToken()` during render; server renders "Welcome" (auth.ts guards `typeof window`), client with a token renders nothing → React hydration error in console on every authenticated visit to `/`.
- **User-visible:** Red hydration overlay/warning in dev; in prod a flash of the "Not signed in yet" card before content settles. Console noise on the landing page.
- **Fix:** `const [token, setToken] = useState<string|null>(null)` + read in `useEffect` (the pattern WorkingArea.tsx:26–34 already uses).

---

## P2 — Quality, speed, accessibility

### 18. Zero optimistic UI + one global `busy` couples unrelated actions
- **Where:** `app/meeting/page.tsx:24,151–168` — single `busy` disables Send, Send @ai, Generate brief, Approve brief simultaneously; message appears only after POST + full `getMessages` refetch (`:88–90`).
- **User-visible:** Send a message on a laggy link: input freezes for both buttons, your bubble pops in ~1s late, feels broken. Can't queue a follow-up while AI "thinks."
- **Fix:** Optimistic append with temp id + reconcile; per-action busy (`busy === "send"` etc.).

### 19. Keyboard support: none. Enter doesn't even send a chat message
- **Where:** `app/meeting/page.tsx:146–150` — bare `<input>`, not in a `<form>`, no `onKeyDown`; repo-wide grep: zero `onKeyDown` handlers in `app/` + `src/`.
- **User-visible:** Chat where Enter does nothing (mouse mandatory) is instantly recognizable as unfinished. No shortcuts anywhere (⌘K, g+m, Esc).
- **Fix:** Wrap input in `<form onSubmit>` (Enter sends, Shift-less world is fine for single-line); add a `/`-focus and `g`+nav shortcut layer later.

### 20. Focus management + labeling gaps (a11y)
- **Where:** `app/login/page.tsx:103–117` — switching to OTP mode doesn't move focus to the code input (focus dies with the unmounted button; SR/screen-keyboard users must hunt); `:87–94` email input has placeholder-as-label only; `app/meeting/page.tsx:148` same for chat input; `src/components/AppShell.tsx:59–64` — "TEAM"/"PROJECT" are `<div className="label">`, not `<label htmlFor>`; `:97–105` disabled "Working" is a `<span aria-disabled>` that keyboard users tab straight past with the explanation locked in a `title` attribute.
- **Fix:** `ref.focus()` on mode swap; real `<label>`s; make the disabled item a focusable element with `aria-disabled="true"` + visible hint text (title tooltips are touch/AT-hostile).

### 21. Live regions missing where content updates itself
- **Where:** `app/meeting/page.tsx:136–144` (new chat messages/AI replies unannounced), `src/components/StatusPanel.tsx:39–45` (state/connection pills change silently), `app/page.tsx:135–144` (activity stream).
- **User-visible:** Screen-reader users never learn the AI answered or the run got blocked — the blocker callout (StatusPanel:47–52) is exactly the thing they most need announced.
- **Fix:** `aria-live="polite"` on the chat log and status pill row; `role="status"` on blocker callouts.

### 22. Human Gate ignores server review status — double-approve and phantom decisions
- **Where:** `app/review/page.tsx:11,34–56,129–152` — buttons gated only by local `busy`; `decision` lives in component state and is never reconciled with `liveReview.review_status` (no refetch after decide).
- **User-visible:** Approve twice (two audit entries); another reviewer's decision never appears; your "Decision recorded" banner evaporates on refresh while the server state moved on.
- **Fix:** Disable/relabel from `review_status` after refetch-on-decide; show recorded decisions fetched from the backend.

### 23. Console-noise & robustness odds and ends
- **Where:** `useEventStream.ts:55` (see #11); `src/components/CommentsNudges.tsx` (`crypto.randomUUID()` — throws on non-secure contexts, breaks any http:// LAN deploy); `app/page.tsx:138` (`[...events].reverse()` allocates per render); `meeting/page.tsx:187` (`key={r}` on requirement strings — duplicate requirements collide React keys).
- **Fix:** UUID fallback, memoized derived lists, index-or-id keys.

### 24. Client-side risk heuristic duplicates the backend's
- **Where:** `app/review/page.tsx:8` (`RISKY_FILES` hardcoded) rendered only when `risk_flags` is absent (`:114–117`).
- **User-visible:** Two sources of truth drift apart — backend adds `infra/` rule, client keeps saying "No high-risk zones touched" for a file list the flag engine would flag. Mildly dangerous reassurance.
- **Fix:** Render backend flags only; delete the client regex.

---

## What checked out fine (credit where due)
- `parseApiError` (api.ts:37–67) handles contract + FastAPI envelopes properly.
- `getPlan` 404→null (api.ts:99–109) is the right call.
- WS ticket flow (single-use, bearer out of URLs, api.ts:176–185) and seq-dedupe merge (`mergeEvents`) are solid.
- `/runs` page (filters, paging, `aria-live` count, `sr-only` caption, honest relative times) is the quality bar the rest of the app should meet.
- Diff/Terminal empty-states are refreshingly honest about backend limitations.

## Top 5 by ROI
1. **#1 Plan gate UI** — without it there is no product.
2. **#3 prId wiring** — the finale currently cannot trigger.
3. **#2/#13 Real ids + full sign-out cleanup** — kills the silent-404 fog.
4. **#5 Global 401 interception** — one `if`, removes the worst failure loop.
5. **#14 Shared ErrorBanner** — one component fixes lying copy + a11y on five screens.
