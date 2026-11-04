# Marathon 2.0 W11 — a model's click is executed, then judged (M2-W11)

Follows the 2026-10-28 Marathon 2.0 roadmap, the W11 row, and
[the W10 outcome](m2-w10-plan.md). The earlier W11 pass proved dialog triage
from a photo and image diffing, and design-to-code from a mockup in one run —
the design leg has also failed on model-side stops since, and the re-run table
below records that rather than averaging it away. This pass takes the other half
of the row: **a vision model names a control, the harness clicks where it said,
and the page — not the model — decides whether the click was right.**

Governing rules: *evidence over claims*, *deny when a boundary cannot be
enforced*, *an unchecked gate stays open when a dependent week begins*, and
*visual similarity is not semantic or accessibility correctness*.

## Purpose

W11 asks for:

1. **realistic pages** — responsive layout, a near-identical pair of controls,
   a canvas control and a control that looks like a button but is not one;
2. **model-only grounding compared with structured alternatives** (the
   accessibility tree; a visual parser stays optional);
3. **independent verification** — re-render after the action and read the
   page's own state, never the model's own claim;
4. **before/after and action evidence** attached to a reviewed run;
5. **honest reporting**: false clicks, inaccessible controls and unsupported
   models are reported as such, not smoothed into a success rate.

## Audit (2026-10-28)

| Area | Observation | Where |
|---|---|---|
| Design→code, triage, diff | live drill, 22 checks PASS (earlier W11 pass) | `scripts/e2e/w11_vision_drill.py` |
| Vision model gate | `HARNESS_VISION_MODELS` refuses a text-only model before any image is sent | `section4/.../goal_images.py` |
| Coordinate grounding | **missing.** No path took a model's (x, y) and acted on it | — |
| Independent verification | the W10 adapter can already re-read `#log`, `aria-pressed` and a canvas pixel | `browser_session.py` |
| Accessibility baseline | `clickAx` exists; `box` (role+name → geometry) did **not** | `browser_session.py` |
| Canvas / div controls | AX exposes the canvas as `image` and the div as `StaticText`; neither is operable by role | fixture |
| Visual parser (OmniParser) | optional; not adopted (W10 finding stands) | — |

The gap was not "can a model point at a button". It was that **nothing checked
the pointer**.

## What changed

### `section5/src/harness_sandbox/browser_session.py`

One new action, `box`: find an AX node by role+name, read `DOM.getBoxModel`,
and return the centre and the box size. It is the structured answer to the same
question the model is asked, it is audited like every other action
(`"action": "box"`), and `clickAx` now shares the geometry code.

| action | what it does | what it is for |
|---|---|---|
| `box` | AX role+name → `(x, y, width, height)` + `backendNodeId` | the accessibility baseline for grounding |

The module still ships in `/opt/harness-shell/` beside the gate
(`section4/worker.Dockerfile`); the W10 policy and bounds are unchanged.

### Fixture: `scripts/e2e/fixtures/w11_grounding.html`

A dark “Workspace” page at 900×640 with:

* **two near-identical buttons** — “Delete file” and “Delete folder” — in a
  two-column grid that collapses to one column under 700 px;
* a **canvas switch** (`<canvas role="img" aria-label="Auto-save switch">`)
  painted `#444b5a` (off) / `#1a7f37` (on) — a switch to a person, an `image`
  to the accessibility tree;
* an **`Export CSV` div** styled like a button, clickable by script, exposed to
  the accessibility tree only as `StaticText` — no role, no keyboard path;
* `#log` text that records exactly which control fired;
* an optional `?grid=1` 100 px overlay, offered to the model as a grounding aid.

### Drill: `scripts/e2e/w11_grounding_drill.py`

The container is `--network none`, read-only, cap-dropped, uid 10001 — the same
confinement W10 proved. One Chromium session per step.

Five tasks, each: navigate → screenshot → inspect → AX `box` (ground truth) →
**one image and one instruction to the model** → the harness converts the
answer to pixels and clicks there → wait → inspect + screenshot. The verdict is
read from the re-rendered page: `LOG:` text, and for the canvas the pixel at the
click point.

| task | viewport | target |
|---|---|---|
| `easy-target` | 900×640 | one obvious button |
| `delete-folder` | 900×640 | “folder”, among a look-alike “file” button |
| `canvas-switch` | 900×640 | canvas switch, `image` to AX |
| `export-div` | 900×640 | `Export CSV`, `StaticText` to AX |
| `canvas-narrow` | 480×800 | the same switch after the responsive reflow |

Deterministic legs (no model), so harness capability is proven separately from
model quality:

* AX baseline: `clickAx button "Delete folder"` must log `delete-folder`;
* pixels-only canvas: `box image "Auto-save switch"` → click centre → the
  canvas pixel must turn `#1a7f37`;
* pixels-only div: `box StaticText "Export CSV"` → click centre → `export`;
* AX failure, asserted: neither the canvas nor the div is reachable as a
  button (`no AX node with role='button' …`);
* false-click detector: instruct “Delete folder”, click “Delete file”, and show
  the verifier flags it — the detector is proven independently of the model;
* unsupported model: the product refuses `deepseek/deepseek-chat` before any
  image is sent, and the provider itself answers 404 "No endpoints found that
  support image input" when an image is attached;
* before/after screenshots of the canvas task attached to a real run as a W9
  visual pair, carrying the run's head, approved plan version and changes
  digest.

### Tests

`section5/tests/unit/test_browser_session.py` grows to 23: the centre and size
of an AX box, the audit line, `clickAx` sharing the same geometry, dispatch
routing for `box`, and an off-viewport centre refused — with a fake CDP, no
browser needed. The W10 policy and wire tests are unchanged.

## Coordinate contract — measured, not assumed

The model was first asked for pixel coordinates. On the calibration page (one
button, page-like layout) it answered close to pixel space (74 px from the
centre, still outside the 2 % tolerance); on the dense task pages it answered in
a **0..1000 grid**. The same model changed its convention with page density.

The drill therefore states the contract explicitly — `0..1000` in both axes,
`1000` = width/height — and the harness converts. The pixel contract is kept as
a recorded probe (not a gate): it documents that asking for pixels is not
reliable enough to gate on.

A second failure mode is visible in the answer stream: the model is
reasoning-heavy, and some calls end `finish=length` with `content: null` after
consuming the whole 3000-token budget. Each task gets at most two attempts; a
second empty answer is recorded as **no answer**, not as a guess.

## Outcome (2026-10-28)

**Grounding drill, live: 22/22 checks PASS.**
`pilot-prep/w11-grounding-1793208723.json`

Measured on `qwen/qwen3.7-flash` (the configured `DSH_MODEL`): 6 task attempts
(one retry on the narrow task) plus 2 calibration-probe calls — 8 API calls,
**$0.0026**:

| task | model said | executed at | page said | verdict |
|---|---|---|---|---|
| easy-target | “Publish release” | (457, 315) | `publish` | **hit** (+7, −5 px) |
| delete-folder | “Delete folder” | (608, 210) | none | **false click** (+65, +73 px) |
| canvas-switch | “Auto-save switch” | (161, 446) | none; pixel still off | **false click** (+21, +157 px) |
| export-div | “Export CSV” | (104, 351) | `export` | **hit** (+6, −5 px) |
| canvas-narrow | — (2 × `finish=length`, no content) | — | none | **no answer** |

* **Labels: 4 of 4 answered.** When the model produced an answer, it named the
  right control every time — including “folder” against a look-alike.
* **Coordinates: 2 of 5 landed.** Both hits were on the two controls whose
  truth boxes are 85–288 px wide. The misses were 65–157 px off, which is a
  real wrong click, not a rounding error.
* **Nothing was smoothed over.** Every executed miss is recorded with its
  displacement and judged by the page, not by the model's confidence (the
  folder miss was reported with confidence **1.0**).

Deterministic legs, all PASS:

* AX baseline lands the folder button (`delete-folder`);
* pixels-only operations land both AX-inaccessible controls — canvas pixel
  `#1a7f37`, `export`;
* AX cannot operate either (`image` / `StaticText`, no button node);
* the false-click detector flags the wrong delete button;
* the product refuses a text-only model and the provider 404s an image request;
* responsive viewport honoured: every screenshot matches its requested width
  (900, 900, 900, 900, 480);
* every step of every answered task is present in the session audit;
* the before/after pair is attached to the run with `runHeadSha c…c`,
  `planVersion 1`, the run's `changesDigest`, `stale: false`.

**Vision drill re-run at HEAD: design leg FAILED twice, triage and the
deterministic gate passed both times.**

| run | design leg | triage leg | gate legs |
|---|---|---|---|
| earlier revision, first pass | **FAIL** — `loop_detected after 50 tool calls` | pass | pass |
| earlier revision, second pass | **PASS** — `TOKEN: ORBITAL-50`, ratio 0.00535 | pass | pass |
| HEAD, re-run 1 | **FAIL** — `tool_budget_exhausted after 61 tool calls` (60 pixel samples, no write) | pass (`E_UPSTREAM_563`) | pass |
| HEAD, re-run 2 | **FAIL** — `model qwen/qwen3.7-flash returned an empty response` (2 samples) | pass (`E_UPSTREAM_963`) | pass |

The design leg has now been recorded failing, passing, failing, failing at the
same revision family, with three different model-side stop reasons. That is not
a product regression — the engine bounded every run and reported the reason
instead of hanging, the loop guard fired once, the budget once, and the provider
returned empty content once — but it does mean **the design-to-code gate is not
stably green at HEAD**, and this document does not claim it is. Triage (exact
code and sentence from a photo) and the deterministic diff gate (exact 10×10
patch reported, path refusal, `--network none`) passed in every run, including
both failing ones.

Evidence: `w11-vision-1793182630.json` (loop), `w11-vision-1793183487.json`
(pass), `w11-vision-1793209063.json` (budget), `w11-vision-1793209463.json`
(empty response).

## Findings

1. **Coordinate convention is unstable, and confidence does not track error.**
   The same model answered in pixels on a sparse page and in a 0..1000 grid on
   dense ones; a pick 73 px off was reported with confidence 1.0. A contract in
   the prompt and a harness-side conversion make the answer usable; neither
   makes it accurate.
2. **Naming is not grounding.** 4/4 labels, 2/5 coordinates. “The model found
   the button” and “the model clicked the button” are different claims, and only
   the second one is an action.
3. **Reasoning truncation reads like refusal.** `finish=length` with
   `content: null` occurred twice on the narrow canvas task; without the
   recorded attempt list it would look like the model declined. It is a budget
   fact, and it is reported as a no answer.
4. **Accessibility is not “can it be clicked”.** The canvas and the div are
   pixel-operable and AX-inaccessible. A model can click them; a screen-reader
   user cannot. The drill reports the gap instead of counting the clicks as
   accessibility success.
5. **The verifier is the product, not the prompt.** Clicks are judged from
   `#log` and the canvas pixel after a fresh render. The false-click detector
   leg proves the judge fails a wrong click even when no model is involved.
6. **A parsers-free baseline is enough for this fixture.** OmniParser would add
   a detector model per screenshot for controls the AX tree already boxes; the
   AX baseline lands both accessible targets. Not adopted, not claimed.
7. **A bounded run and a successful one are different claims.** The design leg
   of the earlier drill stopped four times across four runs: loop, pass, budget,
   empty response. Each failure was the engine correctly ending a run it could
   not finish — not a silent hang and not a product pass. Stated, and the gate
   is left open.

## Gate position

| W11 exit claim | State |
|---|---|
| Realistic responsive pages | **pass** — two-column grid, 700 px reflow, 480×800 task |
| Canvas controls | **pass** — canvas switch operated by pixels and verified by pixel; reported as `image` to AX |
| Visually ambiguous targets | **pass** — look-alike delete buttons; label correct 4/4, coordinate 2/5 recorded |
| Model-only grounding vs optional parser | **pass** — model compared with AX `box`; parser not adopted, stated |
| Independently re-render and verify actions | **pass** — verdict from `#log` and canvas pixel after re-render |
| Before/after and action evidence | **pass** — W9 visual pair attached with run revision; per-action audit |
| False clicks reported | **pass** — displacement recorded for every executed miss |
| Inaccessible controls reported | **pass** — canvas and div listed with `operableByAx: false` |
| Unsupported models reported | **pass** — product refusal and provider 404 both recorded |
| Visual similarity is not semantic/accessibility correctness | **kept** — pixels prove the click, not the meaning; AX gap stated |
| Design→code, triage, diff (earlier W11 pass) | **triage and diff pass at HEAD; design→code OPEN** — passed once, then failed twice at HEAD on model-side stops (loop guard, tool budget, empty response). Not claimed as green. |
| Vision drill re-run owed by W10 | **done, with a failure recorded** — two re-runs, results in the table above |

## Limitations

- **Model quality is measured, not fixed.** 2/5 coordinate hits is the result for
  this model at this prompt; the drill does not claim grounding is solved, and
  a different model or prompt would need its own run.
- **n=5 tasks, one model, one browser build, one host.** Small on purpose: the
  claim is about the loop (execute → re-render → judge), not a benchmark.
- **The model sees one image per step**, not a trajectory; no retries with
  feedback after a miss. A retry loop is a product decision (cost, budget) and
  is not implemented.
- **The normalized contract is harness-side.** The model is told 0..1000; if it
  answers in pixels anyway, the conversion is wrong and the click is wrong —
  that is visible in the evidence, not corrected.
- **Local fixtures only.** No external site was visited; the W10 boundary and
  no-external-readiness statement carry over.
- **Canvas semantics are its label.** The switch's meaning (“auto-save”) is the
  `aria-label`; the pixels prove the state change, not the intent.
- **The design-to-code leg is model-flaky.** Four runs produced loop, pass,
  budget and empty-response outcomes. No retry-until-green was performed and
  none is claimed; the leg stays open for W12 with a tighter task or a prompt
  that does not invite exhaustive pixel probing.
- **Not wired into the agent loop.** `browser_session`, including `box`, still
  runs through its CLI and drills; the engine's browser tool is unchanged.

## Carry-over

- **W12:** governed skill catalog and project memory.
- **W12 also:** the design-to-code leg — stable behavior at HEAD (a task shape
  that does not reward 60 pixel samples), then a green re-run.
- **W17:** load/saturation for browser processes, model calls and the action
  queue under concurrent runs.
- **M01:** native-tool confinement still open; nothing here widened access.
- **Engine wiring:** expose grounding as a tool behind the W10 action policy —
  with a retry budget and an explicit “give up” path — before claiming agent
  browsing.
- **Optional parser:** re-evaluate OmniParser only if a fixture appears where
  the AX baseline cannot box the target at all (e.g. icon-only chrome).
