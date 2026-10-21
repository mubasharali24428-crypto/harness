# Parts 12 and 56: real-browser responsive and keyboard check (2026-10-20)

Until this pass nothing in section 7 had been driven by a real browser for layout or keyboard use: the tests render to static markup in Node. This is
the first run in Chromium, against a production build (`next build` + `next start`) of the section 7 working tree.

## Method

* Script: `section7/scripts/browser-check.mjs`. Usage and environment variables are in its header. It exits 1 on a hard failure.
* Browser: Chromium 141.0.7390.37 through Playwright (the pre-installed copy; `playwright install` was not run). Axe: axe-core 4.10.2, tags
  wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa.
* Pages: `/login` and the shared run page `/projects/p1/runs/r1`. Widths: 320, 375, 768 and 1280 px.
* **The data is synthetic.** Every identity, orchestrator and discussion call is answered from fixtures inside the script (one run, a lead, two
  participants, a conflict, an agent, a comment, 60 history events); anything not listed is answered 404 so the page takes its documented
  "not available" path. The socket is not answered, so the page falls back to polling. No real backend was started.
* Per page and width the script records: horizontal overflow of the page; a full Tab walk (count of stops, stops with no accessible name, stops
  where focusing changes no pixel near the element, a keyboard trap, the skip link and that it moves the next stop into `<main>`); controls
  smaller than 24 px; and the axe result. Two scripted keyboard-only actions follow: typing and sending a direction to the agent, and reaching
  "Jump to latest" in the 60-event history log with the keyboard.
* Raw output of the last run: `evidence/s11/browser-check-output.txt` (15 lines).

## Result of the last run: 0 hard failures

| Check | Result |
|---|---|
| Horizontal overflow, both pages, 4 widths | none |
| `/login`: Tab stops, names, focus change, axe | 4 stops at every width, all named, all visibly change on focus, axe 0 violations |
| Run page: Tab stops | 53, all named, no trap, order cycles; first stop is "Skip to content" and it moves the next stop into `<main>` |
| Run page: focus change | every stop that was on screen changed pixels on focus; 1 stop was off screen and could not be judged |
| Terminal panel (part 12 item 009) | heading present on the run page at all 4 widths |
| Keyboard-only: send a direction | typed the text, reached "Send direction" in 1 Tab, Enter sent `POST /redirect` |
| Keyboard-only: history log | "Jump to latest" reached with Tab, Enter moved focus onto the newest entry, inside the log |

Mutation check: with the focus line of `jump()` removed, the history-log check fails ("focus is on button, in log: false"). The script can fail.

## Defects the check found, now fixed (each has a unit test where a rule can be pinned)

1. **The top bar and content sat side by side at 900 px and below.** `.shell` is a flex row on desktop and stayed one in the drawer layout, so the
   page was 1002 px wide at 320 and 375 and 1014 px at 768. Fixed with `.shell { flex-direction: column; }` in the 900 px block
   (`AppShell.responsive.test.tsx`).
2. **The run page's two columns were an inline `1fr 1fr` grid.** An inline style beats the media query, and a bare auto track cannot shrink below
   its widest child (a table). Replaced by `.working-grid` / `.working-col` with `minmax(0, 1fr)` and a one-column override at 900 px
   (`AppShell.responsive.test.tsx`). Overflow went from 1002 / 1014 px to none.
3. **Two code editors had no accessible name** (axe `aria-input-field-name`). `useEditorView` takes a `label`; the diff view and the new-file
   editor pass one (`tests/unit/editorLabels.test.ts`).
4. **The history log lost its list role and its scroller was not keyboard-reachable** (axe `listitem`, `scrollable-region-focusable`).
   `role="log"` on an `<ol>` replaces the list role. The log is now a focusable `div` with the list inside it; "Jump to latest" focuses the
   last `<li>` (`keyboard.test.tsx`, and the Chromium check above).
5. **The "Open shared run" link used the browser's default blue on the dark page** (contrast). It uses `var(--accent)`.
6. **The terminal output box scrolls but could not be focused.** It is now a labelled, focusable region (`tests/unit/terminalPanel.test.tsx`).

Item 4 included a regression of mine: my first version moved focus to the log's last child, which was the `<ol>`, not an entry. Reading the code
caught it; the Chromium check now pins it.

## Findings left open (not fixed, informational in the script)

* **Closed 2026-10-21 (A7): colour contrast and target size.** The run page now shows axe 0 violations and 0 targets under 24 px at all four widths (rules at the end of `section7/app/globals.css`; the intent and owner panels are included). The `#6b7280` text the notes below describe is no longer in the source; axe reports no contrast failure. What stays open is below.
* ~~**Colour contrast, run page: 7 elements, 3.3 to 3.9 against the 4.5 needed.** Cause: the literal `#6b7280` used for secondary text on the dark
  panels (`h1` suffix, run-state pill, panel hints), set inline in several components rather than through a token. Raising it is a visual
  decision across the product, so it is left to the owner. The script prints the ratio per element.~~
* **Target size (WCAG 2.2, 24 px): 3 to 4 elements** (breadcrumb links 15 px high, two selects 19 px, one 13 px checkbox). The 44 px rule at 600 px in
  `globals.css` covers `.btn`, the text inputs and the team switcher, not these links, selects or the checkbox.
* **`.cm-scroller`** is flagged by axe `scrollable-region-focusable` at three of the four widths. The editor's own content element takes focus
  and is named, so this is probably axe not seeing it; it was not confirmed with assistive technology.
* The script does not render a terminal line, so the output box of item 6 is covered by the unit test, not by the browser.

## Limits (what this does not show)

* **No screen reader was used** (VoiceOver, NVDA, JAWS, TalkBack). Axe finds missing names and roles; it cannot say whether the announcements
  make sense. Row 56 stays `partial` for this and for the items below.
* Two pages of the product, synthetic data, one browser engine, one run in a given state. The other pages (runs index, settings, dashboard,
  notifications) were not walked. No visual regression, no zoom to 200 to 400%, no touch input, no right-to-left, English only.
* Focus visibility is a pixel comparison of the element's neighbourhood with and without focus. It proves something changes, not that the
  change meets the 3:1 contrast of WCAG 2.4.11.
* Not part of CI. `make` and the workflow files do not run `browser-check.mjs`; it needs a built server and a Chromium.
