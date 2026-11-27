# X-14 memory baseline: shipped memory layers vs a plain graph (2026-11-25)

**Protocol** `x14-memory-dry/1`, frozen corpus
`experiments/context-store/benchmark/memory_corpus.json` (12 project-memory notes shaped like
layer A, 12 denial cases shaped like layer B, 12 denial-stop queries with expected cases and
notes, written before the first run). **Revision** `ee0dcfc`. **Artifact**
`x14-memory-dry-ee0dcfc.json` (per-query rows). No model, no embedder, no spend.

    cd experiments/context-store && make benchmark-x14-memory-dry   # section4 venv

This is the plain-graph baseline the X-14 kill criterion names, built over the two memory
layers that actually ship. It is **not** a revival of the simplicial store: X-12 stays
**DEFER** (`x14-layout-benchmark-2026-11-24.md`).

## Variants

* **production**: what ships. Cases via `case_memory.retrieve_similar_cases` (path Jaccard +
  tool bonus, outcome weight, one-week half-life), top 3. Notes via `memory_loader`: no
  retrieval, every reviewed note in one advisory block.
* **plain_graph**: case / note / path / parent-dir / tool nodes. Score per seed path: 1.0
  shared path, 0.5 via shared parent dir, 0.25 via shared grandparent, plus 0.25 shared tool
  (cases). Top 3 cases, top 3 notes. No decay, no outcome weight.

## Result

| Variant | case hit@3 | case recall@3 | case noise@3 | note recall | note tokens (stub) | distinct note blocks |
|---|---|---|---|---|---|---|
| production | 0.80 | 0.70 | 0.75 | 1.00 | 550 | 1 |
| plain graph | 1.00 | 1.00 | 0.61 | 1.00 | 24 | 11 |

Case metrics over the 10 queries with an expected case; noise = share of returned cases not
expected, over all 12 queries.

## Reading

* **Cases:** production misses when the new denial hits a *different file in the same
  directory* (q3 `payroll/salaries.xlsx`, q6 `frontend/src/settings/Footer.tsx`): Jaccard on
  exact paths is 0, so only the tool bonus ranks, and recent unrelated cases win. The graph's
  directory hop finds them. Both variants pad the top 3 with tool-only matches (noise 0.75 vs
  0.61). The cheapest production follow-up this suggests is a parent-directory term in
  `case_memory.similarity`, not a graph store; that is a **proposal**, not implemented.
* **Notes:** both recall everything relevant. The graph sends ~23x fewer note tokens, but
  produces **11 different note blocks across 12 queries**, while production sends one block
  for every run. Per-run note retrieval would break the S-02 cross-run prefix cache (PR 39);
  the all-notes block is what keeps the prefix stable. At today's cap (20 notes / 32 KB) the
  stable block is the better trade.

## Limits

12 / 12 / 12 is smoke scale and the corpus was written by the same session that wrote the
graph scorer, with several same-directory queries; that favours the directory hop. Token
counts are `len/4` stubs. No claim beyond this corpus belongs in STATUS or marketing. A v2
corpus from real (anonymised) `case_memory` rows on the owner's stack would be the honest
next protocol.

## Boundaries

Read-only experiment, imported by no section, no prompt bytes, no write path, no migration,
no `contracts/` change (CD-02).

## Commands (cloud container)

```
make benchmark-x14-memory-dry                       -> exit 0 (table above)
PYTHONPATH=. section4/.venv/bin/python -m pytest tests -q   (in experiments/context-store) -> 30 passed (exit 0)
PYTHONPATH=. python3 -m pytest tests -q   (no section4 deps) -> 26 passed, 1 skipped (exit 0)
ruff check benchmark tests                          -> clean (exit 0)
```
