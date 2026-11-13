# Outreach kit — design-partner recruitment (P-01 / P-05 support)

Prepared 2026-11-11 by fleet for the OWNER to execute. Fleet cannot send these or
sign anything. Goal per plan: 20 conversations (W1–2), 2 signed SOWs (W3).
SOW template ready: `docs/legal/PILOT_SOW_TEMPLATE.md` (counsel review before signature).

## 1. Target profiles (in priority order)

**A. Pakistani export software houses, 30–300 devs, US/EU clients.** Their pain:
clients increasingly ask "is AI writing this code, and who checks it?" — they need
a trust artifact to win/keep contracts. Fastest pilots: local, cost-pressured,
already using agents informally. Starting names from public PSEB/exporter lists
(owner can prioritize warm introductions; no conversion uplift has been measured):
Systems Limited, Tkxel, NetSol, Devsinc, Contour Software, Arbisoft, 10Pearls,
Folio3, Nextbridge, Code Ninety (PSEB top-10 exporter 2024–26). Large firms =
slow procurement; prefer their mid-size competitors and spin-outs.

**B. Global regulated SMBs/mid-market (50–500 eng):** fintech, healthtech,
insurance, defense-adjacent contractors; anyone with SOC2/ISO auditors asking about
AI change management. Channels: LinkedIn eng-leader posts about agent failures,
r/ExperiencedDevs, HN "who's using coding agents" threads, SOC2 communities.

**C. Offshore shops in other cost markets** (Vietnam, Philippines, Egypt, LATAM) —
same wedge as A, expands after first proof.

## 2. The 30-second pitch

> "How does your team review AI-written code today? Harness is a local engineering
> prototype combining plan approval, confined execution and run-level audit/cost
> records. I can demonstrate a real run and explain its current limitations.
> I'm looking for feedback from two potential design partners; any customer pilot
> would follow security and legal approval. Would a short demonstration be useful?"

## 3. Cold email / LinkedIn template

Subject: "Proof for your clients that AI-written code was human-approved"

> Hi {name} — {one genuine sentence about their firm/client work}.
> Quick question: when a US client asks "did AI write this, and who checked it?",
> what do you show them today?
> I built Harness: human-approved plans, sandboxed agent runs, per-change audit +
> cost, self-hosted (your infra, your model keys). 4-week supervised pilot, 2
> partner slots, no production secrets touched. Worth 20 minutes?
> — Mubashar (demo: 2 min, on request)

## 4. Conversation script (20 min, listen more than pitch)

1. "Walk me through how your team uses coding agents today." (baseline)
2. "What do your clients ask about AI-written code? What do you show them?" (pain)
3. "What would have to be true for you to let agents touch a client repo?" (buying criteria)
4. "Who signs off on a new tool like this, and what's the budget line?" (procurement)
5. "If I gave you a supervised 4-week pilot on one non-production repo, what would
   make it a success — and what would make you stop?" (success criteria → SOW §3)

**Listen for:** a named client asking about AI governance; a lost deal or audit
finding; someone already manually reviewing agent PRs. **Disqualify politely:**
"we'd need full autonomy day one" (not the product) or "production secrets in pilot"
(SOW exclusion). Record managed-cloud demand as input to P-D1; it remains undecided.

## 5. Objection responses (honest)

- "Is it production-ready?" → "No — pilot-grade, supervised, and I'll show you the
  known-limits list before you sign. That honesty is the product culture."
- "How is this different from other coding tools?" → "Harness combines plan approval,
  confined execution and audit records in a self-hosted workflow. Supported providers
  and integrations must be verified for your setup; arbitrary agent compatibility
  and competitor uniqueness are not established."
- "What does it cost?" → pilot band per SOW (P-D4 owner decision); "the pilot price
  is {X}, and the question I need answered is whether you'd pay it after 4 weeks."
- "Where's the team?" → "Solo founder + agent fleets, {N} commits in 3 months,
  every claim pinned to evidence. Here's the live demo and the audit trail."

## 6. Tracking ledger (owner maintains; plan P-01 acceptance = 20 sent, 5 booked)

| # | Name | Firm | Profile A/B/C | Channel | Sent | Response | Conversation date | Outcome | Next step |
|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | |

## 7. Demo asset for calls

Live stack boot + verified journey: `docs/briefs/evidence/90day/demo/verification-2026-11-11.md`.
Record the 2-min video per `docs/demo/yc-demo-runbook.md` (integrity rules in the
verification doc §3). For calls before recording: screen-share the live stack.

Sources for target list: nextbridge.com/top-it-companies-pakistan;
codeninety.com/awards/pseb-top-10-exporter; icreativez.com/software-companies-in-pakistan;
techdestination.com. Lists are starting points, not endorsements — verify fit per firm.