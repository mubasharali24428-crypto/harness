# YC demo recording — runbook (2026-11-01)

One take, about 2 minutes, your voice over a screen recording (macOS: Cmd+Shift+5 → "Record Entire Screen", Options → your mic).

## What works today, checked on the live stack (image tag w24b1)

| Step | Where | Status |
|---|---|---|
| Create a run | Web Command Center, or VS Code `Harness: Run on selection` | Works. Checked both. |
| Approve the plan | Web "Approve plan", or the VS Code plan dialog | Works. The plan text is a fixed template (W24 finding 4), so don't read it aloud as "the agent's plan". |
| Agent works live in the sandbox | Web run page, Overview tab | Works. Steps stream in live (`run-09b5188ca848`, 21 steps, $0.006). The labels are generic ("tool call / tool result"). A short task finishes in **13–26 s**. |
| Pause / Resume | Web run page, Overview: the **Pause** button beside the state pill (added 2026-11-01, frontend image `w24b2`) | **Works live** (`run-9101a0a2e853`). Pause kills the agent's process. In my test the step count stayed frozen for 10 s and the page says "it cannot change files until you resume". Resume carries on from the same session. (The small Pause buttons in the agent tree are a different thing: they only change the record.) |
| Redirect | Web run page, Team tab → Join this run → Become session leader → type in "Direction for the agent" → Send direction | **Works when the run is paused.** The notice says "The agent gets this when the run next starts or resumes." The database recorded `guidance.delivered` once the resumed agent call finished. It is never injected mid-step. |
| Review summary | VS Code, the "Record this result" dialog | Works: files, tests passed, cost, time. The web card shows nothing until a diff is recorded. |
| Reject one, approve the rest with Alt+Y / Alt+N | VS Code | **Works. Rehearsed twice on the real installed .vsix** (`run-fbe42f778559`, `run-9c6bde223d4c`): Alt+Y on calc.py, Alt+N on shout.py. Final notice "1 applied, 0 deleted, 1 refused". The server recorded calc.py approved, shout.py rejected, and each digest matches its patch. |

The web run and the VS Code run are **two separate runs**. The extension reviews only the run it started; it cannot open a web run's diff.

## Before you record

1. Run `scripts/demo/yc_demo_vscode.sh`. It resets `~/harness-demo` (calc.py with `a - b`, shout.py with `.lower()`, two failing tests) and opens a clean VS Code window with the extension installed. Re-run it before every take.
2. In that window: Cmd+Shift+P → `Harness: Sign in` → your email → get the code with `scripts/demo/yc_demo_vscode.sh --otp you@example.com`. Sign in **before** you start recording.
3. Web: open `http://localhost:3010` (not 127.0.0.1). Project `demo-repo`.
4. Turn on Do Not Disturb. Close other windows.

## Shot list and voiceover (rough. Say it in your own words.)

**0:00 — Web, Command Center.** Type a goal → Propose plan → Approve plan → Start run.
The `demo-repo` workspace starts **empty**, so give it a goal that creates files, for example:
`Create temperature.py with celsius_to_fahrenheit and fahrenheit_to_celsius, add test_temperature.py, and run python -m unittest`.
(A "read the repo" goal finds nothing to read.)
> "This is Harness. I describe a task, the agent proposes a plan, and nothing runs until I approve it."

**0:20 — Click "Open shared run" → Overview.** Let the steps tick in (wait about 1 s for the first render).
> "It's running now, in a locked-down container: no network beyond what's allowed, no access outside the repo. Every tool call is recorded, and so is the cost."

**0:35 — Click Pause** (beside the "Working" pill). The pill turns "Paused" and the steps stop.
> "I can stop it at any moment. That actually kills the agent's process, so it can't touch a file until I say so."

**0:45 — Team tab → Join this run → Become session leader → type a direction → Send direction.** It shows "The agent gets this when the run next starts or resumes."
> "Now I change course: say, also handle Kelvin."

**0:55 — Back to Overview → Resume.** New steps appear.
> "It picks up where it left off, with my direction."

**1:00 — VS Code.** Open calc.py → Cmd+Shift+P → `Harness: Run on selection` → Whole file → Nothing else → type:
`Fix add() in calc.py and shout() in shout.py so that python -m unittest passes`
→ the plan dialog → **Approve**.
> "Same thing from the editor. The plan goes through the same gate."

**1:20 — The "Record this result" dialog.**
> "Here's the review summary: which files changed, the tests the agent ran and that they passed, what it cost, and how long it took. It's built from what actually happened, not from what the agent says."
→ **Confirm**.

**1:35 — Per-file review.** On `calc.py`, press **Alt+Y**. On `shout.py`, press **Alt+N**.
> "Now I go file by file. calc.py, yes. shout.py, I don't want that, so no. Only what I approved gets written."
Final notice: "1 applied, 0 deleted, 1 refused". Optional: run `python3 -m unittest test_calc` in the terminal.

**1:55 — Close.**
> "Plan approval, sandboxed execution, live steering, and a human decision on every change. That's Harness."

## If something goes wrong mid-take

* The run fails or times out: check `docker ps` (all harness-* containers up), then re-run the launcher and start over.
* The Alt keys do nothing: click the notification's Apply / Skip buttons. They do the same thing.
* The web run page is blank: wait a second; it renders after the first fetch.
* The run finishes before you click Pause: give it a longer goal. Short goals finish in 13–26 s.
* Rolling back the Pause button: `HARNESS_IMAGE_TAG=w24b1 docker compose -p harness --env-file .env -f deploy/docker-compose.yml up -d --no-deps --no-build frontend`

## Rehearse it again (automated, ~$0.002 per run)

```
node clients/vscode/scripts/w24_window_run.mjs --demo
```
Evidence and screenshots go to `docs/briefs/evidence/w24/w24-window-*.json/png`.
