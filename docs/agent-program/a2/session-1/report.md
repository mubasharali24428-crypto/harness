# A2-S1 — Repository intelligence and grounded planning (report)

Owner: A2. Worktree: `harness-a2` (branch `codex/harness-a2`), baseline `31bf4077e9b567b1f50ecb99818d501baab3115a`.
Plan: `plan.md` (this directory). Evidence: `evidence/`.

## Verdict

All six planned features (F011–F016) implemented and VERIFIED, plus the cross-cutting
project-ownership authz layer. Suite: **111 passed, 1 skipped** (`evidence/pytest-full.txt`);
ruff clean (`evidence/ruff.txt`). Baseline was 50 collected (49 pass + 1 docker-gated skip);
final is 112 collected — **62 new tests, zero regressions, zero contract changes**.

The 1 skip is `tests/test_live_pg.py::test_live_pg_roundtrip` (requires a live docker
daemon + pgvector image) — environmental, same skip as baseline.

## Feature states

| Feature | State | Proof |
|---|---|---|
| F011 freshness + incremental classification + secret hygiene | VERIFIED | `tests/test_freshness.py` (5), `tests/test_secret_hygiene.py` (7), baseline `test_ingestion`/`test_chunking`/`test_chunking_ast` (24) |
| F012 bounded git-history retrieval | VERIFIED | `tests/test_git_history.py` (12) |
| F013 one-query multi-project search | VERIFIED | `tests/test_multi_search.py` (4) |
| F014 dependency graph + HTML report | VERIFIED | `tests/test_graph_symbols.py` (10), `tests/test_report.py` (6) |
| F015 symbol map + references | VERIFIED | `tests/test_graph_symbols.py` (10) |
| F016 goal-driven ranking + token budget | VERIFIED (engine); execution-side injection deferred by plan | `tests/test_ranking.py` (7), `tests/test_retrieval_quality.py` (1 golden harness) |
| Cross-cutting: project→team ownership (fail closed, no-oracle 404) | VERIFIED | `tests/test_authz.py` (10) |

Per-file test counts: `evidence/test-matrix.txt`.

## What shipped

New modules (all `section11/src/harness_context/`): `secret_scan.py`, `classify.py`,
`manifest.py`, `git_history.py`, `graph.py`, `symbols.py`, `ranking.py`, `report.py`,
`authz.py`. Extended: `service.py` (reindex refactored onto `classify_repo`; `freshness`,
`search_multi`, `rank`, `latest_job_root`, `is_known_repo`), `store.py` (`fetch_text`),
`chunking.py` (secret exclusion + scrub-before-hash), `models.py` (additive wire models),
`api.py` (authz on every route + 7 new endpoints: `search-multi`, `rank`, `git-search`,
`freshness/{id}`, `graph/{id}`, `report/graph/{id}`, `symbols/{id}`).

No new tables, no migrations, no changes to any frozen contract (W21-C1 search payload
shape proven unchanged by baseline `test_search` + `test_api`). Serving the report as
inline-SVG HTML required no A4 UI edits.

## Bugs found and fixed during verification (each has a regression test)

1. **git name-status parser desync** (`git_history._parse_name_status`): git emits a literal
   `\n` after the format head even under `-z`, so the first status token arrives as `"\nM"`.
   The alpha-guard skipped it and shifted the whole token stream (first file of every commit
   mis-parsed as `{"A": "A"}` in the fixture repo). Fixed by stripping the separator newline;
   pinned by `test_search_matches_message_and_reports_name_status`.
2. **graph python classification inverted** (`graph.build_dependency_graph`): the
   unresolved-vs-external branch treated dotless absolute imports (`os`) as *unresolved*
   instead of *external*. Fixed: only failing RELATIVE imports count as unresolved; failing
   absolute specs aggregate as external. Pinned by
   `test_python_imports_resolve_to_repo_nodes_and_externals`.
3. **lang fallback blind spot** (`graph`/`symbols`): without a stored lang map, the raw file
   extension (`py`) never matched the language set (`python`), silently disabling the parser.
   Added extension→language fallback maps in both modules; pinned by the unit-level graph and
   symbol tests (they run without any store rows).

## Test-infrastructure change (documented, no production impact)

The `db` fixture switched from `sqlite+aiosqlite://` (in-memory) to a per-test FILE-backed
SQLite (`tmp_path/test.db`). In-memory mode multiplexes every session onto ONE connection
(StaticPool): the authz/bearer SELECTs made during HTTP polling could land inside the
background re-index's open transaction and roll back part of its writes (reproduced:
8 chunks indexed, 4–6 rows persisted, deterministic). File-backed isolation mirrors the
asyncpg pool's per-session connections. Proof: `test_index_lifecycle_over_http` was failing
6≠8 before the switch and passes deterministically (suite green 3×) after.

## Precondition updates to baseline tests (consequence of the new authz layer — intentional)

- `conftest.py`: added the frozen `projects(id, team_id)` mirror DDL + `seed_project()` helper.
- `test_api.py`: module autouse fixture owns `p-api` (documented in the fixture docstring).
- `test_tiers.py`: `_add_tables` now always binds `p-cap`→`team-x1`; billing rows remain
  conditional so "no billing infra" still exercises free defaults.
- `test_missing_project_row_falls_back_to_free_defaults` → renamed
  `test_unknown_project_is_denied_not_free_defaults` with NEW semantics. **Intentional
  contract change**: an unknown projectId used to reach tier logic (answering 402/200 —
  an existence oracle for cross-team probing). It is now denied with the same no-oracle 404
  as a foreign team's project. The old "fall back to free defaults" intent remains covered
  (project known, billing absent) by `test_free_defaults_enforce_cap_without_any_billing_rows`.
- `test_ingestion.py` (baseline suite) passes **unmodified**; the legacy-window→AST
  migration seam (`monkeypatch service.chunk_file`) was preserved by making `classify_repo`'s
  chunker injectable and re-exporting the module-global binding from `service.py`.

## Decisions log

1. No new DB tables/migrations: every S1 feature derives from migration 0030's
   `context_chunks`, live `git`, and the in-memory job registry. Rejected alternative:
   a `repo_roots` table (owned by the migration lane; see NEEDS INTEGRATION).
2. Git-search resolves repos **server-side** from the project's most recent index job —
   the request schema deliberately has no `repoPath` field (a client cannot point history
   retrieval at an arbitrary directory).
3. Secrets: precision-first redaction (PEM/AWS/GitHub/Slack/OpenAI-style/Google/JWT).
   Rejected fuzzy password heuristics — over-redaction would poison retrieval quality.
4. `search_multi` embeds the query exactly once (proven by a counting backend); authz runs
   BEFORE work on every listed project; one foreign id denies the whole call.
5. Ranking ties are deterministic (`-score, path, startLine`); budget skips-and-continues
   (a smaller later chunk may still fit) with `droppedForBudget` reported, never silent.

## NEEDS INTEGRATION (honest gaps, none blocking S1 function)

- **Durable repo-root registry**: `latest_job_root` reads the in-memory job registry, so
  freshness/git-search 404 after a restart even for a previously indexed project. A durable
  `repo_root` column/table is a migration-lane asset (A1); until then this is
  single-process-posture by design, not a bug. Documented in `service.latest_job_root`.
- **Execution-side use of the ranked bundle** (VR005/VR006 context→execution): the S1
  engine + evidence exist; wiring the bundle into agent execution lands in A2-S2 per plan.
- **Workspace isolation (VR009)**: S3 scope.

## Rollback

Revert the `section11/` diff on this branch; no data migration. The only behavior change to
pre-existing routes is the additive authz precondition (documented above).

## Addendum (found in S2, honest correction)

S1's regression check ran section11's suite only. A CROSS-SECTION test in section4 —
`tests/test_context_resolver.py::test_resolve_roundtrip_against_real_section11_index`,
which boots the real section11 app in a sibling-venv subprocess — was broken by the S1
authz layer (index POST → 403 AUTHZ_UNAVAILABLE because its ad-hoc DB has no `projects`
table; correct fail-closed behavior, missing precondition). Fixed in S2 by seeding the
frozen projects mirror (proj-x2 → team-x2) in that driver, same class of update as S1's
section11 fixture changes. Lesson recorded: cross-section consumers of owned routes must
be enumerated in the session's regression sweep, not just the owning section's suite.
