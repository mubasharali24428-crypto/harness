# Week 10 plan — browser use, with the network and credentials still confined (W10)

Follows [Week 9](week-9-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Position

W9 is closed with live evidence on both of its claims
(`w9-share-deploy-1793124898.json`, PASS 21/21). The roadmap row this week must
satisfy (`marathon-execution-plan.md`, W10–W11):

> **W10–W11 browser and vision** — Artifact limits, egress isolation, image
> path | Reproduce/fix/recheck UI with before/after evidence; browser
> credentials and network remain confined.

and the roadmap's own W10 text (`roadmap-2026-10-25.md:169-175`):

> Headless browser control inside the sandbox … screenshots back into context,
> and an agent that can verify its own frontend change instead of asserting it.
> **Gate:** a UI bug is reproduced, fixed and re-verified by the agent with
> before/after screenshots attached to the run.

W10 is the browser half. The vision half (design-to-code from a mockup, image
diffing) is W11 and stays open; this plan only claims what the W10 gate needs
from images: a screenshot the agent can look at.

## Audit before the plan (2026-10-27)

| Area | What exists today | Where | Why it matters here |
|---|---|---|---|
| Engine tool roster | `tool-fs` (`readImage: true`), `tool-fs-search`, `harness-shell`, `harness-web` | `section4/src/harness_orchestrator/engine.py:223-241` | there is **no browser tool**; every new capability has to be inserted here, per run |
| Tool plugins | only `harness-shell.js` and `harness-web.js` | `section4/src/harness_orchestrator/dsh_plugins/` | the plugin contract (register, spawn a gate with scrubbed env) is the seam to reuse |
| `web_fetch` | gate at section5, https/443 only, allow-list per purpose, redirect hops re-checked, 2 MB/40k chars | `dsh_plugins/harness-web.js:54-78`, `engine.py:173-193`, `section5/src/harness_sandbox/web_gate.py:97-105,164-170` | a browser must not become a way *around* this policy |
| Egress | per-run `--internal` network, no gateway, only `harness-egress`; proxy 3128 sandbox / 3129 web, CONNECT-only | `container_runtime.py:19-20,66-92`, `egress_proxy.py:1-6,24,61-70`, `docker-compose.yml:508-535` | the browser inherits this boundary or the claim is false |
| Worker sandbox | read-only root, `cap-drop ALL`, `no-new-privileges`, pids 128, 1 g, tmpfs /tmp | `confine.py:397-424` | Chromium has to work *inside* this, not beside it |
| Worker image | `python:3.12-slim` + bash/git; no Node, no browser | `section4/worker.Dockerfile:1-40` | slice 1 adds Chromium; the `worker` stage is the place |
| Vision today | goal images attached as `{type:image}` blocks; 4 MiB × 3 = 8 MiB caps; `HARNESS_VISION_MODELS` allow-list | `goal_images.py:65-69,95-121,212-232`, `engine.py:265-276` | the screenshot-into-context path must not bypass these caps |
| Vision history | M07 confined run attached the image and failed: 37 bash calls in 517 s, no answer; `usage` null | `docs/briefs/evidence/pilot-prep/m07-confined-vision-failure.json`, `CHANGELOG.md:624-628` | proof that "the model can see an image" is not automatic; the tool must make looking the cheap path |
| Artifact limits | 10 MiB/artifact, 100 MiB/run, 200/run, 500 files; classifier quarantines active content | `artifacts.py:52-55,63`, `artifact_store.py:262-295` | screenshots are artifacts; the caps are claims 4's subject |
| Playwright | exists only for section7's own a11y tests, host-side | `section7/scripts/browser-check.mjs`, `section7/playwright.config.ts` | reusable as *fixture*, not as runtime: it is outside the engine boundary |

## The gate, expanded into claims that must be shown

1. **The agent can see what it renders.** Inside a confined run, the agent takes
   a screenshot of a local page and the PNG reaches its own context: it reports
   a fact only the image carries (a colour), not one it could read from the
   source. This is the W1 claim made tool-shaped: the screenshot path exists for
   attachments (`goal_images.py`), never for what the agent produces itself.
2. **The browser has no network it was not given.** A page rendered in the
   browser cannot reach a non-allow-listed host — the run network has no
   gateway and no DNS, and the browser is pointed at the same web-purpose proxy
   as `web_fetch`. A drill page that tries to fetch `https://example.com`
   records the failure; an allow-listed fetch through the proxy still works.
3. **No credential reaches the browser.** The tool spawns its gate with the same
   scrubbed environment `harness-web.js` uses (`PATH`, `HOME`, proxy only); the
   model API key and runner tokens are never in the browser's env, and the
   profile directory is throwaway.
4. **Screenshots obey the product's limits.** A screenshot is written into the
   workspace as a file, then uploaded through the ordinary artifact path: the
   4 MiB image cap, the 10 MiB artifact cap and the classifier all still apply,
   and an oversized render is refused or truncated by the tool rather than by
   the upload's failure.
5. **The gate itself.** One run: the agent screenshots a page with a CSS bug,
   reads the image, changes the stylesheet, screenshots again, and its report
   names the before and after colours. Both PNGs are attached to the run as
   artifacts, and the drill independently samples the two images' pixels — the
   model's words and the bytes must agree.
6. **Every browser call is audited** as a `tool_call`/`tool_result` pair with its
   duration and outcome, like the shell and web tools; a refused path (outside
   the workspace, oversized, denied URL) is visible in the transcript, not
   silent.

## Decisions

1. **The browser runs inside the worker's own netns**, not as a separate
   service: the confinement that already exists (`--internal`, no gateway,
   `cap-drop ALL`, read-only root) is the confinement the browser gets. A
   separate browser service would need its own boundary review and would be the
   first thing to drift from the worker's.
2. **Chromium's own CLI (headless), not Playwright/Node, for the first cut.**
   `--headless --screenshot` and `--dump-dom` cover "render this page, look at
   it, read its text"; the repo already carries no Node runtime in the worker
   and adding one to gain clicking is a decision to take when a drill needs a
   click, not before. The gate is a Python stdlib script like `web_gate.py`, so
   it can be tested without a browser.
3. **`--no-sandbox` inside the worker is acceptable and documented.** Chromium's
   setuid sandbox needs privileges the container deliberately does not have
   (`cap-drop ALL`, `no-new-privileges`); the container *is* the sandbox, and the
   flag is the price of running a browser at all under this boundary — the same
   trade CI images make. What the flag does not change: the run's network,
   filesystem (workspace only) and uid (10001).
4. **A screenshot is a workspace file, and context entry is the existing
   `read` tool with `readImage`.** No new multimodal channel: the tool says where
   it wrote the PNG, and the agent opens it like any other image. This keeps one
   set of size/type rules (`goal_images.py`) rather than two.
5. **The browser uses the web egress purpose (3129)**, the same one `web_fetch`
   uses, so the two tools cannot disagree about what is reachable. A screenshot
   of an allow-listed docs page is therefore possible; a screenshot of anything
   else is not, and the failure is the page failing to load, not a policy
   surprise mid-render.

## Slices

1. **Browser gate + tool mounting.** `section5/src/harness_sandbox/browser_gate.py`
   (stdlib): validates `{url, path, action}`, confines `path` to the workspace,
   spawns Chromium with a scrubbed env and the web proxy, enforces a byte cap and
   a wall-clock timeout, returns `{ok, path, bytes, width?, height?, title?, error?}`.
   `dsh_plugins/harness-browser.js` registers `browser` with
   `screenshot`/`dump` actions, mirroring `harness-web.js`. `engine.py` mounts it
   when the gate and plugin exist (fail-closed otherwise). `worker.Dockerfile`
   installs Chromium in the `worker` stage only. Unit tests with a fake
   executable: path escapes refused, env scrubbing, cap, timeout, non-zero exit.
2. **Screenshot into context and into artifacts.** Wire the file drop
   (`.harness-shots/` subject to the same forbid rules as any workspace write),
   confirm the fs `read` tool returns the PNG as an image block, and make the
   drill's fixture page + bug available. Measure: does `readImage` accept a PNG
   produced this way, at what size?
3. **Live gate drill.** `scripts/e2e/w10_browser_drill.py` on the compose stack:
   seed a page whose swatch must be red but renders white; the agent screenshots
   (before), reads, edits the CSS, screenshots (after), and reports both colours;
   the drill samples both PNGs' pixels and attaches them as artifacts. Plus the
   confinement legs: a page fetching `https://example.com` fails, an
   allow-listed fetch through the proxy succeeds, no credential appears in the
   browser's env. Evidence to `docs/briefs/evidence/pilot-prep/`.

## What W10 will not claim

- No clicking, typing or multi-step navigation: the tool is render-and-look.
  Interaction is the first thing to add when a gate needs it.
- No hosted-browser service, no video, no trace viewer.
- W11's vision work (design-to-code, image diffing, photo triage) stays open.
- A real product UI regression from a running frontend (the gate uses a static
  fixture page in the workspace, because a dev server is a network listener the
  run boundary does not have).

## Known constraints

- `contracts/` is FROZEN: the tool is a plugin, not a contract event; browser
  calls surface through the existing `tool_call`/`tool_result` events.
- The image grows: Chromium plus its Debian dependencies is a few hundred MB in
  the worker image. The `daemon` stage must not inherit it (only `worker` needs
  to render), and the build has to stay reproducible (pinned image base, apt
  from the bookworm archive).
- The model is the hosted OpenRouter one for drills (W9 precedent). A model that
  ignores the browser tool (the M07 failure mode) is a real risk: the drill's
  goal names the tool and caps tool calls, as the W4 web drill does.
- Seatbelt mode (`confine.resolve_mode`) has no egress boundary at all
  (`confine.py:56-61`); the browser claim is a container-mode claim and is
  recorded as such, not extrapolated.
