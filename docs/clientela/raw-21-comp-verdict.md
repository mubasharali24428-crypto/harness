# Comparative Verdict — Attempt 3

---

## SECTION A — Where the harness genuinely WINS

**1. Human plan-gate governance**
The plan-gate (approve before agent execution) has no equivalent in Cursor, Windsurf, Copilot Workspace, Devin, Aider, or any meeting incumbent — all either act directly or offer only coarse confirm-per-command prompts.
*Buyer relevance:* **Strong for regulated/enterprise buyers** (finance, health, gov) where an auditable human sign-off before autonomous action is a procurement requirement. Weak for individual devs and small teams, who optimize for speed and will find the gate friction unless it's fast.

**2. Event-sourced audit trail**
Every gate decision, pause, resume, human-edit, and command is recorded as immutable events. Devin offers session logs; nobody offers a replayable event history of *human decisions* interleaved with machine actions.
*Buyer relevance:* **Real but secondary at purchase time** — it rarely drives the initial pick, but becomes decisive post-incident ("what exactly did the agent do?") and in compliance reviews. Best sold as part of the governance story, not standalone.

**3. OS-level sandbox allow-list**
dsh executes inside an OS-enforced allow-list sandbox — capability boundaries enforced by the kernel, not by prompt instructions. Incumbents rely on container isolation (Devin) or nothing structured at all (IDE agents run with full user privileges).
*Buyer relevance:* **Genuinely differentiated and increasingly demanded** as enterprises wake up to prompt-injection-driven exfiltration. Under-marketed today; the security buyer is the easiest new door this opens.

**4. Team-scoped multi-tenancy**
Workspaces, briefs, approvals, and audit trails are scoped per team out of the box. Copilot Workspace ties tenancy to the GitHub org; Cursor/Devin bolt team plans onto single-user roots.
*Buyer relevance:* **Table stakes for enterprise, differentiator vs indie tools.** It matters most in the 20–500 seat band where tool sprawl forces consolidation; below that it's invisible.

**5. Self-host + BYO-model**
Deploy the whole harness on your own infra and point it at any model endpoint. None of the six incumbents allow true self-host; BYO-model exists only partially (Aider/Cursor via API keys).
*Buyer relevance:* **Decisive for a narrow but valuable slice** — defense, EU data-residency shops, IP-paranoid R&D. For the broad market it's a checkbox, not a driver. Pairs naturally with #3 as one "sovereignty" pitch.

---

## SECTION B — 10 shortcomings that kill adoption first (ranked)

1. **No voice/video/anything live** — text-only chat means the harness can't even enter the meeting category conversation. *Zoom/Meet/Teams are the entire product here.* (BLOCKER)
2. **No codebase indexing / @refs / RAG** — every competitor grounds answers in the actual repo; the echo-bot only knows pasted text, so output quality collapses on real codebases. *Cursor, Windsurf, Copilot Workspace, Aider.* (BLOCKER)
3. **No code editor surface / file tree** — devs won't live in a chat pane with no way to see or navigate their project. *All five coding incumbents.* (BLOCKER)
4. **No Tab completion** — the highest-frequency interaction in AI coding is absent; without it there's no daily-habit hook. *Cursor, Copilot.* (HIGH)
5. **Read-only diff panel, no inline accept/reject** — review happens away from where edits land; Windsurf's per-hunk flow is the baseline expectation. (HIGH)
6. **No checkpoints / git snapshots** — a bad agent run is unrecoverable; every incumbent lets you roll back. Trust requires undo. *Cursor, Aider.* (HIGH)
7. **PR flow orphaned** — approved work dead-ends instead of landing as a reviewed pull request; breaks the loop teams actually ship through. *Copilot Workspace, Devin, Aider.* (HIGH)
8. **No rules files / persistent repo conventions** — corrections don't stick across sessions, so users re-explain their codebase forever. *.cursorrules, Windsurf rules, CONVENTIONS.md.* (HIGH)
9. **Per-step model selection missing + cost table unwired** — no model choice, no real spend visibility; the dead UI actively erodes trust. *Cursor.* (MEDIUM)
10. **No threads, presence, calendar, or persistent rooms** — individually minor, together they mean zero social fabric: the product can't host collaboration, only transactions. *Slack huddles, Teams, Granola.* (MEDIUM)

**Net read:** Section A wins are real but sell to governance/security buyers; Section B items 1–3 are structural gaps that block mainstream coding and meeting adoption outright. Fastest credible wedge: fix 6–8 (S/M builds) to make the existing dsh pipeline trustworthy, then attack indexing (#2) as the first L build.
