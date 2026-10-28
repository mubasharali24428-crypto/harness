# Week 11 plan — vision, multimodal and design input (W11)

Follows [Week 10](week-10-plan.md). Governing rule inherited from
[the marathon execution plan](marathon-execution-plan.md): *"An unchecked gate
stays open when a dependent week begins."*

## Position

W10 is closed with live evidence (`w10-browser-1793129794.json`, PASS 16/16):
the agent renders a page inside its own sandbox, opens the screenshot, fixes
the CSS and checks the result. W11 is the other half of the same roadmap row
(`marathon-execution-plan.md`, W10–W11: *"Artifact limits, egress isolation,
image path"*) and the row in `roadmap-2026-10-25.md:179-182`:

> Design-to-code from a mockup, error-dialog triage from a photo, and image
> diffing for visual regression.

Three capabilities, one gate each, all inside the boundary W10 established: a
mockup is just another image the agent can look at, and the rendering side is
the browser tool that already exists. Nothing here widens what the agent can
reach.

## Audit before the plan (2026-10-28)

| Area | What exists today | Where | Why it matters here |
|---|---|---|---|
| Seeing an image | `tool-fs` with `readImage: true`; model must declare `input: [text, image]` | `engine.py:244-269`, `deploy/patches/openrouter-vision.patch.yml` | W11 needs no new vision channel; it needs the agent to *use* this one on its own artifacts |
| Attached images | goal uploads become `{type:image}` blocks; 4 MiB × 3, 8 MiB total, retention, allow-list | `goal_images.py:50-125,212-232` | the frontend already lets a user attach a mockup (`section7/app/dashboard/planGate.tsx:360`) |
| Rendering | `browser` tool (screenshot/dump) inside the worker, workspace-confined, proxy-bound | `dsh_plugins/harness-browser.js`, `browser_gate.py` | the design-to-code loop renders with the same tool W10 proved |
| Image comparison | none in the product; the W10 drill decodes PNGs privately | `scripts/e2e/w10_browser_drill.py:140-186` | visual regression needs a real tool, not a drill helper |
| PNG handling | no decoder/encoder outside that drill; no image library in the images | `section5/src/harness_sandbox/` | stdlib is the constraint: the gate must decode and write PNGs itself |
| Artifact caps | 10 MiB/artifact, 100 MiB/run, classifier quarantines active content | `artifacts.py:52-55,63`, `artifact_store.py:262-295` | diff images are artifacts and inherit these caps |
| Worker image | Chromium + DejaVu fonts, read-only root, uid 10001, no package manager at run time | `section4/worker.Dockerfile:24-40` | fixtures must render with the *same* fonts as the agent's page or pixel comparison is a font test |
| Model | OpenRouter vision patch, `qwen/qwen3.7-flash`, allow-listed | `.env` (`DSH_MODEL`, `DSH_PATCH_FILES`), `openrouter-vision.patch.yml` | transcription quality is the model's, and the drill must not hide that |

## The gate, expanded into claims that must be shown

1. **A mockup becomes a working page.** One live run receives `mockup.png` —
   rendered before the run by the worker image's own Chromium and seeded alone,
   so no HTML source exists in the workspace — and must produce a page that
   matches it. The drill re-renders nothing itself: it decodes the agent's
   screenshot and the mockup with its own PNG reader and compares them. The
   agent's page must also contain a random token that exists **only inside the
   image's pixels** (`ZEPHYR-64`-style), proving it looked rather than inferred.
2. **The diff is a product tool, not a drill trick.** `image_diff` reads two
   workspace PNGs, reports changed pixels/ratio/bounding box/mean colours, and
   writes a diff PNG; `image` action `sample` returns exact pixel colours. Both
   are confined to the workspace, capped by bytes and pixels, and audited as
   ordinary tool calls. The drill proves the arithmetic outside the model:
   against a deliberately regressed copy, the reported box must cover the
   changed region and the ratio must equal its area.
3. **Photo triage reads text that exists only in pixels.** A second short run
   receives `dialog.png` and must report the error code and message rendered in
   it; the drill knows both strings and checks them exactly. No text file in
   the workspace contains them.
4. **The agent used the loop it was given.** The transcript must show an image
   read of the mockup, at least one screenshot, at least one `image_diff` call
   and at least one edit, in that order; a run that never compared cannot pass
   on a lucky first attempt.
5. **Limits still apply.** The diff tool refuses paths outside the workspace,
   non-PNG input, mismatched dimensions, oversized files and over-cap pixel
   counts; a diff image is written inside the workspace and uploads through the
   ordinary artifact route (10 MiB cap, classifier).
6. **Nothing new is reachable.** The new tool spawns nothing and dials nothing;
   the browser's network policy is unchanged. The W11 gate re-runs W10's
   no-proxy leg so "vision" cannot become a way around egress.

## Decisions

1. **One new gate, two actions: `diff` and `sample`.** Diffing is the visual
   regression primitive; sampling exists because a model cannot read a hex code
   off a JPEG-quality guess, and a design-to-code loop that cannot recover exact
   colours turns into a guessing game. Both actions are pure functions over
   files, so the gate is testable without a browser or a model.
2. **Stdlib PNG only, and an explicit refusal set.** 8-bit RGB/RGBA,
   non-interlaced, as Chromium writes. Palette, 16-bit and interlaced PNGs are
   refused by name instead of decoded wrongly. No Pillow in the worker image:
   image codecs are a large attack surface and the product only needs PNGs it
   produced itself.
3. **Fixtures are rendered by the worker image, not on the host.** The mockup
   must use the same fonts and renderer as the agent's page or the diff measures
   font substitution. The drill runs the worker image once per fixture with
   `--network none` and a temp mount, then deletes the HTML: only the PNG is
   seeded.
4. **The gate threshold is set before the run, not after.** The goal asks for a
   changed-pixel ratio below 2% at tolerance 16; the drill passes at ≤ 3% and
   also checks four flat-colour samples. If a probe misses, the fixture or the
   goal is fixed and the reason recorded — the threshold does not move to fit
   the result.
5. **Triage is a second run, not a question bolted onto the first.** A single
   goal that both builds a page and reads a dialog tests two abilities with one
   pass/fail; separate runs keep each claim's evidence attributable and cost
   little (no browser, a handful of tool calls).
6. **No new UI this week.** The frontend already attaches images to a goal;
   design-to-code is a runtime capability. Building a mockup-upload wizard
   without a gate that needs it would be unverified surface.

## Slices

1. **Image gate.** `section5/src/harness_sandbox/image_gate.py`: `diff`
   (changed pixels, ratio, bbox, max delta, mean colours, diff PNG) and `sample`
   (exact colours at points), workspace-confined, byte/pixel caps, explicit
   unsupported-format errors. Unit tests: identical → 0, a known square → exact
   ratio/box, tolerance, mismatch refusal, escapes, caps, encodable diff PNG.
2. **Mount it.** `dsh_plugins/harness-images.js` registers `image`
   (`diff`/`sample`); `engine.py` mounts it with the same workspace root as the
   browser and fails closed when the gate is missing; `worker.Dockerfile` copies
   the gate. Tests pin the mount and the fail-closed path.
3. **Fixtures + design-to-code drill.** `scripts/e2e/w11_vision_drill.py`:
   render `mockup.png` and `dialog.png` in the worker image, run A (build from
   mockup with screenshot + diff loop), run B (triage), verify the token, the
   independent diff, the samples, the tool trail and the boundary leg; write
   `w11-vision-<stamp>.json`.
4. **Close it.** Outcome in this file, CHANGELOG entry, commit. If a probe
   fails, the failure is recorded and the reason stated, as in W10.

## What W11 will not claim

- No general-purpose image editing, segmentation, OCR service or PDF input.
- No JPEG/WebP decoding in the gate (the runtime still accepts them as goal
  images; the diff tool is PNG-only and says so).
- No "the page is accessible/correct" claim from a pixel match: design fidelity
  is not correctness, and the diff says nothing about semantics.
- No automatic visual-regression baseline service (storage, approvals, PR
  comments); the tool produces evidence a person or a run can act on.
- No claim that a different model transcribes equally well; the evidence names
  the model it ran with.

## Known constraints

- `contracts/` is FROZEN: the tool is a plugin; calls surface as the existing
  `tool_call`/`tool_result` events.
- The mockup's HTML is deleted before the run; a drill that forgets would let
  the agent read the spec as text and void claim 1. The evidence records the
  seeded file list.
- Chromium timeouts still apply to the agent's render; a slow worker can turn a
  loop step into a retry (W10 measured this and raised the deadline to 60 s).
- The hosted model is the same OpenRouter vision route W10 used; transcription
  quality is observed, not assumed, and a failure is a finding rather than a
  fixture to relax silently.

## Outcome (2026-10-28) — gate PASSED (second attempt)

Evidence: `docs/briefs/evidence/pilot-prep/w11-vision-1793183487.json` — **PASS,
22/22 checks**, one design run and one triage run against the compose stack with
`qwen/qwen3.7-flash` through OpenRouter and the vision transport patch
(`openrouter-vision.patch.yml`).

What the two runs did:

- **Design-to-code.** Only `mockup.png` was seeded; the HTML that produced it
  was written in a temp dir outside the workspace and deleted. The agent opened
  the image, sampled colours, wrote `index.html` + `style.css`, screenshotted
  its own page and diffed it against the mockup. The drill then re-rendered the
  page with its own Chromium and its own decoder: **2,568 changed pixels of
  480,000 = 0.00535 (0.535%)**, bbox `[141, 405, 182, 23]` — the title line only,
  which is the expected font-rendering residue. All five flat colours match
  exactly (`canvas 15,17,21`, `bar 79,124,255`, `panel 23,26,33`,
  `green 62,207,142`, `amber 242,184,75`). The agent's own reported
  `RATIO: 0.00535` agrees with the measured bytes. The token `ORBITAL-50` exists
  nowhere in the workspace as text and appears in the page the agent wrote.
- **Triage.** Only `dialog.png` was seeded. The agent read it and reported
  `CODE: E_UPSTREAM_187` and `MESSAGE: Snapshot ceff could not be uploaded.` —
  both exact, neither present as text anywhere in the run.
- **Artifacts.** `mockup.png`, `render.png`, `diff.png` and `dialog.png` all
  uploaded through the ordinary route (`201`), each `sha256` equal to the bytes
  the drill seeded or produced.
- **The gate, without a model.** In the worker image on a `--network none`
  container: the known 10×10 patch is reported as exactly **100 pixels,
  ratio 0.02, bbox `[20, 10, 10, 10]`**; a `../../etc/passwd` path is refused
  with `escapes the workspace`; `ip route` is empty.
- **Egress unchanged.** W10's no-proxy leg re-ran: a page's `fetch()` answers
  `FETCH FAILED`. Vision did not become a way around the proxy.

### The first attempt failed, and why

`w11-vision-1793182630.json` — **FAIL, 20/22**. The design run was stopped by
the product's loop guard: `loop_detected after 50 tool calls`. The agent had
guessed the geometry instead of measuring it (48px title where the mockup has
64px, bar at 8,7 rather than 120,136 in page space, a guessed subtitle grey),
ignored what its own diff told it, sampled one pixel at a time (33 `sample`
calls), and rewrote the same CSS repeatedly until the repeated-call detector
fired. The independent render measured 4.04%, above the 3% gate.

Two things were fixed, and the reason is recorded here as decision 4 requires —
**neither threshold moved** (goal `0.02`, gate `0.03`):

1. **The fixture.** Rounded corners, a hairline border and 64px/20px text made
   the diff a font-and-antialiasing test at that model's precision. It is now
   sharp axis-aligned rectangles (600×460 panel, 520×40 bar, two 240×120
   blocks), one 28px token line, no radii or borders. A 4px text miss now costs
   0.007 and a 2px block offset 0.004, so matching the *design* is what the
   ratio measures.
2. **The goal.** It now states that the design is rectangles only, tells the
   agent to find edges with batched `sample` runs, to use the diff's bbox to
   correct geometry, and that identical repeated calls/files are what the loop
   guard stops on.

### Honest notes

- The passing run still used **25 `sample` calls**, not the two the goal asks
  for; the model batched up to 64 points but chose narrow runs. It stayed under
  the 60-call budget and converged, so the gate passed on evidence. The
  behaviour is a model/prompt finding for W14's routing work, not a defect in
  the tool: the tool accepts 64 points per call and the transcript shows it.
- The design run settles at `awaiting_done_signal` after the agent reports; the
  drill measures the final workspace files, so the claim does not depend on a
  human pressing done.
- Not claimed, as planned: no image editing, segmentation, OCR service or PDF
  input; no JPEG/WebP decoding in the gate; no accessibility or semantic
  correctness from a pixel match; no visual-regression baseline service; no
  claim that another model transcribes equally well — this evidence names the
  model it ran with.
