# MISSION 21 — HORIZON LATER phase 1: CONTEXT PILLAR (codebase indexing) — TASK GRAPH

> ALEX-ELITE dual teams. Baseline: b6cf6ae. Source: mission17-10x-plan.md
> §LATER — the single biggest multiplier (O-DELTA killer-gap #1). Migration
> slots: next free 0030+ (check ls).

| Lane | Mission | Owned paths | Team |
|---|---|---|---|
| X1 | Codebase indexing core: repo ingestion pipeline (clone/attach → chunked parse per language → embedding vectors → pgvector store); incremental re-index on change; index status API | section11/src/harness_context/ (NEW section) · migrations/0030_pgvector.sql · tests | 1 |
| X2 | @refs resolution service: parse @file, @symbol, @function references from meeting/brief text → resolved context bundles fed to planner; context budget management | section4/src/harness_orchestrator/context_resolver.py (NEW) · brief/planner integration · tests | 1 |
| X3 | Context UI surface: file-tree browser upgrades to searchable symbol index; @ref autocomplete in meeting composer; context-preview panel showing what the agent will see | section7 meeting composer + tree components · tests | 2 |
| X4 | Planner integration: plans derive from indexed context (retrieval-augmented) instead of pasted text only; retrieval scoring threshold; context-window budget management per plan | section4/src/harness_orchestrator/planner.py · tests with seeded index | 2 |

PAIR CONTRACTS: W21-C1 (X1↔X2): index query API {query, project_id, top_k}
→ scored chunks with file paths + line ranges. W21-C2 (X2↔X3): @ref syntax
documented + autocomplete payload shape.

DEPENDENCIES: X1 first (schema+pipeline), then X2/X3/X4 in parallel against
its frozen query API. Embedding provider: pluggable backend (Ollama local
default per AGENTS.md environment), never a hardcoded vendor.

Gate law: same as all waves. Exit criteria: a plan proposed with @refs
derives its steps from retrieved chunks (proven by test); stranger can
index a repo and ask a question that the answer demonstrably uses.
