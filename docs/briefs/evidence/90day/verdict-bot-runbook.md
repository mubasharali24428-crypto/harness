# Verdict bot runbook — P-15 / I-02 MVP (2026-11-21)

What shipped: `.github/workflows/verdict-bot.yml` (working sample, dispatch-
only) + `scripts/ops/test_verdict_bot_workflow.py` (6 negative shape tests).
The comment builder itself was already live: `verdict_comment.py` emits plan
approval (who/when), sandbox boundary, tests, cost (actual vs estimate),
risk flags — scrubbed against a secrets list, idempotent via `verdict_marker`,
size-capped. `verdict_post.py` is the CLI that reads a run's RECORDED facts
and posts through the GitHub service (section2 refuses without the project's
write opt-in).

## 1. Configure (once per repo)

1. Repository secrets: `HARNESS_DATABASE_URL`, `HARNESS_GITHUB_URL`,
   `HARNESS_REVIEW_URL`, `HARNESS_API_TOKEN` (the harness session token of a
   viewer-role service account — the bot READS recorded facts; it never
   merges).
2. The target project must have GitHub write opt-in ON in the harness
   (Settings; the GitHub service refuses otherwise).

## 2. Run (manual until per-repo triggers are approved)

Actions → "verdict bot" → Run workflow: `run_id`, `pr_number`,
`confirm_post=false` (default) → the job prints the comment and posts nothing.
Inspect it, then re-run with `confirm_post=true` to post. The comment is
ADVISORY; a human merges (invariant, unchanged).

## 3. Negative guarantees (tested)

- `secrets.*` appear only as env-mapping values — never expanded into shell
  (test_every_secrets_reference_is_an_env_mapping_value,
  test_run_block_never_expands_secrets).
- Dry-run default + explicit confirmation language
  (test_dry_run_is_the_default_and_stated).
- Least privilege: `contents: read` only; commenting rides the harness API
  token, not the Actions token (test_least_privilege_permissions).
- Comment body scrubbing: masked tokens/quoted secrets, inert free text
  (section4 test_verdict_comment.py).

## 4. Partner rollout (P-15 acceptance path)

Copy the workflow file into the partner repo, set the four secrets, open a PR
from an agent run, dispatch the bot on that PR (dry-run → confirm). The
acceptance "bot comment on a real partner PR" is met on the first confirm;
the no-secrets negative test rides in the same commit.
