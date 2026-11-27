# Hybrid memory architecture (A + B + C): what is built, what is proposed

**Date:** 2026-11-25 · **Revision read:** `fe2233c` (branch `w28-s2-sprints1-4`) · **Docs only.** This page changes no behaviour, schema, prompt or contract.

"Hybrid memory" is not one shipped product and there is no earlier document with that name. It is the umbrella for three layers that differ in status. Every path below was read in the repo at the revision above.

```
 run start / plan proposal (section4)                    denial stop (section4)
 ┌────────────────────────────────────────────┐          ┌─────────────────────────────────┐
 │ A  Governed project memory   BUILT         │          │ B  Case memory         BUILT    │
 │    reviewed, advisory prompt block         │          │    past denial stops, recall    │
 │    rules → skills → MEMORY → @ref bundle   │          │    read-only UX on the approval │
 └───────────────▲────────────────────────────┘          └───────────────▲─────────────────┘
                 │ human review (identity API, 0067)                     │ human PUT on rules API
                 │                                                       │ (never auto-promoted, CD-02)
 ┌───────────────┴───────────────────────────────────────────────────────┴─────────────────┐
 │ C  Geometric / simplicial context store   CANDIDATE: in-memory prototype only, X-14 = DEFER│
 │    could later feed A's snapshot; no schema, no prompt injection, nothing imports it       │
 └─────────────────────────────────────────────────────────────────────────────────────────┘
```

## Status at a glance

| Layer | Status | Prompt bytes? | Schema |
|---|---|---|---|
| A. Governed project memory (W12) | **Implemented** | Yes: one advisory block, reviewed rows only | `0067_project_memory.sql` |
| B. Case memory (denial recall) | **Implemented** (Sprint 2–5) | No. Shown to the human next to a stop; adds one sentence to the stop detail text | `0080_case_memory.sql` |
| C. Simplicial context store | **Proposed.** X-12 prototype exists as an isolated experiment; X-14 dry benchmark verdict **DEFER** | No | None (no migration) |

Boundaries that apply to all three: memory is advisory and never grants capability; Section 5 stays the only hard enforcement; no auto-promotion of learned rules (CD-02, default "proposals only", record still a DRAFT: `docs/decisions/2026-11-24-cd-02-learned-memory-skills-DRAFT.md`); migrations are forward-only; verdict stays NOT READY until owner gates say otherwise. Sources: `AGENTS.md`, `FUTURE PLAN 2.0.md` §2.

## Layer A: governed project memory (W12)

- **Store and API (identity, section1):** `section1/src/harness_identity/memory_api.py`, router prefix `/api/v1`: `GET|POST /projects/{project_id}/memory`, `POST …/memory/{entry_id}/review`, `DELETE …/memory/{entry_id}`. Mounted in `section1/src/harness_identity/app.py`. Test: `section1/tests/test_memory_api.py`.
- **Migration:** `section1/migrations/0067_project_memory.sql`. Status `draft` (visible, never injected) → `reviewed` (injected). `expires_at` optional; text capped at 16 KiB. `source='run'` is reserved for a future lane and would need its own review.
- **Loader (section4):** `section4/src/harness_orchestrator/memory_loader.py`. `load_project_memory` reads only `status='reviewed'` and unexpired rows; any failure degrades to "no memory". `build_memory_block` renders one block headed `PROJECT MEMORY (reviewed, advisory)`. Caps: `MAX_MEMORY_ENTRIES = 20`, `MAX_TOTAL_BYTES = 32 KiB`.
- **Wiring:** loaded in `service.py` at plan proposal (`memory_loader.load_project_memory` / `build_memory_block`), stored as `run_data["memory_prompt_block"]`, assembled into the executor prompt in this order: rules → skills → project memory → `@ref` bundle. Skills are the sibling lane: `section4/src/harness_orchestrator/skills_loader.py`.
- **Evidence:** drill `scripts/e2e/w12_skills_memory_drill.py`; results `docs/briefs/evidence/eval/w12-skills-memory-*.json`; unit test `section4/tests/unit/test_memory_loader.py`.

## Layer B: case memory (denial recall)

- **Module:** `section4/src/harness_orchestrator/case_memory.py`. A case = failure class + tool + paths + SHA-256 fingerprint + outcome (`recovered`/`denied`/`repeat`, weights 1.0/0.6/0.3) + a bounded suggested rule. Always `status="proposed"`. Weight halves every 168 h, applied on read.
- **Similarity:** Jaccard over paths plus 0.25 for the same tool, computed **in Python** over the team's newest `MAX_SCAN = 200` cases; ties broken by case id (deterministic). `MAX_RECALLED = 3` ride on the run record. The module header names pgvector fingerprint search as the upgrade path if a team outgrows the window. **That path is not built.**
- **Migration:** `section1/migrations/0080_case_memory.sql`. `status` is CHECK-constrained to `'proposed'`; index `(team_id, recorded_at DESC)`; every read filters `team_id`.
- **Write path:** the denial stop in `service.py` calls `recall_and_record` (reads recent cases first so a case never matches itself, then stores the new one) and appends "Seen before in N earlier run(s) of this team." to the stop detail.
- **API:** `GET /runs/{runId}/learned-rule` in `section4/src/harness_orchestrator/api.py` returns `{offer, similarCases}` read-only; logic in `collab.py` (`similar_cases_for_run`, `learned_rule_for_run`). Saving a suggested rule is still the human `PUT /projects/{id}/rules`. Companion module: `denial_learning.py` (I-08).
- **UI:** `section7/src/components/workspace/LearnedRuleOffer.test.tsx` covers the read-only offer (Sprint 5 Task 37).
- **Tests:** `section4/tests/unit/test_case_memory.py`, `test_denial_learning.py`, `test_similar_cases_api.py`, `test_learned_rule_api.py`. Sprint 5 gap-close evidence: `docs/briefs/evidence/sprint5-gap-close/pr-27-review.md` (the live Mac `similarCases` PASS is recorded there; this page does not re-verify it).

## Layer C: geometric / simplicial context store (candidate)

- **Design:** `docs/briefs/harness-10x-plan-2026-11-22.md` §3 (vertices, structural and similarity edges, 3-way faces, attach/subdivide and collapse/coarsen, curator subagents) and §5 (kill criterion). Plan items: `FUTURE PLAN 2.0.md` §20, X-12 (prototype), X-13 (curators), X-14 (layout benchmark), X-15 (hex map).
- **What exists:** an in-memory, stdlib-only X-12 prototype at `experiments/context-store/simplicial/__init__.py` with tests in `experiments/context-store/tests/test_simplicial_store.py`. Faces are 3-way facts with provenance; curators only *propose* edges into a review queue; `serialize()` is hash-ordered with no vectors or coordinates; cap 20 faces mirrors `memory_loader`. Nothing in any section imports it and no migration exists.
- **Not built (by design, per its README):** attach/subdivide and collapse/coarsen mesh ops, Postgres tables, event-sourced replay, X-13 curators, X-15 map, any prompt injection.
- **X-14 dry benchmark:** `docs/briefs/evidence/10x/x14-layout-benchmark-2026-11-24.md`. Faces beat the plain graph (whole-fact@3 1.00 vs 0.83) but only **tie** flat chunks (hit@3 1.00 both). Verdict **DEFER**: no production code until a corpus shows a gap. Cache-read rates were not measured (no provider called).
- **Cache honesty:** a store cannot raise provider cache hits by itself; hits need a byte-stable prefix (S-02, plan item 8.2, X-09). Any number is measured, never promised.

## Related, but not part of A/B/C

- **Hybrid keyword + vector retrieval** for `@refs` shipped as X2: `section4/src/harness_orchestrator/context_resolver.py` (hybrid ranking), CHANGELOG entry X2. The pure-vector gap analysis is in `docs/archive/m2-w2-plan.md` (moved from `docs/` by X-05).
- **Learned memory and skills (S-10 / X-20):** `FUTURE PLAN 2.0.md` item 8.10 and X-20. Proposals only, staged for human approval, benchmark-gated promotion, scope per CD-02. Not implemented.

## Open gaps

1. Case similarity is Python over recent rows; pgvector or an indexed fingerprint lookup is undesigned.
2. X-12 mesh operations and persistence are unbuilt, and X-14 says do not build them yet.
3. S-02 / X-09 (frozen prefix snapshot at run start, CI test that volatile data stays out of the prefix) is the prerequisite for any measured cache claim across A, B and C. Status not verified here.
4. Blind-approval KPI has no pilot data.
5. CD-02 remains an unsigned DRAFT.

## Answering "what is built vs proposed?"

Built: A and B, end to end, with migrations, APIs, tests and evidence. Prototype only: C's store (isolated, in-memory). Proposed: C's persistence, curators and map, pgvector case search, and learned-memory promotion.
