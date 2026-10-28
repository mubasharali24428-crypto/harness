# Marathon 2.0 W2 — code intelligence: audit before any index (M2-W2)

Follows the 2026-10-28 Marathon 2.0 roadmap, W2 row, and
[the W1 baseline](m2-w1-plan.md). This is **Marathon 2.0 W2**, distinct from
the historical W2 (context, memory and the contract bump), whose work is already
in the tree.

Governing rule: *an unchecked gate stays open when a dependent week begins.*
W1's baseline is checked (3/3 executed, 22 deferred with reasons), so W2 starts —
but only with an audit and a measurement, not a new index.

## Purpose

Find the smallest search capability that lets a representative repository task
locate the right files **within the existing boundary**, and decide what to do
with Section 11's index. The roadmap is explicit: assess Section 11 before
commissioning another index.

## Audit (2026-10-28)

| Area | What exists | Where | Finding |
|---|---|---|---|
| Section 11 service | `harness_context`: FastAPI router (`/context/index`, `/context/search`), chunking, embeddings, store; 1,312 LOC | `section11/src/harness_context/` | **Not deployed.** It is absent from `deploy/docker-compose.yml` and not installed in the backend image (`import harness_context` fails in the orchestrator container). It is a package with tests, not a running capability. |
| Search algorithm | `_embed_many([query])`, then `fetch_vectors(project)` and exact cosine over every chunk, sorted in Python | `service.py:279-299` | O(N·d) per query in pure Python. Measured, 256 dims: **24 ms / 1k chunks, 119 ms / 5k, 240 ms / 10k, 1.2 s / 50k** (median of 3, before the DB fetch). No ANN, no keyword/hybrid path, no lexical fallback. |
| Embedding backend | Ollama `nomic-embed-text` by default, hashing backend in tests | `config.py`, `embeddings.py` | The local embedding model is present on this host, so a real measurement is possible; CI uses deterministic hashing. |
| Section 11 tests | search shape/scope/ranking, chunking, ingestion, API | `section11/tests/` | 11 search+chunking tests pass (SQLite + hashing). Relevance is asserted on a mini-repo; no precision/latency measurement at realistic size. |
| Agent search tool | native `tool-fs-search` (glob/grep) mounted first-class by the engine | `engine.py:295-299` | Exists and avoids a shell round-trip. Its workspace/forbidden-path bounds are **not proven** here; that is M01's question, and M01 is open. |
| Repo map | none | — | The planner has no compact structural view (tree + languages + symbols) to choose files without reading them. |
| Evaluation task | `code-search` in the corpus is `fixture-owed` | `docs/eval/corpus.json` | W2 must build the fixture and the independent verifier or the capability stays unmeasured. |

### What the audit decides

1. **Do not deploy Section 11 in W2.** It is not wired, its query path is exact
   cosine over all project vectors (1.2 s at 50k chunks before the DB fetch),
   and it has no hybrid or keyword path. Deploying it now would add a service
   and a data path without evidence that it beats `glob`/`grep` on the task the
   product actually runs. It stays a candidate for semantic retrieval after a
   measured need.
2. **Measure agent-side search first.** The existing `tool-fs-search` plus a
   bounded repository map is the MVP path: read-only, no network, no new
   credentials, and it fits the Section 5 boundary the product already enforces.
3. **A repo map is a hypothesis, not a conclusion.** It is only worth shipping
   if the measurement shows a task-level improvement (files found, tokens,
   time) over plain `glob`/`grep`.

## The gate, expanded into claims that must be shown

1. **A representative repo task finds the relevant code.** A fixture repo with
   a planted, independently verifiable answer: the agent must locate and use the
   right file(s) within a fixed tool budget.
2. **The measurement is independent of the agent's report.** The verifier reads
   the files the run actually touched/edited and the fixture's own test command;
   the answer text is not the verdict.
3. **Bounds hold.** Every search path is read-only, workspace-confined, capped
   (bytes/results/time), and cannot reach the network. No capability is widened
   while M01 is open.
4. **The decision is recorded.** Section 11 stays undeployed with the measured
   reason; the repo-map decision is made on the same tasks and budgets, not on
   intuition.
5. **The corpus gap closes.** `code-search` moves from `fixture-owed` to
   `implemented` with a driver and a verifier, and runs in the next baseline.

## Decisions

1. **Read-only, in-boundary only.** No new executable, server, model or network
   destination. Search tools obey the workspace and forbidden-path policy; a
   refusal is recorded, not bypassed.
2. **Same-task comparison.** Any repo-map or Section 11 experiment uses the same
   fixture, queries, budgets and model as the `glob`/`grep` baseline; success,
   tokens, cost and wall clock are compared, not just relevance scores.
3. **n=1 first, then repeats.** Like W1, the first measurement is an existence
   proof labelled `repeats: 1`; promotion needs repeated runs.
4. **Keep the held-out split.** The code-search fixture's held-out variant is not
   used to tune the map or the prompt.

## Slices

1. **Measurement harness.** A synthetic repository (200–500 files, several
   languages) with 10 queries whose relevant chunks are known; measure
   `glob`/`grep` recall@5 and latency, and Section 11 exact cosine at
   1k/5k/10k/50k chunks (the last already measured: 24/119/240/1204 ms).
2. **Code-search task.** Fixture repo + goal + independent verifier; wire it into
   `docs/eval/corpus.json` as `implemented`.
3. **Repo-map prototype (conditional).** Build a compact map (tree, languages,
   file sizes, top symbols via regex/AST, capped) and feed it as context or a
   read-only tool; measure against slice 1. Ship only on a measured benefit.
4. **Decision record.** Section 11 vs agent-side search, with numbers; update the
   corpus and the roadmap if the answer differs from this plan.
5. **Close.** Outcome here, CHANGELOG entry, commit.

## What W2 will not claim

- No semantic-search quality claim and no Section 11 deployment.
- No ANN/embedding infrastructure, no new index service, no vector database.
- No general "finds any file" claim; only the fixed fixture and held-out variant.
- No capability widening while M01 is open.
- No Rust decision; profiling belongs to W14.

## Known constraints

- Section 11's production embedding path is Ollama; the measurement harness must
  state whether it used hashing or the local model.
- `tool-fs-search` bounds are unproven; if M01 finds a gap, W2's search work
  waits for the fix rather than shipping around it.
- Live code-search tasks spend model tokens; the first run is a small, budgeted
  sample.
