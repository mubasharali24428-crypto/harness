# Task 44 — CI `changes` job (cloud, 2026-11-25)

**Status: PASS — `changes` job is green on PR #27** (run 45613454005 at 356d7fe, which contains the fix).

## Root cause (from the failing run's log, run 45612683018 / job 139200010438)
Step `resolve the lists for the job outputs` (paths-filter itself succeeded) died with
`json.decoder.JSONDecodeError: Expecting value: line 1 column 2`. The step inlined
`${{ steps.filter.outputs.changes }}` — a JSON array full of double quotes — into a
double-quoted shell argument, so bash received `[section1,...]` with the quotes eaten
(`"[\"section1\",…]"` rendered as `"["section1",…]"`). It is a quoting bug, not a
`fetch-depth` problem (paths-filter on `pull_request` reads the file list from the API).

## Fix
`.github/workflows/ci.yml`: pass the event name and the filter/forced outputs through
`env:` and read them as `$FILTER_CHANGES` etc.; default an empty output to `[]` so
`fromJSON()` in downstream `if:` never sees an empty string. No filter definitions changed.

## Verification here
- YAML parses; the shell logic was simulated with `["section1","e2e"]` -> `sections=["section1"]` and empty input -> `[]`.
- Real proof: https://github.com/mubasharali24428-crypto/harness/actions/runs/45613454005 — job "which jobs does this change actually touch?" **success**; all 9 pytest legs, e2e compose journey, migrations-on-PG, mcp, hx, static gates, vitest+tsc, security scan **success**. The only red job is `readiness drift (manifest vs HEAD)` (0076 vs 0081), expected until the owner redeploys (Task 45b).
- Before the fix (run 45612683018) the same job failed in the resolve step; every earlier run on the branch failed the same way, which also left the dependent pytest legs skipped.
