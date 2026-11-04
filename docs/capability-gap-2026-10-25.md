# Capability gap — where the harness stands vs the big-league agents

2026-10-25. Every "we have/don't have" below is verified against code with a
`file:line`, not inferred from the README. The comparison set is Claude Code,
OpenAI Codex (CLI + cloud), Cursor, Devin and OpenHands — described from their
observable behaviour, not their source.

---

## 1. What the executing agent is actually offered today

This is the ground truth, and it is much narrower than the platform's feature
list suggests. The engine runs the SDK's `sdk-minimal` profile
(`section4/src/harness_orchestrator/engine.py:51`) and patches it
(`engine.py:61-100`). In that profile:

| Tool | Status | Evidence |
|------|--------|----------|
| `bash` (our plugin, policy-gated) | **EXISTS** — the only real capability | `dsh_plugins/harness-shell.js:52-74`; gate `section5/src/harness_sandbox/shell_gate.py` |
| `read` / `write` / `edit` (native file tools) | **ABSENT — one config flag away.** The profile ships them switched off | verified in the runtime binary: `tool-fs config: read:false, readImage:true, write:false, edit:false` |
| `glob` / `grep` as first-class tools | **ABSENT** — reachable only as allow-listed bash programs | `policy.py:87-120` |
| `web_fetch` / HTTP tool | **ABSENT** | no such tool in the profile tree |
| browser / computer use / keyboard-mouse | **ABSENT** — nowhere in the repo | only `section7/scripts/browser-check.mjs` (our own a11y test) |
| image / vision input | **PARTIAL** — `readImage` is enabled and the runtime has a real attachment store (`attachments.readImage`, base64 delivery over ACP), but our engine passes a plain-text goal and no upload path exists | `engine.py:620`; runtime attachment/AcpContent code |
| sub-agent `task` tool | **ABSENT** (`agents: []` in the profile) | profile patch |
| MCP client | **ABSENT in our wiring** — the runtime does translate "ACP MCP-server declarations into Agent-scoped DSH MCP clients", so the capability is upstream and unused by us | runtime `lib/types/mcp.js` |
| working-directory get/set | EXISTS | `deepseek_harness/api.py:148-156` |
| context compaction | **EXISTS upstream, unexamined** — `compaction-basic` and `image-offload` are both in the profile | profile patch |
| context window | 1,000,000 tokens by default, `DSH_CONTEXT_WINDOW`-configurable | `llm-deepseek config.defaultContextWindow` |

That table reframes the whole gap list: **the runtime is far more capable than
the profile we run it with.** We chose `sdk-minimal`, then switched off its file
tools, never pass its notification callback, never mount its MCP servers, never
attach an image, and cap output at 4096 tokens. Several items below are
therefore configuration and review work, not construction.

So: **one tool, `bash`, gated by a 300-line policy file.** Everything else the
agent does is a shell command. That is a defensible security design, but it is
not what the comparison products offer, and it costs the model a lot of
efficiency — see §3.

What the gate allows (`harness-shell.js:9-16`, `policy.py:87-140`): `ls cat
head tail find grep rg pwd echo git <subcommands> python python3 pytest npm npx
yarn pnpm vitest jest cargo go ruff flake8 mypy black isort eslint prettier
tsc`. No pipes, no `;`, no `&&`, no redirection, no `$(...)`, no backgrounding
(`policy.py:273-290`). Denials return the reason and the model is told to change
the command (`harness-shell.js:15,71`). Per-run `maxToolCalls` budget
(`harness-shell.js:44-51`) and a 900s hard deadline (`engine.py:371-386`).

Two things worth naming in this design, because they are the kind of thing a
security review will ask about:

- `python`/`python3` are allow-listed and the scope check **skips code strings**
  (`shell_gate.py:55-58`), so `python -c "open('x','w')…"` writes anywhere the
  OS permits. The real boundary is Landlock/Seatbelt + the container, not the
  gate. That's fine if it's understood as the design; it's bad if anyone
  believes the path check is what confines writes.
- Landlock ABI v1 has no network hook (`confinement.py:440`), so on Linux,
  network confinement comes from the container boundary, not the sandbox the
  code advertises.

---

## 2. Where we are genuinely ahead

Not a consolation section — these are capabilities the comparison products do
not have, and they were the expensive parts:

| Capability | Us | Them |
|------------|----|------|
| OS-enforced confinement | Landlock + Seatbelt + allow-listed shell gate + egress proxy, per-run workspace | permission prompts; a user-configured sandbox; or nothing |
| Approval before execution | plan gate: versioned plans, forbidden paths, budget caps, human approve required | per-command y/n, or full-auto |
| Reversibility | pre/post-edit git checkpoints per batch, restorable, durable run queue | git-based undo, ad hoc |
| Multi-actor | agent tree, assignments, meetings, handoff, leader/participant direction queue | single user, single agent |
| Metering & governance | per-team cost ledger, tier caps enforced in code, audit trail, secret+reasoning scrubbed on export | per-account, provider-locked |
| Model breadth | 65 providers, 3 wire protocols, BYOK | 1–3 providers |
| Project rules | `project_rules` injected into the planner (`rules_loader.py:41`) | `CLAUDE.md` / `AGENTS.md` / `.cursorrules` — a file, not a per-project DB row |
| Deployment | self-hostable, SSO, DPA-shaped legal packet | SaaS |

---

## 3. The gap list

Effort in focused engineer-days. "Decision" = needs your call before code.

### A. Agent capability

| # | Gap | Who has it | Our state | Why it matters | Effort |
|---|-----|-----------|-----------|----------------|--------|
| A1 | Native read/write/edit tools | all five | **switched off in the profile, not missing** | The model spends turns and tokens parsing `cat` output and assembling patches as shell strings; dedicated tools are cheaper and return structured results the UI can render | **1–2d** (flip + review the fs policy path) |
| A2 | glob/grep/search tools | Claude Code, Cursor, Codex | absent (bash only) | Repo navigation is the first thing an agent does | 1–2d |
| A3 | Web fetch / docs lookup | Claude Code, Cursor, Devin | absent | No way to read a library doc or an error page; agents hallucinate APIs instead | 2–3d + egress decision |
| A4 | Browser control | Devin, OpenHands, Cursor | **absent** | Frontend work is unverifiable for us — the agent can edit React and never see it render | 1–2wk |
| A5 | Computer use (screen + keyboard/mouse) | Devin, OpenHands (full VM) | absent | Only matters for A4-class tasks and GUI testing | rides on A4 |
| A6 | Vision input (screenshots, design mockups, error dialogs) | Claude Code, Cursor, Devin | **half-built** — `readImage: true` in the profile and the runtime has a real attachment store; we never attach anything | "fix this UI" with a screenshot is a top-3 daily use for the others | **2–3d** (goal attachment + mime path) |
| A7 | Sub-agents / task delegation | Claude Code (Task), Devin (parallel), Cursor (background agents) | **records only** — `agent_tree.py:18-20` says the engine does not call it, and the profile ships `agents: []` | We designed the tree and never wired it; the feature is invisible to users | 1–2wk to wire |
| A8 | Background/parallel runs on isolated worktrees | Cursor background agents, Codex cloud, Devin | queue exists, one workspace per run, no worktree isolation | Parallelism is the actual productivity multiplier of the leaders | 1wk |
| A9 | LSP / real diagnostics after edits | Cursor | absent (relies on `tsc`/`ruff` via bash) | Compiler-grade feedback beats re-reading files | 3–5d |
| A10 | Notebook / REPL execution | Claude Code, OpenHands | absent | Narrow, but blocks a whole data-work segment | 1wk |

### B. Context and repo intelligence

| # | Gap | Who has it | Our state | Why it matters | Effort |
|---|-----|-----------|-----------|----------------|--------|
| B1 | Automatic repo context (tree, README, git status/log) | all five | **absent** — goal + plan text only, and runtime context is switched off in the prompt (`includeRuntimeContext:false`) | This is the single biggest quality lever after A1. Our own audit says the same (`docs/parity-assessment-2026-10-25.md:62`) | 3–5d for a baseline |
| B2 | Codebase index / semantic search | Cursor (embeddings), Devin, Codex | section 6 has an index for user search/export; **the agent never queries it** | Without it, a big-repo agent is blind past ~20 files | 2–3wk |
| B3 | `@`-symbol context picking (files, symbols, docs, commits) | Cursor | **landed for files, symbols, selection and uncommitted hunks (W7)** — `clients/vscode/src/core/context.ts` renders them into the goal and states any reduction in the goal text itself; docs/commits not covered | The editor surface is what made this worth building; the reduction note is what makes it auditable | remaining: `@doc`, `@commit` |
| B4 | `.git` history access | Claude Code, Codex | **stripped** from the seeded workspace (`engine.py:194-198`) | The agent cannot see who changed what or run `git log` meaningfully | 1d to reconsider |
| B5 | Dependency/manifest awareness | Cursor, Claude Code | absent | Version-confusion errors the others avoid | part of B1 |
| B6 | Context compaction / long-run memory | Claude Code (auto-compact) | **exists upstream, unexamined** — `compaction-basic` + `image-offload` are in the profile and the window defaults to 1M (`DSH_CONTEXT_WINDOW`); what is ours is the hardcoded `max_tokens=4096` output cap (`engine.py:602`) and surfacing none of it | We may be degrading runs we don't need to, and capping answers we shouldn't | 2–4d |
| B7 | Loop detection / stuck-run recovery | Claude Code, Devin | **absent** | A looping agent burns the whole budget and looks broken | 2–3d |

### C. Execution control

| # | Gap | Who has it | Our state | Why it matters | Effort |
|---|-----|-----------|-----------|----------------|--------|
| C1 | Live transcript of tool calls | all five | **folded into the final result** (`engine.py:520`) — the SDK's `on_notification` callback exists and we pass nothing | The #1 thing that makes a run feel alive; mostly our own wiring | 3–5d + contract decision |
| C2 | Interrupt / stop mid-step | Claude Code (esc), Codex, Cursor | **absent** — `Session` has no cancel; Pause/Stop are record-level and the UI already admits it (`agentTree.tsx:78`) | Without it a bad run costs the full budget | 5–8d |
| C3 | Steer mid-run | all five | direction applies "next time it starts or resumes, not mid-step" (`sharedRunPanel.tsx:245`) | Same | rides on C2 |
| C4 | Retry / model ladder on failure | Claude Code, Cursor | deliberately removed — one attempt, honest error (`engine.py:14-18`) | Ours is defensible (honesty) but users will read it as fragility; a *visible* "retry with X" is the fix | 1–2d |
| C5 | Rewind to checkpoint from the UI | Cursor has this well | restore exists in S5, weak in UI | Recovery affordance | 2–3d |
| C6 | In-loop human approval for dangerous actions | Claude Code permission modes, Cursor | **absent** — we deny outright and tell the model to change approach | A policy gap, not a code gap: deny-only is safer but less useful. `IncomingRequest` in the SDK suggests the protocol can carry it | 1wk + **decision** |
| C7 | Shell state persistence across calls | all five (a real terminal session) | **absent by design** — each call starts fresh, no `cd` carry-over | Costs the agent many turns (activate venv, cd, run…) | 2–4d + **decision** (state carry-over weakens the gate) |

### D. Interaction surface

| # | Gap | Who has it | Our state | Why it matters | Effort |
|---|-----|-----------|-----------|----------------|--------|
| D1 | Terminal-native CLI | Claude Code, Codex, OpenHands | **landed (W6)** — `harness run`/`chat`/`diff`, same gate and audit as the web, local-workspace mode under the same container boundary, offline 0600 token store | The leaders' primary surface; devs won't open a browser to fix a bug | done; IDE surface was D2 |
| D2 | IDE extension | Cursor (is the IDE), Claude Code, Codex | **core + adapters landed (W7)** — inline *proposed*/*reviewed* diff at the point of work, context picking, status bar, plan approval and per-file approve/reject in the editor, token in SecretStorage; gate proven live (`w7-ide-1793116802.json`) | The diff the operator reviews is the diff that is written, digest-bound: a mismatched patch is never shown and `CHANGE_STALE` is the server's answer, not the client's | packaging/publishing and a real-window smoke test remain |
| D3 | Share links / public run transcripts | Claude Code, Devin | absent | Adoption loop | 2–3d |
| D4 | Slash commands / prompt shortcuts | Claude Code, Cursor | absent | Cheap, heavily used | 2–3d |
| D5 | Hooks (run my script on tool-use / on-stop) | Claude Code | absent | Extensibility without us building every feature | 3–5d |
| D6 | Voice / mobile | Codex (mobile app), Claude | absent | Niche for a team product | later |
| D7 | The web run surface's own polish | — | audit Batch B/C/D still open: no copy buttons, no keyboard review loop, no cost footer, no elapsed timer, non-live default tab, silent stream death | See `docs/ui-ux-audit-2026-10-25.md` | ~2wk |

### E. Extensibility

| # | Gap | Who has it | Our state | Why it matters | Effort |
|---|-----|-----------|-----------|----------------|--------|
| E1 | **MCP client** — consume third-party tools | Claude Code, Cursor, Codex, Devin | **unused, not missing.** We expose an MCP *server* with 4 governance tools (`mcp/src/harness_mcp/server.py`); the runtime already translates "ACP MCP-server declarations into Agent-scoped DSH MCP clients" and we never mount one | MCP is the ecosystem the others grow on; without it we can't reach Slack, Jira, DBs, internal APIs | **3–5d** to mount + a tool-allowlist review, not 1–2wk |
| E2 | Custom agents / skills / playbooks | Claude Code subagents, Devin playbooks, OpenHands microagents | absent | Teams encode their own conventions | 1wk after E1 |
| E3 | Plugin/tool roster review | — | `sdk-minimal` is a reviewed floor; `DSH_PATCH_FILES` fails closed if a patch is missing (`engine.py:612-618`) | This part is good — keep it | — |

### F. Security posture vs theirs (the part of your question about "full access")

Read this before deciding we should chase A4/A5/C7.

| Dimension | Big-league default | Ours |
|---|---|---|
| Where code runs | the developer's machine | a container, OS-confined |
| File writes | allowed, prompted | allowed inside the workspace only |
| Network | usually on | **off by default**; pinned-registry + github/gitlab allow-list (`egress.py:6-16`) |
| Destructive commands | prompt, or auto-accept in yolo mode | denied with a reason |
| Secrets in context | keychain/env, model can read | vault-encrypted, hint-only, scrubbed from logs and exports |
| Audit | transcripts | append-only event bus, per-team |
| Approval granularity | per command | per **plan**, before anything runs |

"Full access of the computer" is what Devin and OpenHands give you *because they
give you a whole VM*. The equivalent for us is not fewer guardrails — it's a
**bring-your-own runner**: an agent executes on a machine the customer controls
(their laptop, their CI box, their GPU host) with the same policy gate, and we
keep the plan approval, checkpoints and audit on top. That gets the capability
without becoming a prompt-safety product. It's a real project (~3wk) and it's a
product decision, not a gap to close quietly.

What I'd fix regardless, because they are security *gaps* not trade-offs:

- S1: Landlock v1 gives no network hook — Linux confinement is the container, and
  the docs should say so plainly (`confinement.py:440`).
- S2: the `python -c` scope bypass (`shell_gate.py:55-58`) — either document
  that OS confinement is the boundary, or parse the code argument.
- S3: `harness-sandbox-egress` has been crash-looping on the dev stack for
  hours — an allow-list nobody can observe is not a control.
- S4: no in-loop approval path (C6) means the only lever is deny, which pushes
  users toward wanting deny removed.

---

## 4. Sequenced plan

The reframing from §1 matters for sequencing: a surprising share of the gap is
**us not using what we already ship**. Those come first because they are cheap
and reviewable.

**Week 1 — configuration and wiring, no new subsystems.**
A1 flip `read`/`write`/`edit` on (with the fs policy path reviewed, since the
scope check is what makes them safe). A2 glob/grep. C1 pass
`on_notification` and stream tool calls. B4 stop stripping `.git`. B6 raise the
hardcoded 4096 output cap and surface the window/compaction state. A6 mount the
attachment path so a screenshot can reach the model. D7 Batch C of the UI audit.

**Weeks 2–3 — control.**
C2/C3 interrupt and mid-run steering (the hard one — `Session` has no cancel).
C6 in-loop approval, which needs your policy call before code. B1 baseline repo
context in the prompt. B7 loop detection. E1 mount the MCP client behind an
explicit tool allow-list.

**Weeks 4–6 — reach.**
A3 web fetch behind an egress decision. A8 worktree isolation for parallel runs.
A7 wire the agent tree to real execution. C5 rewind-from-UI. D4/D5 commands and
hooks.

**Separate track, decide before building:** A4 browser (1–2wk), D1 CLI (2–3wk),
D2 IDE extension (3–4wk), B2 codebase index (2–3wk), and the BYO-runner that
would give "full computer access" without dropping the guardrails (~3wk).

Revised total to credible-in-the-big-leagues-on-capability: **6–8 weeks**, not
8–10 — because week 1 alone closes the two gaps users feel most (can't see the
files, can't watch it work) and both are configuration plus a callback.

## 5. What I could not verify

- What `dsh-tool-fs` does when read/write/edit are enabled — whether it honours
  the same scope paths our shell gate enforces, or a separate policy. A1's
  estimate assumes it honours the sandbox; if it doesn't, the flip is a security
  change, not a feature change.
- What `compaction-basic` and `image-offload` actually do at runtime (B6).
- Whether the SDK's `IncomingRequest` can carry a real approval round-trip (C6).
- `mcp-resources` semantics in the profile.
- Any claim about Codex/Claude Code/Cursor/Devin internals — the comparison is
  from observable behaviour, not their source.
