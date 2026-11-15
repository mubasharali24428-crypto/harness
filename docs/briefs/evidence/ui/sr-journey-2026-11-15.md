# Accessibility-tree journey: sign in to plan approval — 2026-11-15

Produced by `section7/scripts/sr-journey.mjs` against http://localhost:3011 (Chromium, keyboard only).

**This is not a screen-reader recording.** It records what the browser exposes to assistive
technology (focus order, roles, accessible names, live-region text). How VoiceOver or NVDA speak
it still needs a person. The plan's "recorded manual screen-reader journey" is therefore **not met**.

## Findings

- None from this automated pass.

## Journey

### 1. Sign in

| Key | Step | Focus lands on (role: name) |
|---|---|---|
Page title: "AI Engineering Harness". Headings: ["HARNESS"]

| Key | Step | Focus lands on (role: name) |
|---|---|---|
| `Tab` | reach the email field | textbox: "Email address" |
| `Enter` | submit the form | main: "New runDescribe the goal. The agent proposes a plan, and nothing runs before you approve i" |

Live regions now read: (nothing)


### 2. Seed the demo project (a fresh team has none)

| Key | Step | Focus lands on (role: name) |
|---|---|---|
| `Tab` | reach the demo button | button: "Try the demo" |
| `Enter` | start the demo | main: "REPLAY · a recorded session, clearly labeled. No provider keys, no live execution.textstat" |

Landed on: /projects/demo-proj-f70c93233f22/runs/demo-run-59a12a634cbb?tab=changes&demo=1. Headings: ["NEEDS YOU\n1","WORKING\n0","DONE\n0","textstats.stats.median_word_length currently raises NotImplementedE…","Diff","Your decision","src/textstats/stats.py\n+7 −1"]

Live regions now read: "REPLAY · a recorded session, clearly labeled. No provider keys, no live execution. / 0 of 1 files marked reviewed"


### 3. New run: type a goal, propose a plan

| Key | Step | Focus lands on (role: name) |
|---|---|---|
| `Tab` | reach the goal input | link: "Skip to content" |
| `Tab` |  | combobox: "PROJECT" |
| `Tab` |  | link: "New run" |
| `Tab` |  | link: "textstats.stats.median_word_length currently raises NotImplementedE…Review the pull reques" |
| `Tab` |  | link: "Notifications, none unread" |
| `Tab` |  | button: "Go to…⌘K" |
| `Tab` |  | disclosure: "Keyboard" |
| `Tab` |  | button: "Got it" |
| `Tab` |  | disclosure: "Menu" |
| `Tab` |  | button: "Switch to dark theme" |
| `Tab` |  | button: "Sign out" |
| `Tab` |  | textbox: "Goal for a new run" |

Tab presses from the top of the page to the goal input: 12

| Key | Step | Focus lands on (role: name) |
|---|---|---|
| `Enter` | propose the plan | main: "Add a docstring to word_countWaiting for someone to approve the plan·Spend unknown·Estimat" |

Landed on the run. Headings: ["NEEDS YOU\n2","WORKING\n0","DONE\n0","Add a docstring to word_count"]
Focus after the page change: main: "Add a docstring to word_countWaiting for someone to approve the plan·Spend unknown·Estimat"

Live regions now read: (nothing)


### 4. Approve the plan

| Key | Step | Focus lands on (role: name) |
|---|---|---|
| `Tab` | reach Approve plan | button: "Diff" |
| `Tab` |  | button: "Plan" |
| `Tab` |  | button: "Terminal" |
| `Tab` |  | button: "Evidence" |
| `Tab` |  | button: "Advanced" |
| `Tab` |  | disclosure: "Plan proposed8:23:21 PM" |
| `Tab` |  | disclosure: "Plan3 steps · waiting for approval" |
| `Tab` |  | button: "Approve plan" |

Tab presses to reach Approve plan: 8

| Key | Step | Focus lands on (role: name) |
|---|---|---|
| `Enter` | approve | button: "Start run" |

After approval, focus is on: button: "Start run"

Live regions now read: "Plan approved. Start the run when you are ready."

