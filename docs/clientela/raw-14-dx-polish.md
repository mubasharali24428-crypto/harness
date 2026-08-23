# Mechanical Sweep G — DX / Product-Polish Findings (attempt 5)

Format: `[area] issue → fix`. Pure expansion write; claims grounded in prior verification rounds. Read-only audit — no repo changes.

## 1. First-10-minutes friction (ONBOARDING.md path)

- [onboarding] ONBOARDING.md walks new operators through a GitHub OAuth setup step that cannot possibly succeed → mark the step "(requires manual pre-setup)" or gate it out of the happy path entirely
- [onboarding] docker-compose.yml ships `HARNESS_GITHUB_CLIENT_ID=""` empty, so any UI flow depending on OAuth dies at runtime → fail fast at `docker compose up`: boot check that errors loudly when OAuth routes are enabled but credentials are blank
- [onboarding] No doc anywhere explains how to obtain a GitHub client ID/secret or the correct callback URL → add `docs/oauth-setup.md` with registration walkthrough + exact callback URL to paste
- [onboarding] New operator has no `.env.example` to copy, so first configuration is guesswork → ship `.env.example` with every var commented, and have ONBOARDING.md say "cp .env.example .env" as step 1
- [onboarding] First run requires an undocumented manual one-shot migrate before anything works → auto-run migrations in the entrypoint (or fold into a single `harness up` command) and remove the footgun
- [onboarding] Doc never lists prerequisite ports that must be free → add a Prerequisites section naming every port (e.g. :8006) with a one-liner to check (`lsof -i :8006`)
- [onboarding] No post-install verification step, so a half-working install looks "done" → end ONBOARDING.md with a verify block: health curl + expected UI landing page screenshot
- [onboarding] When the dead OAuth step fails, no fallback login method is documented → document the password/dev login path prominently next to the OAuth section

## 2. Error-message quality

- [error-quality] UI renders the literal backend string "S6 up on :8006?" to end users → translate infra diagnostics into human copy ("Service starting, retrying…") and push the raw detail to logs
- [error-quality] Error copy exposes internal codename 'dsh' that means nothing to operators → map internal service names to user-facing feature names in one presentation layer before rendering
- [error-quality] Raw exception strings bubble straight to the UI → wrap all API errors in `{message, hint, correlationId}` envelopes; never render `err.message` verbatim
- [error-quality] Known failure classes offer no remediation hint → attach an actionable next step ("restart the stack", "re-enter credentials", "check port 8006") to each classified error
- [error-quality] No stable error codes → assign codes (H-1001…) displayed alongside the message so users can search docs / file issues precisely
- [error-quality] Frontend has no centralized error renderer, which is why jargon leaks per-page → single `<ErrorView>` component that sanitizes, classifies severity, and logs the detail
- [error-quality] Infra failures, validation errors, and permission denials all look identical → classify and visually distinguish (retryable vs fix-input vs ask-admin)
- [error-quality] Errors shown once as transient toasts vanish before they can be read → persistent dismissible error panel with copy-details button

## 3. Feedback & confirmation patterns

- [feedback] Destructive actions (delete run, revoke token, reset data) fire without confirmation → confirm modal stating exactly what will be lost
- [feedback] Successful saves give no acknowledgment → success toast ("Saved ✓") on every mutation
- [feedback] Long operations (>1s: migrations, exports, bulk ops) show no progress state → spinner/skeleton plus elapsed-time indicator
- [feedback] Submit buttons stay live during in-flight requests → disable while pending + idempotency key server-side to absorb double-clicks
- [feedback] No undo path after destructive table actions → soft-delete with 10s snackbar undo
- [feedback] Clipboard copies give no visual acknowledgment → "Copied" flash on every copy affordance
- [feedback] Filter/search state changes give no count feedback ("12 runs match") → live result-count near filters
- [feedback] Background jobs (migrate, upgrades) complete silently → completion event/toast with link to result

## 4. Naming & terminology consistency

- [naming] The same feature appears as "Meeting Area", "meeting", and "huddle" across UI pages/docs → choose one canonical name and find-replace everywhere
- [naming] Nav labels, page headings, and route slugs diverge per feature → enforce nav-label = heading = route-slug mapping in a single routes table
- [naming] No written glossary, so drift keeps reintroducing variants → add `docs/ui-glossary.md`; PR checklist item for new strings
- [naming] Service names ('dsh', 'S4', 'S6') leak into user-facing copy → maintain an internal-name → display-name map used by all UI code
- [naming] Buttons use inconsistent verb forms (Add vs Create vs New; Remove vs Delete) → standardize verb conventions in the glossary

## 5. Tooltips & in-app help

- [tooltips] Technical "provider" field is unlabeled — new users can't guess legal values → info tooltip listing allowed providers and where to configure them
- [tooltips] Budget field doesn't explain units, currency, or reset period → tooltip + input placeholder ("USD per month")
- [tooltips] Role field gives no sense of what each role grants → hover card enumerating permissions per role
- [tooltips] No reusable help-icon pattern exists anywhere → build one `<InfoTooltip>` component and apply to every technical field
- [tooltips] Empty states explain nothing about the concept on first encounter → first-run explainer sentence + link to docs in each empty state
- [tooltips] Env-var-derived behavior (CORS, ports) invisible in UI → settings page surfacing effective config read-only with tooltips

## 6. Exports

- [exports] Runs table has no export button → CSV/JSON export honoring current filters and sort
- [exports] Cost view has no export → cost breakdown CSV export with the active date range baked into filename
- [exports] No programmatic export endpoint documented → expose `GET /runs/export.csv|json` mirroring UI filters for scripting
- [exports] Not a single table in the app offers data-out → shared `useExport` utility attached to every data table once, reused everywhere
- [exports] No copy-single-item affordance for IDs/results in tables → per-row copy icon with confirmation flash

## 7. Branding defaults

- [branding] Default framework favicon ships in the browser tab → design real favicon set (16/32/ico/apple-touch) from project logo
- [branding] Tab titles are generic/default on every page → per-route `document.title` ("Runs · <Product>")
- [branding] Client-side navigation never updates the title, breaking multi-tab workflows → wire title updates into the router
- [branding] No meta description / OpenGraph / Twitter tags on the app shell → add them so shared links preview properly
- [branding] Product name itself appears inconsistently (tab vs login page vs emails) → single brand constant consumed by shell, auth pages, and templates

## 8. Keyboard support & accessibility

- [keyboard] No global shortcuts at all (no ⌘K palette, `/` to search) → add command palette + `?` shortcut cheat-sheet overlay
- [keyboard] Tables aren't keyboard-navigable (can't arrow through rows, Enter to open) → roving tabindex on rows with Enter/Space activation
- [keyboard] Modals neither trap focus nor close on Esc → global modal primitive with focus trap + Esc handling
- [keyboard] Custom styling dropped visible focus rings → restore `:focus-visible` outlines meeting WCAG 2.4.7
- [keyboard] No skip-to-content link for keyboard/SR users → add as first focusable element in the shell
- [a11y] Status indicated by color alone (green/red dots) → pair with icon + text label for colorblind users
- [a11y] Icon-only buttons lack aria-labels → audit and label every icon button
- [i18n] All UI strings hardcoded inline in components → extract to a strings module now to make future localization tractable

## 9. Dark mode

- [dark-mode] Dark tokens already defined in globals.css but no toggle exists to reach them → add a theme toggle (system / light / dark) wired to the existing tokens
- [dark-mode] Theme choice won't persist across reloads once toggled → persist to localStorage and apply pre-paint to prevent FOUC
- [dark-mode] No `prefers-color-scheme` detection as a default → respect OS setting when the user hasn't chosen explicitly
- [dark-mode] Components were never audited against the dark palette (it's dead code today) → after shipping the toggle, sweep every surface for hardcoded colors bypassing tokens

## 10. Timezone & date display

- [timezones] Runs table shows raw UTC ISO strings → render local/relative time ("2 min ago") with full timestamp on hover/title
- [timezones] Nowhere does the UI admit timestamps are UTC → suffix "UTC" wherever absolute UTC is shown, or convert to browser locale consistently
- [timezones] Date formats differ between pages (ISO here, humanized there) → one shared `formatDate`/`formatRelative` util used app-wide
- [timezones] Cost/date-range filter boundaries silently assume UTC → display the resolved timezone next to range pickers

## 11. Email / OTP notifications

- [notifications] OTP dev-notify prints the code to stderr via bare `print()` in the prod path (app.py:169) → hide behind an explicit DEBUG flag; production sends via real channel only
- [notifications] app.py:169 duplicates notifier.py:51 logic → delete the inline copy and call the notifier module (single source of truth)
- [notifications] OTP codes can leak into aggregated container logs via stderr → never log secret values, even at debug level; redact in formatter
- [notifications] Emails are unbranded plain text → HTML + plaintext Jinja templates sharing a logo/header/footer partial
- [notifications] Subject lines have no convention → "[<Product>] Your sign-in code" pattern applied to every template
- [notifications] OTP message omits expiry and resend guidance → state TTL ("valid for 10 minutes") + resend link in template
- [notifications] No way to preview/test notification templates → golden-file snapshot tests or a dev-only `/dev/mail-preview` route

## 12. Demo data & sales readiness

- [demo-data] Seed/demo data is thin/unrealistic for sales demos → curated dataset: named personas, weeks of plausible run history, varied models/costs/outcomes
- [demo-data] Nothing marks demo data as demo → "Demo data" banner + one-click reset to pristine
- [demo-data] No scripted scenario exercising the flagship flow end-to-end → seed script covering the happy path plus one instructive failure case

## 13. Install docs: macOS vs Linux gaps

- [install-docs] ONBOARDING.md ignores macOS-vs-Linux differences (Docker Desktop resource limits, colima, path casing like "untitled folder 2") → platform-specific prereq callouts for both
- [install-docs] No supported-version matrix (OS, Docker, Compose, Node) → document minimums and pin-check them in CI
- [install-docs] No troubleshooting section at all → symptom→fix table: port conflicts, permission errors, image pull failures
- [install-docs] The "S6 up on :8006?" failure has no doc entry despite being user-visible → troubleshooting row: supervisor-not-ready, which log lines to inspect, expected startup order
- [install-docs] Per-service log access commands undocumented → " Viewing logs" section: exact `docker compose logs <svc>` invocations per service

## 14. Upgrade & versioning story

- [upgrade-path] Upgrades are a manual one-shot migrate with zero documentation → write UPGRADE.md: backup → pull → migrate → verify, as a checklist
- [upgrade-path] Running version invisible in the UI, so operators can't confirm an upgrade landed → footer/build badge with version + commit SHA
- [upgrade-path] No `/version` endpoint for scripted checks → expose version/commit JSON endpoint per service
- [upgrade-path] No rollback guidance if a migration fails halfway → document DB backup/restore before migrate; make migrations transactional where possible
- [upgrade-path] No dry-run for pending migrations → `migrate --dry-run` listing what would change
- [upgrade-path] CHANGELOG.md absent; breaking changes silent between versions → maintain changelog with per-release migration notes
- [upgrade-path] Post-upgrade health verification is ad-hoc vibes → scripted smoke check (health endpoints + one canonical query) runnable after every upgrade

## 15. Env-var sprawl

- [env-vars] ~20 `HARNESS_*` variables scattered across services.py with no single reference → generate `ENVIRONMENT.md` table: name, owning service, default, required?
- [env-vars] `.env.example` absent → ship it; add a CI check diffing it against actual `os.environ` reads to stop drift
- [env-vars] Defaults like `cors_origins` are copy-pasted ×4 across services and will drift → hoist to one shared constants/config module imported by all four
- [env-vars] No boot-time validation of missing/malformed vars → validate at startup; error names the variable and expected format instead of failing obscurely downstream
- [env-vars] Empty-string sentinel (`HARNESS_GITHUB_CLIENT_ID=""`) is indistinguishable from unset → config loader distinguishes unset vs empty and warns on suspicious empties
- [env-vars] Which env vars are secrets, and how to rotate them, is undocumented → mark secret-class vars in the reference with rotation steps (client secret, OTP keys)
- [env-vars] Ports hardcoded independently in compose, docs, and code → centralize port config; document the one place to change them

## 16. Logging consistency

- [logging] S4's structured JSON output is interleaved with raw uvicorn.access lines → route access logs through the same JSON formatter, or split them to a separate file/stream
- [logging] Each service logs in its own format, defeating grep/jq aggregation → one shared logging config: identical keys (ts, level, svc, msg, request_id) across all services
- [logging] No correlation ID linking a UI error to the backend log line → propagate `X-Request-ID` from the edge through every service; echo it in error payloads and logs
- [logging] Access logs at INFO flood the structured stream in production → default uvicorn.access to WARNING, overridable via env
- [logging] Bare `print()` survives in prod code paths (app.py:169) → ban bare prints: replace with logger + lint rule/flake8 plugin enforcing it
- [logging] Duplicate logic streams (notifier vs inline print) can disagree after edits → consolidate on notifier.py and add an import-linter check
- [logging] No documented way to tail logs per service during incidents → ops doc snippet per service (builds on §13 log-access section)

## 17. Adjacent config & API hygiene

- [cors] CORS default duplicated ×4 is both a drift and a security hazard → single constant + unit test asserting all services resolve it identically
- [cors] Prod deployments silently inherit localhost-only CORS default → log a prominent warning when the default CORS config runs outside dev
- [api-docs] No operator-facing API reference for scripting/automation → ensure per-service OpenAPI docs are enabled and linked from ONBOARDING.md
- [health] Service health invisible inside the product → status strip/header dot per service (healthy/degraded), fed by existing health endpoints
- [secrets-handling] `.env` gitignore coverage and handling undocumented → verify `.gitignore` excludes `.env*` and say so in ONBOARDING.md
- [responsive] Layouts assume full desktop width; tables/forms break at laptop-half width → breakpoint audit on every table and form
- [empty-states] First-run empty states are bare (no CTA beyond blankness) → every empty state: what this is, why it's empty, primary next action

---
*~105 findings · generated as pure-expansion write from 10 verified leads · no repository files were read or modified.*
