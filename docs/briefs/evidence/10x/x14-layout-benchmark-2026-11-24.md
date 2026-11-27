# X-14 dry layout benchmark — simplicial faces vs flat chunks vs plain graph (2026-11-24)

**Protocol** `x14-dry/1` (frozen: `experiments/context-store/benchmark/corpus.json`, 12 decision
facts restating `docs/decisions/*`, 12 paraphrased queries written before the run).
**Revision** `a0600cb8`. **Embedder** section11 `HashingEmbedding(256)` for every variant (no
paid or local model). **Artifact** `x14-dry-a0600cb8.json` (per-query rows included).

    cd experiments/context-store && make benchmark-x14-dry

| Variant | hit@1 | hit@3 | whole-fact@3 | tokens@3 (stub) | mean latency (µs) |
|---|---|---|---|---|---|
| flat chunks (section11 style) | 0.92 | 1.00 | 1.00 | 153 | ~280 |
| plain graph (3 edges per fact) | 0.83 | 0.92 | 0.83 | 122 | ~840 |
| simplicial faces, flat rescore | 0.92 | 1.00 | 1.00 | 153 | ~370 |
| simplicial faces, hyperbolic rescore | 0.92 | 1.00 | 1.00 | 153 | ~880 |

All variants miss the same hit@1 query ("who decides how approvals are rendered for a member"
returns D-05 first). Latency is pure Python on this machine, brute-force; it says nothing about
pgvector HNSW. Cache-read rates: N/A (no provider called).

## Reading
- Faces **do** fix the plain-graph failure the design targeted: a fact split into pairwise edges
  was only fully recovered 83% of the time; faces 100%.
- Faces **do not** beat a flat chunk that carries the same text: same embedding text, same
  ranking, same numbers. Hyperbolic rescoring changed nothing on this corpus and cost ~2x latency.

## Kill criterion (pre-registered in `experiments/context-store/README.md`)
"If face retrieval does not beat the plain-graph / flat-HNSW baseline … the candidate is deferred."
It beats plain-graph, it ties flat chunks → **Verdict: DEFER.** No production code; section11
unchanged. Re-open only with a corpus where three-way relations are not already co-located in one
chunk (e.g. facts assembled from separate events), or a semantic embedder, under a bumped protocol.

## Limits
12 facts / 12 queries is a smoke-scale dry run; hashing embeddings are lexical. No claim of
superiority or inferiority beyond this corpus belongs in STATUS or marketing.
