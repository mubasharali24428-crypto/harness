# ALEX-FORCE — Team Definition & Handoff Protocol

> ALEX-FORCE is a fresh 20-agent engineering team chartered to take over the
> AI Engineering Harness campaign from the retiring ALEX_TEAM analyst fleet.
> First mission: **MISSION 15 — WAVE 5 (roadmap program)**, already scoped.

## TOPOLOGY (fixed, 20 agents)

- **1 FORCE-COMMANDER** ("ATLAS" role continuity) — owns dispatch, gates,
  merge law, verifier coordination. Never edits lanes directly except ATLAS
  merge-time wiring explicitly assigned in a graph.
- **2 PRINCIPAL LEADS** — ALPHA lead + BETA lead. Each owns 9 builder agents,
  reviews lane reports before they reach the Commander, and runs first-pass
  gate checks (footprint audit + suite rerun).
- **17 BUILDERS** — tier-2 subagents across two squads (ALPHA 9, BETA 8).
  Same operating law as prior waves: verbatim owned paths, pure-write outage
  protocol (mark UNVERIFIED claims), pasted-output verify contracts.
- **VERIFIER TEAMS** — drawn from the same 20 on demand: any builder can be
  re-dispatched as a verifier with read-only authority; cross-lane pairing
  required (a lane never verifies its own work).

## OPERATING LAW (inherits ALEX_TEAM)

- Blocked >15 min → escalate to squad lead → Commander.
- Never fabricate. UNVERIFIED marks for anything not executed.
- Provider-outage protocol: keep writing owned files; single-response budget
  discipline; write-src-first when iteration budgets are tight.
- NO git writes by builders — Commander commits per gated group.
- contracts/ and conftest.py frozen unless a graph explicitly assigns them.

## HANDOFF STATE (as of transfer)

- Repo: /Users/user/untitled folder 2/harness @ HEAD 629d063 (pushed).
- Campaign doc set: docs/clientela/ (23 raw reports + parts 1–4),
  CLIENTELA_SUGGESTIONS.MD at root, docs/briefs/mission11–15 graphs.
- Waves 1–4: CLOSED, all verifier-certified, CI green throughout.
- Wave 5: IN FLIGHT — graph at docs/briefs/mission15-wave5-graph.md.
  Committed so far: G6a guards (de1c842), S2 server (02f5fca), S4 KDF
  (30475c7), D1 FK story (179d4e3/edff107), G7 docs (694a8f2), D2 vacuum
  (f06acc3), S1 CSP (a99e69b), lane-leftover batch (faf49c0).
- Suites at last full run: S1 400 passed · S3 91 · S4 287+ · S6 76 · S7
  vitest 431 · CI green through 32701744809.
- Remaining W5 work: gate the uncommitted S2/S4/D1/G6b refinements if any,
  run the 5 W5 verifier verticals (graphs §VERIFIER TEAMS), wave close,
  STATUS.md entry, push.

## FIRST-MISSION ORDERS

1. Read docs/briefs/mission15-wave5-graph.md + this file.
2. Inventory git status; commit or repair anything orphaned per merge law.
3. Dispatch remaining W5 verification (W5-VT1..VT5 per graph §VERIFIER TEAMS).
4. Close Wave 5: STATUS entry, mission push, CI confirmation.
5. Then plan Mission 16 from part4 §W5+ backlog severity order.
