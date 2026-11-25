# Task 44 — CI `changes` job (cloud, 2026-11-25)

**Status: fix pushed; green run NOT yet confirmed** (updated below once the PR #27 run finishes).

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
- Real proof is the GitHub run on the pushed commit (see below).
