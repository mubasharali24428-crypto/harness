# A2-S1 — Repository intelligence and grounded planning (plan)

Owner: A2 (Execution, Context, Memory and Multi-Agent Runtime).
Baseline: `31bf4077e9b567b1f50ecb99818d501baab3115a` (worktree `harness-a2`, branch `codex/harness-a2`).
Features: F011, F012, F013, F014, F015, F016. Risks exercised: VR005, VR006 (context to execution — lands in S2), VR009 (workspace isolation — lands in S3), VR022/VR029 (version/retrievability hygiene).

## What changes (all inside A2-owned paths)

| File | Change | Feature |
|---|---|---|
| `section11/src/harness_context/secret_scan.py` | NEW — conservative secret-pattern scrub for indexed content + secret-file exclusion helper | F011 support (no secret hashing into shared caches) |
| `section11/src/harness_context/chunking.py` | scrub text on the disk-read path (`chunk_file`) + extend `should_index` secret-file exclusions | F011 |
| `section11/src/harness_context/manifest.py` | NEW — content-addressed freshness report (new/changed/unchanged/deleted) | F011 |
| `section11/src/harness_context/service.py` | extract `classify_repo` (single classification source used by reindex + freshness); add `search_multi` | F011, F013 |
| `section11/src/harness_context/git_history.py` | NEW — bounded, injection-safe `git log` retrieval (message/path queries) | F012 |
| `section11/src/harness_context/graph.py` | NEW — import/require/link dependency-edge extraction from indexed content + deterministic graph build | F014 |
| `section11/src/harness_context/symbols.py` | NEW — Python AST symbol table (defs + approximate references), TS/JS regex defs; honestly labelled (NOT a full call graph) | F015 |
| `section11/src/harness_context/ranking.py` | NEW — goal-driven auto retrieval + multi-query merge + token budget + per-chunk reasons | F016 |
| `section11/src/harness_context/report.py` | NEW — standalone inline-SVG HTML renderer for the dependency graph (own report artifact; no A4 UI edits) | F014 |
| `section11/src/harness_context/authz.py` | NEW — project→team ownership check for retrieval/ingestion/deletion routes (fail closed) | cross-cutting (assignment) |
| `section11/src/harness_context/models.py` | additive wire models for new endpoints | all |
| `section11/src/harness_context/api.py` | wire authz into existing routes; add freshness/git-search/search-multi/graph/symbols/rank routes | all |
| `section11/tests/*` | new suites + fixture updates where the new authz layer legitimately changes a precondition (documented per test) | all |

No new database tables, no migrations, no contract changes: every S1 feature derives from the existing
`context_chunks` index (migration 0030), live `git`, and in-memory job registry. Nothing cross-lane is required.

## Dependencies on existing APIs (baseline, unchanged)

- `ContextStore` / `context_chunks` DDL (section11/sql.py, migration 0030).
- `contracts.make_bearer_dep` principal resolution; `projects(team_id)` table read-only for authz (same pattern as `_enforce_context_cap`).
- Index job registry (`ContextService.jobs`) is the repo-root authority for git-search (fail closed on unknown root).

## Acceptance cases (executed in tests)

1. Incremental index: edit → `filesIndexed`/`chunksIndexed` reflect only changed file; rename → prune + insert;
   delete → prune; ignored/secret files never indexed; skip-proof (`embed_calls` unchanged on unchanged re-index).
2. Freshness report matches classification: stale=1 after one edit, 0 after re-index; deleted files listed.
3. Git history: fixture repo with N commits; message query returns exactly the matching commit; path filter honored;
   non-git dir → empty + reason (no crash); limit and timeout bounded.
4. Cross-repo search: two projects, one query; per-project results; foreign-team project denied 404 (no oracle);
   unknown project denied 404.
5. Graph: python imports (absolute + relative), TS imports, md links; external packages counted not invented as nodes;
   cycle-safe; renderer emits standalone HTML (no external assets).
6. Symbols: known defs with exact line ranges; references found; TS regex defs; explicit "not a call graph" note.
7. Ranking: distinctive-token fixture repo; recall@3 vs known targets; budget drop counts; reasons present.
8. Authz on existing routes: search/index deny foreign team 404; existing same-team behavior preserved.
9. Retrieval quality harness: precision/recall over known query set recorded as evidence.

## Rollback

All changes are additive files + additive routes; the only behavior change to existing routes is the new
authz precondition on `/context/search`, `/context/index*` (documented; tests updated accordingly).
Rollback = revert the commit(s) for `section11/` in this branch; no data migration involved.

## Out of scope (explicit)

- Execution-side injection of the ranked bundle → A2-S2 (F016 closes there; S1 ships the ranking engine + evidence).
- Durable cross-restart repo-root registry → NEEDS INTEGRATION note for the migration owner (A1).
- Call-graph accuracy claims → deliberately NOT claimed (references are approximate; review says "do not conflate").
