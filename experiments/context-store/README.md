# X-12 candidate — simplicial context store (prototype)

Design: `docs/briefs/harness-10x-plan-2026-11-22.md` §3. Registered **candidate experiment** under
Phase 9 discipline: nothing here is imported by any section, and no migration exists for it.

- Faces = 3-way facts with provenance; retrieval returns whole faces.
- ANN top-k (brute-force cosine here; pgvector HNSW 256-d in section11 for production) + pluggable
  rescore (`identity_rescore` default, `poincare_rescore` hyperbolic hybrid).
- Curators propose similarity edges into a review queue; a different named human accepts (CD-02).
- `serialize()` is hash-ordered and carries no vectors or coordinates (S-02 prefix stability).
- Cap = 20 faces/project (mirrors `memory_loader.MAX_MEMORY_ENTRIES`); overflow forces coarsening.

**Not built (stubs by design):** attach/subdivide and collapse/coarsen mesh ops, Postgres tables,
injection into prompts. Add only if the benchmark below passes.

**Kill criterion (X-14):** if face retrieval does not beat the plain-graph / flat-HNSW baseline on
the dry corpus (recall@k and serialized-prefix stability), the candidate is **deferred** with the
evidence; no production code is written for it. Paid trials need an agreed budget (X-D2).

    make test   # PYTHONPATH=. python -m pytest tests -q
