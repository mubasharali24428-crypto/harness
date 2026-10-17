# A4-S3 — Editor, desktop and issue-intake surfaces

Baseline 31bf407, branch `codex/harness-a4`. Nothing pushed.

## Built
- `integrations/editors/vscode` (F001), `neovim` (F003), `jetbrains` (F002, HTTP files only), `apps/desktop` (F006).
- `section2/src/harness_github/intake/` (F037 GitHub, F038 GitLab) wired into `app.py`; `config.intake_db_path`; `GitHubClient.create_issue_comment`.

## Defects found by tests and fixed
- Desktop deep link `harness://review/../admin` was collapsed to `/review/admin` by URL parsing; now a strict raw-string regex.
- Neovim `sign_in` kept a rejected token; now verifies first.
- Intake stored `fixes #N` auto-close text un-neutralised; now neutralised at intake.
- Removed an unused constant (`GITLAB_MIN_ACCESS`).

## Results (see `evidence/`)
section2 89 pass (15 new intake); editor service 43; VS Code 22 + tsc + .vsix; desktop 11 unit + 5 Electron; ruff clean on touched files. Mutations: CSP removal and GitHub-signature bypass are each caught by a test.

## Honest limits
- VS Code, Neovim, JetBrains hosts are not installed; F001/F003 are unproven in a host, F002 has no plugin.
- Desktop is unsigned, mac arm64 only.
- Intake runs against fake GitHub/GitLab only. Approval does not start a run (NEEDS INTEGRATION with A2). Durable store should move to the shared Postgres schema via A1 (NEEDS INTEGRATION).
- GitLab comment triggers lack an access-level check; they only queue for approval.
