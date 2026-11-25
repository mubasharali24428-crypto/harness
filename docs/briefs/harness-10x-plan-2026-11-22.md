# Harness 10X plan — landscape analysis, owner product ideas, and the wave program

**Date:** 2026-11-22 · **Status:** Proposed (evidence states per `FUTURE PLAN 2.0.md` §0 apply; nothing here is `Verified` until its evidence lands). Amendment target: `FUTURE PLAN 2.0.md` §20 (this file is the detail behind it).
**Inputs:** (1) competitor landscape research of 2026-11-22 (coding-agent harnesses, orchestration frameworks, observability/eval/memory infrastructure; sources at the end); (2) repo audit at HEAD `0792b6dd` (scorecard 52/100, NOT READY); (3) three owner product ideas same day — approval rendering by reader, the geometric context store (hexagon → triangle → edge-storing triangles), and multi-agent context curation.

## 1. The 10X thesis

The market has converged on the same ~8 table stakes (skills/AGENTS.md feedforward, hooks, subagents, plan-then-approve, MCP, checkpoints/rewind, auto-compaction, VM isolation) while systematically under-investing in **durability, approval ergonomics, deterministic replay, integrated evals, external security, and governance**. Measured evidence:

- Same model scores **60.45%–82.02%** on Terminal-Bench 2.0 depending on harness — harness quality is ~22 points of task success.
- Users **blind-approve ~93%** of prompts; Anthropic calls approval fatigue "a serious issue."
- Multi-agent fan-out multiplies tokens **~15×**; agents consume 5–10× the tokens of a single call.
- **>75%** of agent turns carry no recovery-relevant state (blanket checkpointing wastes money); checkpoint ≠ durable execution.
- Enterprise governance plane is unowned: 92% lack visibility into agent identities, 95% doubt they can audit agent actions, 71% have no governance framework, 78% couldn't pass an AI-governance audit in 90 days.
- Guardrails inside the agent process fail (Codex sandbox escapes + CVE-2026-19592): they must live outside, fails closed.
- Vendor churn is a purchasing criterion (Swarm deprecated; AgentKit Builder+Evals killed after ~13 months; AutoGen → maintenance; Roo Code shut down).

**This repo already owns the bones of four of the six under-owned planes:** deterministic replay (Hx recordings `hx.recording/v2`, refuses incomplete `HXH104`; event-sourced `agent_events`), governance (hash-chained audit + `GET /api/v1/audit/pack`, denial-learning loop, denial circuit breaker, cost_ledger), external security (Section 5 Landlock/Seatbelt/Docker fails-closed, egress allow-list), integrated evals (corpus ~36 tasks, `scripts/eval/bench_stats.py`, leaderboard). **The 10X strategy is therefore: close the table-stakes gaps competitors already have, productize the empty space we own, and delete process waste.**

Table-stakes gaps verified in-repo: no context compaction (`section4/src/harness_orchestrator/context_window.py` reports the window honestly; nothing compacts); memory/skills are static reviewed digest-pinned advisory blocks (`memory_loader.py`, `skills_loader.py` — governance-ahead, learning-absent); subagents behind `HARNESS_SUBAGENTS` with W28 S1b incomplete; warm pools (S-07) unbuilt.

## 2. Owner idea #1 — approval rendering by reader (audience-adaptive approvals)

**Idea:** approval requests render in selectable "tones" — technical, plain, and (proposed third) a structured visual **decision card** — so non-technical members can actually understand what they approve; visual aids in chat.

**Analysis (adopt with guardrails):**

- Attacks the 93% blind-approval number at its root: approvals are written engineer-to-engineer. Nobody in the market re-renders the request for the reader.
- **Tone is translation, never replacement.** Technical detail always one tap away; the risk tier is computed by the policy engine, never by prose; the model may re-render wording, never decide what is disclosed.
- **Templates first** (deterministic, instant, snapshot-tested); optional cached LLM plain-language line, obeying plan rule 5.8 (unknown shown as unknown).
- **Profile-based default** (admin sets per member; member self-selects own; quick per-card switch remembered) — not a per-approval dropdown, which would add the fatigue it cures.
- Accessibility: risk badge = icon + color + text label (never color alone); axe-clean, keyboard-only journey required.
- **Tone fixes comprehension, not volume.** Must ship with risk-tiered envelopes (plan 4.10; decisions D-U4/P-D6) and batch review; KPI = **blind-approval rate**, driven toward ~0.
- Maps to plan 4.1 (denial explains reason + safe alternative), 5.6 (pre-review digest — this is its presentation layer), 5.8, UI-week U3 verdict card.

## 3. Owner idea #2 — the geometric context store (evolution: hexagon → triangle → edge-storing triangles)

**Goal (owner):** structured storage of context/memory/skills to push cache-hit rate toward 95%+, maintained by multiple agents, usable everywhere in the harness.

**Analysis trail (2026-11-22):**

1. **Hexagon lattice:** hex tilings have a real precedent (Uber H3 — hexagonal hierarchical geospatial indexing) but adjacency there = physical distance; context adjacency = semantic similarity, and a lattice imposes the wrong metric. **Primes** have no spatial-placement retrieval mechanism, but a legitimate home one layer down: prime-modulo hashing for content-addressed keys and shard placement (plan S-03).
2. **Triangle:** hex and triangle tilings are mathematical duals (Voronoi ↔ Delaunay) — same information, same limitation. But a triangle is a **2-simplex: a first-class three-way relation** (simplicial complex = geometric hypergraph). "Decision D on file F inside task T" survives as one unit instead of dissolving into three pairwise edges. The triangle inequality already does the semantic work in the ANN layer (HNSW), not the layout.
3. **Edge-storing triangles (final owner refinement):** edges carry typed information and act as portals to similar content; corners carry entity payloads; overflow joins adjacent triangles along a shared edge. Every element has precedent: edge properties (property graphs), similarity-edge navigation (HNSW small-world), adaptive density (TIN/AMR — finer triangles where information density is higher), shared-edge growth (mesh topology).

**Resulting design (prototype spec):**

- **Vertices** = entities + payload references (task, file, person, decision, skill).
- **Edges, two types:** *structural* (shared seams — same event family; traversal) and *similarity* (curator-maintained links to resembling content across regions).
- **Faces** = 3-way facts with provenance (reviewer, timestamp, revision) — one unit to retrieve, compact, cache, audit.
- **Operations:** **attach/subdivide** on overflow (nothing lost; adaptive density) and **collapse/coarsen** for compaction and decay (summary-triangles keep provenance links). Together they are the memory lifecycle as mesh operations: refine when hot, coarsen when cold. Growth operations are event-sourced and replayable.
- **Physical store stays Postgres rows** (triangles are rows + typed edge tables); coordinates are logical, for the admin UI map only; **coordinates never enter prompts** (token cost); serialization is **hash-ordered, never discovery-ordered**, to protect prefix-cache stability.
- **Curator subagents** (W28 delegation) maintain similarity edges and propose coarsenings **off the request path** (Letta "sleep-time compute"), gated by the existing reviewed-status injection rule (`memory_loader` injects only reviewed rows) — no new authority (§2 boundary).
- **Caps:** per-project triangle/link caps mirroring `memory_loader` `MAX_MEMORY_ENTRIES`/`MAX_TOTAL_BYTES`; LRU/decay feeds collapse.

**Where this sits in the memory picture:** built vs proposed for the whole hybrid (project memory, case memory, this store) is mapped in `docs/briefs/memory/hybrid-memory-architecture.md`.

**Honest cache note:** a store cannot raise provider cache-hit rate directly — hits are byte-identical prefix matches. The chain is: store → stable canonical serialization (S-02) → prefix stability → hits. Within-run 80–95% is realistic with a stable prefix; cross-run depends on workload similarity; **the number is measured (S-01 discipline), never promised.** The geometric store is a **registered candidate experiment under Phase 9 discipline** (adopt/pilot/defer/reject with evidence), benchmarked against the plain-graph baseline — see X-14.

## 4. The wave program (IDs `X-*`; normative table lives in FUTURE PLAN 2.0 §20)

| Wave | Theme | Items | Runs inside |
|---|---|---|---|
| A | Fix what's broken | X-01…X-06 | Phases 0–1 |
| B | Approval ergonomics | X-07, X-08 (+ X-D1) | UI week + Phases 3–4 |
| C | Context economics | X-09…X-11 | Phase 8 |
| D | Context store + curators (candidate) | X-12…X-15 (+ X-D2) | Phase 9-style candidate |
| E | 10X differentiators | X-16…X-20 | Phases 4–5 (+ Phase 8 for X-20) |

**Sequencing rule:** C's S-02/S-03 foundation ships before D's store is judged; A is a prerequisite for everything (CI fix unblocks drift check + scanners in one move); B needs D-U4/P-D6 decision records first.

## 5. Measurement and kill criteria

- Every X item lands evidence under `docs/briefs/evidence/10x/` at a named revision (§16 weekly-ledger rule applies; no entry = not done).
- Speed/cache claims need the plan-8 comparison protocol: same model, same task, ≥5 trials; wall-time split (model / tool / harness / human-wait) from `bench_stats.py` (S-01).
- **X-14 kill criterion:** if the simplicial store does not beat the plain-graph baseline on decision-provenance retrieval quality at equal-or-better cost and latency, record `reject` and keep the graph baseline; the hex map may still ship as read-only visualization if member-scoping tests pass.
- **X-07 kill criterion:** if blind-approval rate does not fall on pilot tasks after Wave B, tone rendering is revisited before adding more surfaces.
- Blind-approval rate, cache-read ratio, fan-out token multiplier, and intervention rate become standing KPIs (Grafana, next to cost).

## 6. Anti-goals (carried + new)

Carried from plan §13 "Not to copy": free-write agent memory; same-OS-user-plus-denylist as a boundary; cross-tenant cache sharing; time limits as the correctness mechanism; caching general agent-step responses. New from the landscape: **no scaffold bloat** (mini-swe-agent's ~100-line harness beats elaborate scaffolds — keep the minimal path short; complexity must pay for itself in measured task success); **no feature-parity chase** of Claude Code's extensibility surface (parity on the six §1 table stakes only); **no LLM-decided disclosure** in approval rendering (policy engine decides what is mandatory; tone layers re-render only); **no geometry in byte layout or prompts** (geometry is logical/visual; the store is rows).

## 7. Landscape sources (major claims)

- OpenAI, "Harness engineering" — <https://openai.com/index/harness-engineering>; "Inside the Codex sandbox" — <https://openai.com/index/inside-the-codex-sandbox>; Codex escapes analysis — <https://devops.com/codex-sandbox-escapes-show-why-agent-guardrails-cant-live-inside-the-agent>
- Martin Fowler/Böckeler, "Harness engineering" — <https://martinfowler.com/articles/harness-engineering.html>; Addy Osmani — <https://addyosmani.com/blog/agent-harness-engineering>; counterpoint — <https://future-seems-so-good.com/blog/the-harness-is-in-the-weights>
- Terminal-Bench 2.0 announcement (harness variance) — <https://www.tbench.ai/news/announcement-2-0>; HAL (cost-aware evals) — <https://hal.cs.princeton.edu>, <https://arxiv.org/abs/2510.11977>; mini-swe-agent — <https://github.com/swe-agent/mini-swe-agent>
- Anthropic, multi-agent research system (15× multiplier) — <https://www.anthropic.com/engineering/built-multi-agent-research-system>; context engineering — <https://www.anthropic.com/engineering/effective-context-engineering>; server-side compaction — <https://platform.claude.com/docs/en/build-with-claude/compaction>; Claude Code changelog — <https://code.claude.com/docs/en/changelog>
- CSA governance gap — <https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-agent-governance-framework-gap-20260403>; Dataiku governance statistics — <https://www.dataiku.com/blog/ai-governance-and-accountability>
- pydantic-ai durable execution — <https://pydantic.dev/docs/ai/capabilities/durable_execution/overview>; Diagrid framework taxonomy — <https://www.diagrid.io/blog/ai-agent-frameworks>; LangGraph time travel — <https://docs.langchain.com/oss/python/langchain/frontend/time-travel>
- Letta sleep-time compute — <https://www.letta.com/blog/sleep-time-compute>; Cline context engineering — <https://cline.bot/blog/how-to-think-about-context-engineering-in-cline>; Amp Orbs — <https://ampcode.com/chronicle>; OTel GenAI semconv (development status) — <https://opentelemetry.io/docs/specs/semconv/gen-ai>
