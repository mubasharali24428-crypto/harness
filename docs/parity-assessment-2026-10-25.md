# Parity assessment — what it would take to stand next to Codex / Claude Code

2026-10-25. Written after reading the engine, the run surface and the SDK seam,
not from a feature checklist. Effort is in focused engineer-days; "blocked"
means it needs a decision from you, not just code.

## First, what we are already ahead on

These are not consolation prizes — they are the things Codex and Claude Code do
*worse*, and they were the expensive parts to build:

| | Us | Them |
|---|---|---|
| Where code runs | OS-enforced sandbox: Landlock ABI + allow-listed shell gate + egress proxy, per-run workspace | the user's own machine, with a permission prompt |
| Approval model | plan gate with versioned plans, forbidden paths, budget caps, human approve before any execution | per-command y/n, or full-auto |
| Reversibility | pre/post-edit git checkpoints per batch, restorable, plus a durable run queue | undo via git, ad hoc |
| Multi-actor | agent tree with assignments, meetings, handoff, leader/participant direction queue | single user, single agent |
| Metering | per-team cost ledger, tier caps enforced in code, BYO keys across 65 providers | per-account, provider-locked |
| Audit | append-only event bus + audit trail, secret and reasoning scrub on export | transcripts |

The honest summary: we built the parts that matter for *operating agents on a
team's code*, and we have not yet built the parts that make a single developer
feel the run is happening.

## The gaps, ranked by how much they cost us

### 1. There is no live transcript — the run looks dead while it works (~5 days)

The SDK emits `tool/call` and `tool/result` events with callId, tool name,
arguments and `isError`. `_fold_tool_calls()`
(`section4/src/harness_orchestrator/engine.py:524`) reduces them to
`{callId, tool, target, outcome}` and attaches the list to the **final** result
(`engine.py:520`). So the data exists and we throw away its timing, its output
and its ordering.

This is the single biggest perceived-quality gap: Claude Code shows every
command scrolling past with its result; we show "10 latest of 10" after the
fact. Fix has three parts — stream per tool call as it happens, add the event
type, render it. **Decision needed:** frozen `contracts/` has no tool event
(`contracts/ts/src/events.ts:4-19`), so either we version the contract with
`tool_call` / `tool_result`, or we ride the existing `terminal_output` payload
with a convention. I would version the contract; the convention rots.

### 2. Pause and Stop do not stop anything (~6 days, partly upstream)

`agentTree.tsx:78` already admits it: "They do not stop a running process yet."
The worker is a separate process running `harness.run(goal)` to completion;
there is no cancel channel and no way to inject direction mid-step
(`sharedRunPanel.tsx:245` says the same). Interrupt needs a signal path into the
worker plus a queue the SDK drains between turns. Whether the SDK honours a
stop cleanly between tool calls is unverified — check before committing to the
number.

### 3. No context management of our own (unknown, 3–10 days)

Nothing in `section4/src` compacts, summarizes or trims; we inherit whatever
the `sdk-minimal` profile does. Two concrete problems I did verify: `max_tokens`
is hardcoded to 4096 for every model (`engine.py:602`), and there is no
budget-aware "what's in the window" affordance anywhere in the UI. For long
runs in a big repo this is what makes an agent look stupid before it is.

### 4. The agent does not know the repo (1–2 weeks)

Section 6 has a context index with tier limits, but the engine's input is the
goal plus the plan — I found no path where the agent queries the index. Codex
and Claude Code both build a repo map / retrieval step before editing. This is
the biggest *quality* gap after the transcript, and the most expensive.

### 5. Nothing else is fast to use (~1 week, mostly Batch C/D of the audit)

No copy buttons anywhere. No keyboard model for the hunk review loop
(`j`/`k`, accept/reject). No elapsed timer, no token/cost footer, no ETA.
Streaming dies quietly on a dropped socket. Default tab on a live run is the
non-live one. Individually small; together they are the difference between
"tool" and "product".

### 6. What the SDK seam actually gives us (verified in the installed package)

Checked `deepseek_harness` in `section4/.venv` rather than assuming:

- `DeepSeekHarness.run(input, *, session_id, on_notification)` takes a
  **notification callback**, and our engine passes nothing — we call
  `harness.run(goal, session_id=…)` and collect `result.events` afterwards
  (`engine.py:618`). So item 1's "live" half is wiring we already own, not an
  upstream gap; only the event-bus type is blocked on the contracts decision.
- `Session` exposes `run`, `get_working_directory`, `set_working_directory` and
  **no cancel or interrupt**. `IncomingRequest` exists in the exports, which
  suggests the protocol supports server→client requests we could answer. So
  item 2 stays the harder one: today the honest route is terminating the worker
  process and resuming from the checkpoint, which is coarse but real.
- The profile is `sdk-minimal` (`engine.py:51`) chosen deliberately — the
  comment says a bad environment override must not select an unreviewed tool
  roster. Anything item 4 does must go through that review, not around it.

### 7. We are web-only (strategic, not a fix)

Codex and Claude Code live in the terminal and the editor, where the code
already is. A CLI or an IDE extension is a different surface with real demand
behind it — and a different distribution decision. Worth an explicit call
rather than drifting toward it.

## What I'd actually do

1. **Contracts decision today** (item 1) — it gates the highest-visibility fix.
2. **Week 1:** live transcript end to end + the audit's Batch C (live-by-default
   run page, real reconnect, copy affordances, cost footer).
3. **Week 2:** interrupt/steer channel, and remove the "looks in control but
   isn't" affordances if it slips.
4. **Week 3–4:** context budget — replace the hardcoded `max_tokens`, add
   window telemetry, decide on compaction.
5. **Then** the repo-intelligence question, which is a project, not a task.

Six weeks of focused work to a run surface that feels like theirs, with our
safety and governance story still ahead. Item 4 is the one that could take
months and change the most, and I would not start it before the transcript
lands — the transcript is what tells you whether the agent's reasoning is good
enough to be worth indexing a repo for.

## What I did not verify

- Whether the notification callback fires per tool call in the order the UI
  needs, and what `Notification` carries — read the type before sizing item 1
  beyond the estimate.
- What the `sdk-minimal` profile does about context overflow.
- Any claim about Codex/Claude Code internals — that comparison is from their
  observable behaviour, not their source.
