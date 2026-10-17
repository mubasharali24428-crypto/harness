# A4-S2 — Isolated Git work and reviewed pull requests

Baseline 31bf407, branch `codex/harness-a4`. Owned paths: `section2/**` (additions under `harness_github/gitflow/`, minimal edits to `client.py`/`app.py`), `docs/agent-program/a4/**`. Section 5 is neither imported nor edited.

## Defects found and fixed
Pre-existing (section2 baseline):
1. `GitHubClient.read_file` / `repo_tree` sent `ref` and `recursive` as a **JSON body on a GET**, so GitHub ignored them: every read hit the default branch regardless of `ref`. Now real query params; paths/refs URL-encoded. Regression test inspects the actual request URL.
2. Merge guard matched the bare substring `/merge`, falsely blocking legitimate refs/paths (e.g. branch `merge-fix`). Now matches the real endpoints (`/pulls/N/merge`, `/merges`, `/merge-upstream`, `/auto-merge`) after stripping the query; force/branch-delete/PR-PUT guards unchanged.
3. `openDraftPR` (legacy route) is not idempotent — a retry opens a duplicate PR. Left unchanged for compatibility; the new `/gitflow/pull-requests` route is idempotent.
Found in this session's own new code by its tests (fixed before commit): invalid run ids leaked `NamingError` instead of a typed worktree error; wildcard CODEOWNERS patterns wrongly recursed (GitHub: `docs/*` does not match `docs/a/b.md`); router was module-global so multiple apps duplicated routes; unknown project / no GitHub link returned 500 (now 404 / 409); non-draft provider response raised a 500 (now 502 `PROVIDER_NOT_DRAFT` naming the PR to convert); tenant check ran after the GitHub-link check (now first, so another team's project is a plain 404).

## What was built
`section2/src/harness_github/gitflow/`: `naming` (agent/<runId>, ref safety), `worktrees` (WorktreeManager), `commits` (draft + `commit_paths`), `pathpolicy`, `codeowners`, `pr` (description + idempotent draft PR), `text` (secret scrub, mention/closing-keyword neutralising), `routes` (4 POST endpoints under `/api/v1/developer-workflow/projects/{id}/gitflow`: run-branch, commit-draft, reviewers/route, pull-requests). Hostile-input stance: every git call is fixed-argv with global/system config disabled and `core.hooksPath`/`fsmonitor` neutralised on the command line (a test proves a repo's own hook/fsmonitor/sshCommand do not run, and a mutation check confirmed unhardened git *would* run the hook).

## Verification (actual runs)
| Command | Result |
|---|---|
| `section2: pytest tests -q` | 74 passed (baseline 36 + 38 new) |
| new gitflow tests only | 38 passed |
| `ruff check` (new + edited files) | clean |
OpenAPI for the new surface: `openapi-gitflow.json`. No merge/PUT/DELETE/PATCH route exists on the Git surface (asserted via the OpenAPI path set).

## NOT proven / limits
- Fake GitHub only (MockTransport); no live provider, no real pushes, no customer repo touched. Real `requested_reviewers`, draft semantics and branch-protection behaviour are unverified.
- Worktrees are a host-side library with no HTTP surface and are **not** inside S5-managed execution: NEEDS INTEGRATION — S5 must own worktree placement and the commit/push step; this lane never pushes.
- `agent/<runId>` PR→run linkage reuses the existing `record_pr_opened`; it was not exercised against the real agent_runs schema here (best-effort, never fails the open).
- CODEOWNERS parsing follows documented GitHub semantics; edge cases (escaped spaces, `[..]` classes) are not implemented.
- Commit drafts are rule-based; they never claim intent beyond path evidence.

## Setup / rollback
`pip install -e section2[dev]` (venv at `section2/.venv`); no migration, no new tables. Rollback = revert the commit; the legacy section2 routes are untouched apart from the two client fixes.
