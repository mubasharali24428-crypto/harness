# Lane D evidence (release 32, acceptance map 27), session 2, 2026-10-18

## Files
Created: `deploy/observability/alerts.yml`, `docs/runbooks/pilot.md`,
`section1/tests/test_alert_rules_load.py`, `section4/tests/unit/test_pilot_flag_rollback.py`,
`docs/briefs/evidence/s2/acceptance-core-flow.md`, this file.
Edited: `deploy/observability/prometheus.yml` (added `rule_files`; corrected a comment that said `job` carries the
service name, there is one job `harness-backends` and the split is `instance`), `docs/runbooks/alerting.md`,
`docs/runbooks/rollback.md` (pointer to pilot.md). No compose change needed: the `prometheus` service already mounts
`./observability` at `/etc/prometheus`. No source, Makefile, deploy/pilot or matrix edits.

## 1. Alert rules load
- `docker run --rm --entrypoint promtool -v deploy/observability:/cfg prom/prometheus:v2.53.0 check rules /cfg/alerts.yml`
  (image was already local, no pull, no compose): `SUCCESS: 15 rules found`.
- `... check config /etc/prometheus/prometheus.yml` (same mount point as compose): `1 rule files found`, config valid,
  `Checking /etc/prometheus/alerts.yml: SUCCESS: 15 rules found`.
- `section1/tests/test_alert_rules_load.py` (new, 8 tests incl. a promtool test that skips when no local binary) +
  `section1/tests/test_deploy_observability.py` (existing): `26 passed, 1 skipped` (section1 venv). Ruff clean.
  The test checks YAML structure, required keys, severity label, `for` format, balanced `expr`, runbook annotation
  resolving to an existing file and heading anchor, `rule_files` wiring, coverage of the four lane-A counters,
  and that alerting.md names every alert.
- 15 alerts in groups `harness-availability` (service down page, 5xx ratio), `harness-slo` (a-f from alerting.md, plus a
  page-level queue-depth rule that the doc promised), `harness-pilot` (approval denial spike, cancel failure, isolation
  violation page, event delivery drops, duplicates).
- Defect fixed: the documented event-bus-lag rule would fire forever on an idle system (gauge = seconds since newest
  agent_events row). The rule now requires queued claims or runs started in the last 15m.
- Rules d and e (backup, WAL archive) load but cannot fire: no emitter exists in the repo (annotated INERT).

## 2. Flag rollback test
Code lives in section4, so the test is `section4/tests/unit/test_pilot_flag_rollback.py` (no section1 copy).
`cd section4 && ./.venv/bin/python -m pytest tests/unit/test_pilot_flag_rollback.py -q` -> `8 passed`.
Together with `test_collab_backend.py` and `test_collab_denial_audit.py`: `94 passed`. Ruff clean.
Mutation check: with `collab.pilot_allowed` forced to True via a throwaway plugin, 4 of the 8 fail (so the tests bite).

Finding that changes the brief's wording: `HARNESS_PILOT_TEAMS` is an allowlist, and unset/empty means open to EVERY team
(`collab.py:74-77`). Removing or emptying the flag is not a rollback; rollback = non-empty list without the team
(placeholder such as `__rolled_back__`). The tests therefore pin both: rolled back (placeholder or only another team
listed) vs emptied/unset (open).
Proven: run stays readable for the owner team (`GET /runs/{id}` 200, auth guard real, aggregate service stubbed because the
sqlite fixture lacks agent_events); stored rows in the four collab tables unchanged; redirect, handoff, leader claim,
approval request and decision by a non-leader participant, and the leader's decision, all 404 `PILOT_NOT_ENABLED`, zero rows
written, no audit row (existing pin); foreign team always 404 `NOT_FOUND`; re-listing the team restores roster, history and
the pending approval (decidable); `_pilot_gate` is wired only into the 15 `collab_*` handlers.
Limit to know: while rolled back, the owner team also cannot read roster, history or approvals through the API (gate covers reads).

## 3. Pilot runbook
`docs/runbooks/pilot.md`: flag semantics, pre-session checks, operator-present rules (who, when, checks, authority),
7 stop conditions, feedback after each task (manual record, no product tool exists), rollback procedure with the test
reference, evidence before expansion.

## 4. Acceptance map
`docs/briefs/evidence/s2/acceptance-core-flow.md`: 10 rows, each with unit results run fresh (2, 14, 87, 4, 5, 9, 4, 6, 19, 31
passed, 0 failed). Every row except 3 still carries a PENDING-LEAD browser or lane-in-flight leg.

## Open risks
- Lane A counters were not in the code when rules were written (`rg` found none). Cancel rule uses a negative matcher
  (`outcome!~"ok|success|stopped|cancelled|canceled|completed"`); drop/dup rules match `drop.*|fail.*|error|lost` and `dup.*`.
  The lead must reconcile label values with lane A and edit the regexes in `alerts.yml` if they differ. Counters appear only
  once incremented; lane A should pre-create children at 0 if it wants `increase()` to see the first event (the isolation
  rule has an extra clause for the first sample, the others do not).
- No Alertmanager: `severity: page` pages nobody until routing exists.
- `increase()`-based pilot alerts stay silent (not "healthy") until the counters are emitted.
- Alert expressions were parsed by promtool, never evaluated against live data (stack not started).
