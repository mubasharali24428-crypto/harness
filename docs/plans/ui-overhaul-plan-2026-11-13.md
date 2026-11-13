# UI overhaul plan: research and phased build (2026-11-13)

Status: **proposal, nothing built.** Owner decisions are listed in §6. This plan is separate from the 90-day
plan's weekly rows; §5 says where it touches them. Source quality is stated in §7: most public writing on these
products is vendor pages and roundup blogs, not user studies, so "people praise X" below means "several
independent write-ups say X", not a measured result.

## 1. Where our UI is today

- Stack: Next.js 15, React 19, CodeMirror 6, one hand-written stylesheet (`section7/app/globals.css`, 1,178
  lines, 64 CSS variables), no component library, no command palette. Tests: vitest, Playwright, axe.
- Screens that exist: login, onboarding, demo, runs list, run page (plan, diff, terminal, tool activity,
  checkpoints, status), review, file tree, cost dashboard, notifications, meeting, settings (9 pages).
- Seen today on the RC stack: login and demo are a single centered card on a dark grid. "Try the demo" sent me
  to login and back to the demo card without starting anything (not investigated; possible bug or a sign-in
  requirement that the page does not explain).
- What it lacks compared with the references below: a home screen that answers "what needs me?", one place to
  see all running agents, a run timeline you can scrub, keyboard-first navigation, a consistent component
  system, and a review flow built around evidence rather than tabs.

## 2. Five harness UIs worth taking from

| Product | What it does well | What to take | What people complain about (avoid) |
|---|---|---|---|
| **Cursor 3** (Agents Window) | A separate window for a fleet of agents beside the editor; tiled layout; diff view with staging and PR management | A dedicated "agents" surface, not agents buried in a chat | Review showed total git changes, not the last iteration; the new window was pushed too hard; overhead for small single-agent tasks |
| **Codex app** | Projects → threads, each thread in its own worktree; the diff pane is the main review surface: inline comments the agent then addresses, stage or revert per chunk or file; PR pane pulls GitHub comments | Diff pane as the centre of review; comment-on-line → agent fixes; per-chunk accept/revert | Little independent user feedback found |
| **Conductor** | One workspace per task (branch, files, chat, terminal, preview, diff); one keystroke opens the diff; removes worktree bookkeeping | "One task = one self-contained workspace" model; single shortcut to the diff | Mac only; parallel agents burn budget fast (we should show spend per workspace) |
| **Devin** | One timeline for shell, edits and browser; click any step to see the state at that moment; "Following" switch that tracks what the agent is doing live; resizable chat | Scrubbable run timeline + follow mode | Tab layout changed several times; older reviews describe a different UI |
| **GitHub Agent HQ** (Mission Control) | One view of all agent sessions across web, editor, mobile and CLI; assign, watch, review without changing pages; a review agent screens PRs before a human | Mission-control home; "pre-review then human" (we already built this as I-10) | Coverage is launch material; no independent design critique found |

Also looked at: **Vibe Kanban** (kanban of agent tasks with inline diff comments; its company reportedly shut
down in April 2026, unverified), **Google Jules** (plan shown before any file is touched, editable/approvable;
reported shut down; its auto-approve option weakened the audit trail, which matters for us), **Warp** (each
command and its output is one block you can copy, collapse and link; critics say long output gets cluttered).

## 3. Beyond harnesses: products with UIs people single out

| Product | The idea | How it applies to us |
|---|---|---|
| **Linear** | Keyboard-first, command palette for every action, optimistic updates, dense but calm dark UI, one accent colour, shortcuts taught in tooltips and menus | Command palette (Cmd+K), single-key shortcuts (`A` approve, `R` request changes, `G then R` go to runs), optimistic approve/comment |
| **Superhuman / Raycast** | Speed as the product; the palette is how shortcuts get discovered; onboarding teaches the keyboard | A 100 ms interaction budget as a test; first-run tour that teaches 5 keys |
| **Vercel** | Status first: production and latest deployment state up top, logs inside the overview, filter and one-click copy, status in the browser tab icon | Run status in the tab icon and title; logs and evidence inside the run overview; copy buttons everywhere |
| **Graphite** | A review inbox split by "needs your review / waiting on author / approved"; small stacked changes | Home screen = inbox of what is waiting on *me*; split big agent changes into reviewable steps |
| **Stripe dashboard** | One primary number per view, clean tables, progressive disclosure | Cost page: one headline number, then drill down |
| **Langfuse / LangSmith** | Trace waterfall: nested steps with time, tokens and cost per step; click a step for inputs and outputs | Run "trace" tab built from our `agent_tool_calls` + `cost_ledger` |
| **Figma** | Presence as a warm social signal; presence on a separate cheap channel; users want to mute it and have that remembered | Avatars of who is viewing a run or plan; mute preference stored |
| **Lovable / v0 / Bolt** | Start from one prompt, live preview at once; non-developers stall where backend setup begins | Zero-setup demo that actually starts; onboarding that ends in a real first run |

### Agent-UX rules several sources agree on

1. **State lives outside the chat.** A long agent run inside one thread is unreviewable. Chat is for talking; a
   task surface holds state.
2. **The plan is an object you can edit**, not a message. Approval then becomes a real review.
3. **Match friction to risk.** Reported figures (secondary sources, unverified): users approve about 93% of
   permission prompts, and click-through reviewers caught 13.6% of dangerous commands in one experiment. Asking
   about everything trains people to stop reading. Gate the risky, batch the routine, show a receipt for the rest.
4. **Action receipts.** After each action: what changed, where, under which permission, with a diff and an undo.
5. **Visible handoffs** between agents, and a stop that propagates downstream when an upstream step fails.
6. **Autonomy is a dial** the user can turn down instantly; turning it up needs confirmation.

## 4. The merged design

**Principles:** evidence before opinion (our differentiator: every claim on screen links to a recorded fact);
status first; keyboard first; one accent colour and one type scale; unknown is shown as unknown, never as
"passed" or "$0" (already the rule in the verdict comment).

**Structure (five surfaces):**

1. **Home = Inbox + Mission control.** Left: "Needs you" queue (plans to approve, reviews, blocked runs, scope
   requests), ordered by age and risk. Right: every running agent as a card with state, current step, spend,
   and who is watching. From Graphite, Agent HQ, Cursor 3.
2. **Run workspace.** Header: goal, state, spend, branch, viewers. Body: a timeline of blocks (Warp), one per
   step, each with its receipt; a scrubber and a Follow switch (Devin); side panel with plan, files, terminal.
   One shortcut opens the diff (Conductor).
3. **Review.** The diff is the centre (Codex): inline comments that become instructions to the agent, accept or
   revert per chunk, "since last iteration" as the default view (the Cursor complaint). Above the diff, the
   **verdict card**: plan approver, sandbox boundary, tests, cost, risk flags, pre-review concerns: the same
   facts as the W8 PR comment.
4. **Evidence and cost.** Trace waterfall per run (Langfuse), cost page with one headline number (Stripe), audit
   export. The client trust portal (P-17) is a read-only skin over this surface.
5. **Command palette and shortcuts** on every page (Linear), with shortcuts shown in menus and tooltips.

**Component stack (decision D-U1):** recommended: Tailwind CSS v4 + shadcn/ui (Radix primitives, source copied
into the repo) + `cmdk`, introduced page by page beside the existing stylesheet, with our 64 variables mapped
to the theme tokens. Reported compatible with Next 15 / React 19 (shadcn changelog). Vercel AI Elements and
assistant-ui offer ready parts for tool calls and reasoning; evaluate before writing our own. Alternative:
keep hand-written CSS and add only Radix primitives + `cmdk` (smaller change, more hand work per screen).

## 5. Phases

Each phase ends with: Playwright journey + axe pass, before/after screenshots in
`docs/briefs/evidence/ui/`, and one timed task (stated per phase). No backend contract changes unless listed.

| Phase | Build | Backend it reads (exists today) | Done when |
|---|---|---|---|
| **U0 Foundation** | Tokens, type scale, primitives, app shell, command palette, shortcut map, loading/empty/error states, 100 ms budget test | none new | Every existing page renders on the new shell; palette reaches every route; axe clean |
| **U1 Home** | Inbox + mission control; tab-icon status | runs list, plan gate, notifications, reviews, event stream | A reviewer finds and opens the oldest pending approval in under 10 s, keyboard only |
| **U2 Run workspace** | Block timeline, scrubber, Follow, receipts, plan as editable object | `agent_events`, `agent_tool_calls`, checkpoints, plan gate, cost ledger | A 40-step run can be scrubbed to any step; plan edit creates a new plan version |
| **U3 Review** | Diff-centred review, inline comment → agent, per-chunk accept/revert, verdict card, risk-tiered approval | section8 reviews/risks, I-10 digest, I-09 gate evidence, verdict facts (`verdict_post.collect_facts`) | Reviewer approves a run using only the verdict card and diff; risky paths still require explicit approval (negative test) |
| **U4 Evidence + trust portal** | Trace waterfall, cost page, export; read-only portal | cost ledger, audit events; **needs** the scoped read-only role from P-17 | P-17 acceptance: end-client completes a review session; cross-member negative tests pass |
| **U5 First run** | Demo that starts without a loop, onboarding to a real first run, key tour, accessibility sweep (A-10), phone triage view (approve/deny only) | demo seed, onboarding | New user reaches a finished demo run in under 60 s; manual screen-reader journey recorded |
| **U6 Presence** | Avatars, who-is-viewing, mute | **needs** D-01 real-time presence; gated by the Week 13 multiplayer decision (P-D7) | Only after P-D7 |

**Order and links to the 90-day plan.** U0 → U1 → U2 → U3 can start now and do not depend on weeks 1–8 being
redeployed; they read data that already exists. U3 reuses W5/W6/W8 output, so it is where earlier work becomes
visible. U4 **is** P-17 (Week 9) plus P-19's export (Week 10); doing U0 first means the portal is built on the
new system instead of the old stylesheet. U6 waits for Week 13. Rough size: U0 one week, U1–U3 about one week
each, U4 one to two, U5 one. These are guesses until U0 shows the real migration cost.

**Risks:** restyling 120 components is where time goes, so U0 must migrate the shell first and leave old pages
working; a palette and shortcuts add accessibility duties (focus order, no single-key traps in inputs); "since
last iteration" diffs need per-iteration snapshots (checkpoints exist; confirm they are enough).

## 6. Owner decisions

- **D-U1** Component stack: Tailwind v4 + shadcn/ui + cmdk (recommended) or hand CSS + Radix.
- **D-U2** Visual direction: dark-first with one accent (Linear/Vercel family, recommended since the current UI
  is already dark) or a light-first, warmer look for the client portal.
- **D-U3** Whether U0–U3 run before P-17 or P-17 ships first on the old UI (recommended: U0 first, then P-17 as U4).
- **D-U4** Risk tiers for approvals overlap with P-21 (I-05), which needs P-D6. U3 shows tiers; it must not
  auto-approve anything until P-D6 is decided.

## 7. Sources and their quality

Vendor or official: [Codex app docs](https://developers.openai.com/codex/app), [Conductor docs](https://www.conductor.build/docs/workflow),
[Devin session tools](https://docs.devin.ai/work-with-devin/devin-session-tools), [GitHub: mission control](https://github.blog/ai-and-ml/github-copilot/how-to-orchestrate-agents-using-mission-control/),
[GitHub: Agent HQ](https://github.blog/news-insights/company-news/welcome-home-agents/), [Vercel dashboard redesign](https://vercel.com/blog/dashboard-redesign),
[Warp block model](https://www.warp.dev/blog/block-model-behind-warps-agentic-development-environment), [Figma cursor limits](https://help.figma.com/hc/en-us/articles/1500006775761),
[shadcn React 19 changelog](https://ui.shadcn.com/docs/changelog/2024-10-react-19), [Superhuman: command palette](https://blog.superhuman.com/how-to-build-a-remarkable-command-palette/),
[Langfuse timeline view](https://langfuse.com/changelog/2024-06-12-timeline-view.md), [LangSmith traces](https://docs.langchain.com/langsmith/view-traces.md),
[Graphite: why](https://graphite.com/docs/why-you-should-use-graphite), [Linear command menu](https://linear.app/features/level-up).

User voices: [Cursor forum: Agents Window](https://forum.cursor.com/t/cursor-3-agents-window/156509?page=3),
[Cursor forum: stop pushing it](https://forum.cursor.com/t/please-stop-pushing-the-agent-window-so-aggressively-in-ide/160204),
[Figma forum: cursor setting](https://forum.figma.com/t/make-show-multiplayer-cursors-setting-sticky-across-sessions/20614),
[Warp G2 review](https://www.g2.com/survey_responses/warp-review-12713960).

Third-party analysis (directional only): [The Decoder on Cursor 3](https://the-decoder.com/new-cursor-3-ditches-the-classic-ide-layout-for-an-agent-first-interface-built-around-parallel-ai-fleets/),
[New Stack: Conductor review](https://thenewstack.io/a-hands-on-review-of-conductor-an-ai-parallel-runner-app/?p=22802860),
[Devin product analysis](https://ppaolo.substack.com/p/in-depth-product-analysis-devin-cognition-labs),
[VentureBeat on Agent HQ](https://venturebeat.com/ai/githubs-agent-hq-aims-to-solve-enterprises-biggest-ai-coding-problem-too),
[Why Linear feels fast](https://dev.to/0xgosu/why-linear-feels-fast-local-data-small-updates-and-product-discipline-1m53),
[Agent UX is not chatbot UX](https://dev.to/victor_desg/agent-ux-is-not-chatbot-ux-and-most-teams-in-2026-ship-them-as-if-they-were-23bi),
[Hatchworks agent UX patterns](https://hatchworks.com/blog/ai-agents/agent-ux-patterns/),
[Permission prompt habituation](https://tianpan.co/blog/2026-04-28-agent-permission-prompt-habituation-curve),
[The human-in-the-loop illusion](https://resilientcyber.beehiiv.com/p/the-human-in-the-loop-illusion),
[SaaS dashboard examples 2026](https://925studios.co/blog/saas-dashboard-design-examples-2026),
[Vibe Kanban status](https://aiidelist.com/ide/vibe-kanban.md), [Jules shutdown](https://techradar.com/pro/google-kills-off-jules-its-ai-coding-agent-which-promised-to-do-all-the-jobs-you-didnt-want-to),
[Lovable vs Bolt vs v0](https://dev.to/techsifted/best-ai-app-builders-in-2026-i-tested-v0-boltnew-lovable-and-replit-agent-45f4).

Not found: independent, measured comparisons of these UIs; Zed's agent panel; Arc. I did not use any of these
products hands-on for this report.
