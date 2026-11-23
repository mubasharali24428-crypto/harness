# Marathon 2.0 W10 — a persistent, audited browser adapter (M2-W10)

Follows the 2026-10-28 Marathon 2.0 roadmap, the W10 row, and
[the W9 outcome](m2-w9-plan.md). The static-fixture repro gate (render, look,
fix, render) already had a live drill; [week-10-plan.md](week-10-plan.md)
closed with "no clicking, typing or multi-step navigation: the tool is
render-and-look — interaction is the first thing to add when a gate needs it."
W10 is that addition.

Governing rules: *an unchecked gate stays open when a dependent week begins*,
*evidence over claims*, *deny when a boundary cannot be enforced*, and *no
claim of external-site readiness from local fixtures*.

## Purpose

W10 asks for a browser that:

1. **stays open** — navigate, inspect, click, type, scroll, screenshot as
   separate actions against one page;
2. **compares accessibility data with visual grounding** on local fixtures;
3. **audits and bounds every action**;
4. **enforces the boundary per action**: redirects, subresources, private
   addresses, downloads, clipboard, credentials, session cleanup and denied
   destinations tested;
5. treats a parser such as OmniParser as an **optional** component.

## Audit (2026-10-28)

| Area | Observation | Where |
|---|---|---|
| Render-and-look gate | live: screenshot/dump of one URL per process, path confined, proxy-bound, env scrubbed, capped | `section5/.../browser_gate.py`, `w10_browser_drill.py` (16 checks) |
| Agent repro/fix/recheck | live model drill: white swatch before, red after, pixels sampled, proxy/no-proxy legs | `w10_browser_drill.py` |
| Interaction | **missing.** No click/type/scroll, no page that survives more than one call | `week-10-plan.md` ("What W10 will not claim") |
| Accessibility data | **missing.** The gate returns a DOM dump, not roles/names | — |
| Action policy | **missing.** The gate has one URL check per process; nothing re-checks a redirect, a download or a permission prompt | — |
| Playwright | **not available on purpose**: the worker image has no Node runtime and the image note says Chromium is used directly | `section4/worker.Dockerfile` |
| OmniParser | evaluated as an optional component | see Findings |

The gap was not "render better"; it was that **looking is not acting**.

## What changed

### `section5/src/harness_sandbox/browser_session.py`

A stdlib-only adapter that launches Chromium once and drives it over the
DevTools protocol through a minimal WebSocket client written here (handshake,
masked frames, ping/pong, large-frame lengths). It pins the viewport with
`Emulation.setDeviceMetricsOverride` (a headless window is not the viewport:
640×400 came out as 640×313 and pushed a control's centre out of the clickable
area — measured, then fixed). Actions:

| action | what it does | bound |
|---|---|---|
| `navigate` | gate URL policy, load, wait for ready, re-check the final `location.href` | 15 s ready timeout |
| `inspect` | Chromium's accessibility tree (role, name, backend node) + page text | 500 nodes, 200 k chars |
| `clickAx` | find AX node by role+name → `DOM.getBoxModel` → click the centre with mouse events | viewport check |
| `click` | viewport coordinate | 0..width/height |
| `type` | focus a selector, `Input.insertText` | 2 000 chars |
| `scroll` | `mouseWheel` delta | ±5 000 px |
| `screenshot` | `Page.captureScreenshot`, PNG header checked, written inside the workspace | 4 MiB total session cap |
| `clipboard` | what page script gets from `navigator.clipboard.readText()` | — |
| `requests` | every request the page made, and download events | 500 URLs |
| `wait` | explicit pause | 5 s |

Enforced at the session, not left to the page:

* the **same URL policy as the gate** (`gate.check_url`: `file://` inside the
  workspace, `https://` only when allow-listed *and* a proxy is configured),
  plus an explicit refusal for literal private/loopback/link-local addresses;
* **redirects are re-checked where they land**, and a refused landing page is
  **quarantined to `about:blank`** before the refusal is raised, so the refused
  document cannot be read by the next action (the request log still records
  that it was fetched — stated, not hidden);
* **downloads are denied** with `Browser.setDownloadBehavior: deny`; the drill
  observes `downloadWillBegin` followed by `downloadProgress: canceled` and no
  file;
* **permissions are denied as a class** (`--deny-permission-prompts`,
  `Browser.resetPermissions`); page script reading the clipboard gets
  `NotAllowedError`;
* **the browser gets no environment** — PATH, HOME, LANG and proxy variables
  only (`browser_gate._browser_env`); a canary variable set on the container
  never appears;
* **cleanup**: `Browser.close`, then SIGKILL of the process group if it
  lingers, then removal of the throwaway profile;
* **audit**: one JSON line per action on stdout with the action, target,
  duration and outcome. The CLI caps a session at 50 steps.

The module is copied into `/opt/harness-shell/` in the worker stage
(`section4/worker.Dockerfile`), beside the gate it shares policy with.

### Tests

`section5/tests/unit/test_browser_session.py` (21): private/loopback address
refusal, the gate-plus-private navigation policy, workspace path resolution,
action caps (unknown action, oversized text, off-viewport click, escaping
screenshot path), WebSocket handshake with a real fake server (masked client
frames, pong to a server ping), CDP by-id dispatch with events kept, protocol
errors surfaced, the bounded `wait`, and the CLI step cap. No browser needed.

### Fixture and drill

`scripts/e2e/fixtures/w10_session.html` — accessible button (`aria-pressed`,
`aria-controls`), labelled canvas (`role=img`, `aria-label`), labelled textbox,
2 400 px spacer, a `download` link, and a clipboard attempt on load.
`scripts/e2e/w10_browser_session_drill.py` runs the adapter in a detached worker
container (`--network none`, read-only, cap-drop ALL, uid 10001) and executes
19 steps.

## Outcome (2026-10-28)

**Browser session, live: 27/27 checks PASS.**
`pilot-prep/w10-browser-session-1793205698.json`

* Start: 640×400 viewport, `envKeys: [HOME, LANG, PATH]` (the container's
  `HARNESS_SECRET_CANARY` never reached the browser).
* Navigate: `file:///ws/w10_session.html`, title read.
* Accessibility: `button "Notifications off"`, `textbox "Your name"`,
  `image "Weekly signups chart"`; page text shows `CLIP: DENIED:NotAllowedError`.
* AX → pixels: the button was found in the AX tree, its box centre was
  **(117, 317)**, real mouse events fired there; afterwards `aria-pressed` is
  `"true"` and the canvas pixel went **#b3261e → #1a7f37** (the same change in
  three places).
* Type: `"Mubashar"` in the focused field; scroll: `scrollY 600`.
* Clipboard: `DENIED:NotAllowedError`.
* Requests: only `file:///ws/...` — no remote subresource from a local page.
* Refusals: `/etc/passwd` outside the workspace; `https://api-docs.deepseek.com/`
  with no proxy ("remote pages need the egress proxy"); `http://` scheme;
  a page-script redirect to `file:///etc/hostname` → refused **and** the page
  is `about:blank` on the next inspect; `../escape.png` refused.
* Download: `downloadWillBegin` → `canceled`, no file written.
* Cleanup: `profileRemoved: true`, **no chromium process left** in the
  container.
* Evidence: the two screenshots were attached to a real run as a W9 visual
  pair (before + after, `pairId` set), provenance `runHeadSha c…c`,
  `planVersion 1` and the run's `changesDigest`.

**Tests: 21/21 PASS** (plus 25 browser-gate/egress tests unchanged).

## Findings

1. **A headless window is not the viewport.** `--window-size=640,400` produced a
   **640×313** screenshot and a click at the control's DOM box centre missed it
   (outside the visible area, silently). `Emulation.setDeviceMetricsOverride`
   pins the viewport; the drill now asserts the screenshot size and that the
   click lands.
2. **Two `Refused` classes are not one.** `browser_gate.resolve_target` raises
   *its* module's exception; the session's `except Refused` missed it and the
   CLI crashed instead of refusing. The session maps the gate's refusals at each
   boundary, and the per-step handler catches both.
3. **A redirect is judged where it lands — and then quarantined.** The page
   script navigated to `file:///etc/hostname`; the final URL check refused it,
   but the document had already loaded. Quarantine (`about:blank`) before
   raising closes the read path; the request log records that the bytes were
   fetched. Stated in the limitations rather than smoothed over.
4. **Downloads need the browser's own refusal.** A page click on a `download`
   link would otherwise write wherever the profile points. The drill asserts the
   `canceled` event and the absence of the file — two different facts.
5. **Playwright was not added.** The worker has no Node runtime and the image
   deliberately uses Chromium directly. The comparison W10 asks for is done with
   Chromium's own accessibility tree — the data Playwright exposes — against
   pixels; adding a second runtime to obtain the same tree would have widened
   the image for no new evidence.
6. **OmniParser stays optional.** It requires a detector model and adds a
   parsing stage to every screenshot; the AX tree already provides roles,
   names and boxes for accessible controls, and the drill shows they are
   sufficient for this fixture. Not adopted, not claimed.

## Gate position

| W10 exit claim | State |
|---|---|
| Persistent adapter: navigate/inspect/click/type/scroll/screenshot | **pass** — one Chromium, 19 actions, one session |
| Accessibility data compared with visual grounding | **pass** — AX role/name → box centre → pixel and ARIA agree |
| Each action audited and bounded | **pass** — one audit line per step; caps on text, scroll, nodes, bytes, steps, time |
| Redirects tested | **pass** — landing URL re-checked, page quarantined, request log recorded |
| Subresources tested | **pass for local fixtures** — only `file:///ws/...` requested; remote subresources are the proxy's job (inherited from the gate) |
| Private addresses tested | **pass** — literal private/loopback/link-local refused in policy (unit) and unreachable through the allow-list (live) |
| Downloads tested | **pass** — denied, `canceled`, no file |
| Clipboard tested | **pass** — page read denied |
| Credentials tested | **pass** — browser env is PATH/HOME/LANG (+proxy); canary absent |
| Session cleanup tested | **pass** — profile removed, no browser process survives |
| Denied destinations tested | **pass** — file outside workspace, non-allow-listed https, no-proxy https, plain http |
| No claim of external-site readiness | **kept** — local fixtures only; no live site was visited |

## Limitations

- **Prototype, not wired into the agent loop.** The engine's `browser` tool
  still calls the one-shot gate; the adapter ships with a CLI and this drill.
  Wiring it is a separate change with its own action-policy review.
- **A redirect is enforced after it lands** (and the page is quarantined). The
  bytes of a refused file-page were fetched by Chromium's renderer; a
  pre-navigation block needs request interception, which this prototype does
  not implement.
- **Subresource destinations are not re-checked per request for https.**
  Chromium's `file://` rules and the egress proxy are the enforcement points;
  the session records what it saw.
- **AX data comes from Chromium**, not from Playwright. For a control that is
  visually present but not exposed to the accessibility tree, `clickAx` cannot
  find it — `click` by coordinate is the fallback, and that is a deliberate
  capability, not an accessible one.
- **No OCR/visual parser.** Canvas content is reached by coordinates; the
  canvas is a labelled image, so its *semantics* are its label, not its pixels.
- **n=1 per scenario**, one browser build (Chromium 156), one host.
- **Downloads are denied as a class**, so the product cannot yet hand a user a
  downloaded file from a page; that is a product decision, not an oversight.

## Carry-over

- **W11:** realistic responsive pages and visually ambiguous targets; compare
  model-only grounding with an optional parser; re-render and verify actions.
  W11's own drill exists and passed earlier; a current-revision re-run is still
  owed.
- **W12:** governed skill catalog and project memory.
- **W17:** load/saturation for browser processes and the action queue.
- **M01:** native-tool confinement still open; nothing here widened access.
- **Engine wiring:** expose `browser_session` as a tool behind the same gate and
  action policy, with per-run session lifetime, before claiming interactive
  agent browsing.
