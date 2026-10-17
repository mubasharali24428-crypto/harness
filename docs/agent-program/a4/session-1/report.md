# A4-S1 — Editable code surface and granular review

Baseline 31bf407 (worktree `codex/harness-a4`). Owned paths only: `integrations/editors/**`, `section7/**` (additions), `docs/agent-program/a4/**`.

## What existed vs. what this session did
`integrations/editors/service` already held an **untracked, untested, unpackaged** ~2.7k-line draft of the developer-workflow service. It was audited rather than trusted: packaged (`pyproject.toml`), covered with 40 pytest cases at the HTTP seam, and fixed where the tests proved defects. The UI half (`ReviewPanel`, model, API client) and a real-browser suite are new.

## Defects found by audit and fixed (each has a regression test)
1. `.git/**` writable via the code surface → `.git/config` `core.fsmonitor` / hooks execute on the host's own `git status`. Now rejected (case-insensitive, any depth).
2. `repoRef` could clone **any local path on the host** → now confined to `DEVWORKFLOW_REPO_ROOTS`; empty = fail closed; remote/`git@`/`-opt` refs refused.
3. Branch name was passed unvalidated to `git checkout -B` (option injection) → strict name regex.
4. forbidden_paths compared case-sensitively (`Secrets/` bypassed `secrets`) → casefolded.
5. Control-char check was off by one (0x1F, DEL accepted).
6. No role enforcement: viewer/guest could write, create/decide/apply change sets, sync and verify → read-only roles denied (HAP `denied`).
7. Same-file edit merge hard-coded `accepted={0,1}`, silently dropping a 3rd+ hunk, and context overlap raised a 500 → replaced with a line-level disjoint merge that rejects true overlaps (422).
8. Creation proposals (new files) were always reported `stale`, so multi-file apply with a new file could never succeed.
9. Apply was not atomic across files → snapshot + rollback; a mid-apply failure leaves the workspace untouched and the change set open.
10. `git status --porcelain` listed new directories as `dir/`, so new files in new directories were never synced/fingerprinted → `-z -uall`.
11. Non-UTF-8 files, bad line numbers and symlink escapes returned 500 → clean 4xx.
12. Wire used snake_case (`cs_id`, `team_id`…) against the camelCase convention → response layer camelizes (`changeSetId`).
13. `app.py` created a data dir at import time → factory `app_from_env` (`uvicorn harness_devworkflow.app:app_from_env --factory`).
14. UI (found from the screenshot): a stale-workspace banner showed after a *successful* apply; hidden label rendered visibly → fixed + model test.
Also: mutating ops refused on closed sessions.

## Verification (actual runs, 2026-10-17)
| Command | Result |
|---|---|
| `service/.venv/bin/python -m pytest tests -q` | 40 passed |
| `ruff check` / `ruff format --check` (service) | clean |
| `section7: npx vitest run` | 524 passed (baseline 507 + 17 new) |
| `section7: npx tsc --noEmit` | clean |
| `section7: npx playwright test -c playwright.review.config.ts` | 4 passed (real Chromium ↔ real service) |
Screenshots: `evidence/before.png`, `evidence/after.png`. OpenAPI: `openapi.json` (19 paths).

## Local completion vs. what is NOT proven
- Identity/sandbox are faked **at the HTTP seam only**; no live S1/S5 run. NEEDS INTEGRATION: combined acceptance against real services.
- S5 enforcement of synced edits relies on its apply-edit API; denial is surfaced (`sync.failed`), not bypassed.
- State is lane-owned SQLite (not shared Postgres): **NEEDS INTEGRATION** with the A1 migration owner for durable multi-instance state.
- Principal tokens are cached 30 s (revocation lag).
- Full IDE (F004), selection action menu (F008), comment UI (F010) are not built; explain/refactor need an A2 code-action endpoint (proposal in the action response).

## Setup / rollback
Run: `DEVWORKFLOW_IDENTITY_BASE=… DEVWORKFLOW_SANDBOX_BASE=… DEVWORKFLOW_REPO_ROOTS=/abs/repos DEVWORKFLOW_DATA_DIR=… uvicorn harness_devworkflow.app:app_from_env --factory`. Unset bases = capability reported unavailable (fail closed). Rollback: stop the service and delete its data dir; nothing else is touched.

## Release verdict
Not releasable alone. Local slice is real and tested; production rollout stays disabled until combined acceptance.
