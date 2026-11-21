# YC Winter 2027: application plan (Harness)

Prepared 2026-11-01 (PKT). Sources: the official YC apply page (fetched today), and
third-party reconstructions of the form. The form itself is only visible after you
start an application, so re-check every question against it before pasting.

Assumptions: the repo is `/Users/user/untitled folder 2/harness` on branch `w1-engine-tools`;
you are the founder "Mubashar Ali" (the git author); the company is called Harness for now;
no co-founder is named anywhere in the repo, so the draft answers are written for a
founder who is solo until you say otherwise.

## 1. Deadline, in your time zone

| Clock | Deadline |
|---|---|
| Official YC (`ycombinator.com/apply`) | **Nov 2, 2026, 8:00 pm Pacific** (on-time). Decisions for on-time applications by Dec 11. |
| Pakistan (PKT, UTC+5) | **Nov 3, 2026, 9:00 am PKT** |

Your "Nov 3, Pakistan time" is right, but it is the morning of Nov 3, which is easy to miss.
**Target: submitted by Nov 2, 6:00 pm PKT** (about 15 hours before the real cutoff). It is
Nov 1, about 6:30 pm PKT as I write this, so that leaves roughly 24 hours of work.

## 2. What the form asks

The official page only says to apply at `apply.ycombinator.com`. The question list below
comes from a third-party reconstruction (26 questions in 7 sections). Check it on the
live form. Word limits are not confirmed.

Required: the founder video (about 1 minute, every founder on camera, no demo, do not read a
script). Optional: a separate product demo video (under about 1 minute is the common
guidance; YC does not publish a limit). If invited: a 10-minute video interview with 2–3
partners, usually in November–December; decisions often come the same day.

## 3. Draft answers (paste-ready; `[ ]` = needs you)

Each answer is a draft in the founder's voice. Replace anything in brackets. Only claims
marked "verified" below have evidence in this repo.

**Founders**
1. *Who writes code?* "I (Mubashar Ali) wrote the code, since 2026-08-22, with AI coding
   agents (Claude Code) as tools. The design and every review decision are mine. [Confirm: no
   non-founder has written code.]"
2. *Looking for a co-founder?* `[Yes / No. If solo: say so and say what you will do about the gap.]`

**Company**
3. *Company name:* `[your choice]`. "Harness" is the working name in the repo.
4. *Describe in 50 characters or less:* "Governed AI coding agents: humans approve every plan
   and every file." (68 characters. Trim to: "Human-approved AI coding agents for teams." = 42.)
5. *Company URL:* `[none yet]`
6. *Link to the product:* `[none public yet]`. The app runs locally with Docker Compose. Offer
   a screen recording (see the video plan) and say that the code is private for now.
7. *What are you making?* "Harness is a self-hosted platform where a team's AI coding agents work
   under human control. Nothing runs until a person approves the plan. Each run happens in a
   sandbox. The reviewer sees what changed, the tests, the cost and the time, then approves or
   rejects each file. Every step goes to an audit log."
8. *Where do you live now / where after YC?* `[city, Pakistan]`. YC's batch is in San
   Francisco, January to March. `[Your plan for the batch]`.
9. *Explain your location decision:* `[your answer. Be direct. This is a question YC asks of every
   non-US founder.]`

**Progress**
10. *How far along?* "A working end-to-end prototype, about 265k lines of Python and TypeScript
    including tests, 631 commits on this branch since 2026-08-22, 10 service sections in
    Docker Compose. A real model run with tool calls and a real file change ran on OpenRouter on
    2026-10-26 (verified). Sandbox, identity (OIDC) and point-in-time backup recovery have each been
    drilled. Not in production. No external users yet (verified)."
11. *How long on this, and how much full-time?* `[since when; full-time or not]`. Repo history starts
    2026-08-22 (about 10 weeks).
12. *Tech stack:* "Python 3.11+ with FastAPI services, PostgreSQL 18, Next.js / React /
    TypeScript for the web app, a VS Code extension, Docker per-task sandboxes (macOS Seatbelt,
    Linux Landlock), and an open-source agent runtime (DeepSeek Harness SDK) behind an
    OpenAI-compatible model router with bring-your-own keys. We use AI coding tools (Claude Code)
    to build it."
13. *Are people using it?* "No external users yet. The first step is a supervised pilot with one
    trusted team. `[If anyone outside has tried it, name them and what they did.]`"
14. *Revenue?* "No."
15. *Applied before with a similar idea?* `[Yes/No. If yes, what changed.]`
16. *Other accelerator programs?* `[None / list]`

**Idea**
17. *Why this idea?* `[Your story: the moment you ran into agents changing code nobody reviewed.]`
    "Coding agents are getting good at doing the work, but teams still have no way to decide what
    the agent may do. Harness puts a person at the points that matter: before any run, during
    the run, and on every file before it is written."
18. *Competitors, and what you know that they don't?* "Claude Code, OpenAI Codex, GitHub's coding
    agent, Cursor's background agents, Devin. They mostly optimise for autonomy: the agent runs,
    you review the result. We treat approval and audit as the product, not a setting: the team
    approves the plan, watches the run, decides file by file, and keeps the record on its own
    infrastructure. `[Verify the competitor descriptions the day you submit.]`"
19. *How will you make money?* "A self-hosted core, with paid tiers (Free / Pro / Team) for
    team governance, support and hosted convenience. Model usage is bring-your-own-key, so we
    never resell tokens. Pricing and terms are still with counsel."
20. *Other ideas you considered?* `[List 1–2, or "None seriously."]`

**Equity**
21. *Legal entity formed?* `[Yes / No.]` (Check the YC rules for the entity YC funds before you
    answer.)
22. *Taken investment?* `[No, or details.]`
23. *Fundraising now?* `[Yes / No.]`

**Curious**
24. *Why YC?* `[Your answer. Name a specific thing you want from YC: the partners, the network,
    or the speed.]`
25. *How did you hear about YC?* `[Your answer.]`

**Batch**
26. *Which batch?* "Winter 2027."

## 4. Strengths to show, and the claims to avoid

**Strengths with evidence (use these):**

| Claim | Evidence in the repo |
|---|---|
| A human approves each plan before any run starts | README, `docs/demo/yc-demo-runbook.md`; the MCP path was fixed so it cannot auto-approve (`docs/pilot-remediation/BASELINE_2026-10-21.md`, S-04) |
| Runs are sandboxed, and the sandbox fails closed | `docs/briefs/evidence/pilot-prep/m03-landlock-scope.json`, `native-linux-container-drill.json` |
| Each file is approved or rejected separately, and the diff is checked against the approved patch | `docs/briefs/evidence/w24/` (automated runs PASS; the runbook) |
| Review summary built from what happened (files, tests, cost, time) | W24 review summary (`docs/m2-w24-plan.md`) |
| Pause and mid-run direction on the web | Live check on run `run-9101a0a2e853`, 2026-11-01 (the runbook) |
| A real model run with a real side effect | `docs/briefs/evidence/pilot-prep/m07-openrouter-smoke.json`, 2026-10-26 |
| Recovery and identity are drilled | `pitr-drill-20261029T140816Z.json`, `oidc-keycloak-drill.json` (14/14) |
| The 2026-10-21 audit's four security findings are fixed or mitigated | `docs/pilot-remediation/BASELINE_2026-10-21.md` |

**Do not claim:**

- "Production-ready", "secure", or "customers". The repo's own verdict is **NOT READY for
  production or a general pilot** (STATUS.md, 2026-10-24). The 2-week supervised pilot has not
  happened and there is no metrics baseline.
- "The agent writes the plan you approve". The plan text is a fixed template right now
  (runbook, W24 finding 4). Say "you approve the plan gate" and do not read the plan aloud as the
  agent's reasoning.
- "Live pause in the middle of a step". Pause kills the agent process between steps; a redirect is
  delivered when the run resumes. The Pause buttons in the agent tree only change the record.
- "Forbidden paths are hard limits". The 2026-10-21 audit showed that string checks could be
  bypassed. OS-level confinement now covers this on macOS and on Linux (probe-gated), but the
  fine-grained deny is still described as pilot-grade.
- "MIT-licensed". `LICENSE` still has an open "decision point" header. Say the license is
  being finalised.
- Billing. Stripe is unavailable in Pakistan, so billing is off by decision.

**Weaknesses to raise yourself (the audit already names them):** a solo founder maintaining
10 services; no users; no revenue; pricing and terms not final; the native-Linux drill of the
sandbox is recorded, but the dedicated sandbox worker is still to be reviewed. A reviewer
who reads the repo will find all of this, so say it first.

## 5. Interview prep (10 minutes, 2–3 partners, if invited)

Answer each in 30–60 seconds, out loud, before the call:
1. Why you? What have you built that proves you can ship alone?
2. Who is the first customer, and what did they say? (Answer honestly: design partners, or none yet.)
3. Why won't Claude Code, Codex or GitHub just add this?
4. What did you learn from the sandbox and recovery drills?
5. What will you have in 3 months that you do not have today?
6. How will you make money, and at what price?
7. Can you be in San Francisco from January? Who is your co-founder (or why you are solo)?

## 6. Timeline (PKT)

| When | Task | Done when |
|---|---|---|
| Nov 1 evening | You answer the questions in §7 | Each `[ ]` above has text |
| Nov 1 night | Paste the answers into the form as drafts; save the form | Form drafted, not submitted |
| Nov 2 morning | Record the founder video (§ video plan), 1–2 takes | One take you would send |
| Nov 2 midday | Record the demo cut (video plan) | MP4 under the size YC accepts |
| Nov 2 by 6 pm | Final read against §4 ("do not claim"), submit | Confirmation page |
| Nov 3, 9 am PKT | **The real cutoff.** Nothing should happen here. | — |

## 7. Questions only you can answer

1. Solo, or with a co-founder? Who appears in the founder video?
2. Company name?
3. Have you applied to YC or any accelerator before?
4. Have you formed a legal entity? Have you taken any money?
5. Has anyone outside you used Harness (a friend, a student, a team)? Who, and what did they do?
6. Your story: what moment made you build this? Two or three sentences.
7. Where do you live, and can you be in San Francisco from January to March?
8. Are you working on this full-time? Since when?
9. Which license do you want? (The repo says MIT is the default under consideration.)
