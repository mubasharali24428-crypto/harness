# Runbook: Pilot operations (shared runs)

Part 32 of the master prompt (release strategy). Scope: the opt-in shared-run
pilot for named teams. Related: [alerting.md](alerting.md) (what pages),
[rollback.md](rollback.md) (image/schema rollback),
[../briefs/pilot-supervisor-checklist.md](../briefs/pilot-supervisor-checklist.md)
(daily and weekly checks, audit events to read),
[../briefs/multiplayer-pilot-release-plan.md](../briefs/multiplayer-pilot-release-plan.md)
(plan and limits). This file adds the operator-present rules, per-task feedback
and the tested flag rollback; it does not repeat those documents.

## 1. The flag: HARNESS_PILOT_TEAMS

- Comma-separated team ids on the orchestrator (section4). A team in the list can
  use the 15 collaboration routes; any other team gets 404 `PILOT_NOT_ENABLED`
  on all of them, reads included. The UI shows a plain notice and stops polling.
- **Unset, empty or only commas = open to every team.** That is the dev default
  and the opposite of a rollback. Pilot environments must use a non-empty list.
- The flag gates collaboration routes only (join, roster, leader, redirect,
  history, handoff, approvals, staged approvals/apply). It does not stop a team
  from creating or running runs, and the run itself stays readable.
- Code: `collab.pilot_allowed`, `api._pilot_gate`. Changing the value needs an
  orchestrator restart (environment change).

## 2. Before each pilot session

Run the checklist items under "Before admitting a team / each day" in the
supervisor checklist, then confirm:

- [ ] Named operator present (section 3) and reachable for the whole session.
- [ ] Task is low risk and the workspace is a bounded clean checkout
  (`HARNESS_WORKSPACE_SEED`); nothing that touches production credentials.
- [ ] Automatic merge stays off; a human reviews every generated change before it
  is merged (directives 004, 005).
- [ ] `alerts.yml` loaded: `http://localhost:9090/alerts` lists the
  `harness-pilot` group. If the session-2 counters are not emitted yet the pilot
  alerts are silent, so the operator watches the audit log by hand
  (alerting.md section 6).

## 3. Operator-present rules

Directive 008: a human operator is available during early pilot runs.

| Item | Rule |
|---|---|
| Who | One named operator per session, not a participant in the task. A second named person is the backup. The pilot team knows both names and how to reach them. |
| When | From the first Join on a run until the run is terminal and its Stop (if any) is confirmed. Early pilot runs (first two weeks, or until the evidence review in section 6 passes) never run unattended. |
| What they check at start | Allowlist non-empty and lists only the pilot teams; container confinement and isolation on; self-approval unset; workspace seed is the intended checkout. |
| What they check during the run | Audit log for the run: `authz.denied` and `approval.self_denied` repeats, `redirect.scope_changed` / permission changes and their reasons, approvals pending too long, handoffs left pending. Alerts page (`localhost:9090/alerts`). Orchestrator logs if a run looks stuck. |
| What they check at the end | Every Stop shows `run.cancel_confirmed`; if only `run.cancel_requested`, `docker ps`, kill that run's container, verify it is gone, record it. Changed files were reviewed by a human. Workspace is not left holding secrets. |
| Authority | The operator may pause any run, stop any run and remove a team from the allowlist without asking. They do not widen access, disable isolation or switch to paid models to get a run through. |

### Stop the pilot (stop conditions)

Stop affected runs and apply the rollback in section 5 when any of these happens:

1. Any sign of cross-team access, or `HarnessIsolationViolation` fires.
2. Credentials or secrets appear in a workspace, log, event or UI.
3. A Stop cannot be confirmed (`run.cancel_confirmed` missing after the container
   was killed or the container is still alive).
4. Approvals cannot be verified, or an action ran without a matching approval.
5. Safety-critical audit evidence is missing for a run.
6. The same failure repeats on two consecutive tasks (for example stale-digest
   denials, event drops, wrong run shown) without a root cause.
7. Disk pressure or unclear workspace provenance threatens the host.

Pause and recover (do not widen anything) when the leader cannot complete a
handoff or an approval is stuck past its TTL; escalate per the checklist.

## 4. Feedback after each task

Directive 009: collect feedback after each real task, not only at launch.

1. When a run is terminal, the operator asks every participant (leader, others,
   observers) the same short questions, within the same working day:
   - Did the result match what you asked for? (yes / partly / no)
   - Did you always know who was in control and what was waiting on whom?
   - Did any approval, redirect, handoff or stop behave in a way you did not expect?
   - Did you ever see another team's data, or miss an update?
   - What would have made this task faster or safer?
2. The operator writes one record per task in the incident record the checklist
   requires: run id, date, team, participants, task type, outcome, answers,
   incidents (with request ids and error codes), whether Stop was confirmed.
   There is no feedback tool in the product; this is a manual record.
3. Any "no", any unexpected behavior, any isolation or stop doubt becomes an
   incident with an owner before the next task starts.
4. Weekly, attach the records to the `pilot_metrics` report (checklist, weekly).
5. Expansion (new teams or higher-risk tasks) happens only after the records and
   the acceptance evidence under `docs/briefs/evidence/s2/` have been reviewed
   by a human (directive 010). Nothing here expands access automatically.

## 5. Rollback of the collaboration flag

Use when a team must leave the pilot, or when the stop conditions above apply.

1. Tell the team. Stop their active runs (`POST /api/v1/orchestrator/runs/{id}/stop`)
   and confirm `run.cancel_confirmed` for each (kill the per-run container if not).
2. Remove the team from `HARNESS_PILOT_TEAMS`. **Keep the list non-empty.** If it
   would become empty, set an explicit non-matching placeholder such as
   `__rolled_back__`; never unset or empty it (that opens every team).
3. Restart the orchestrator so the new value is read.
4. Verify as a member of that team: `GET /runs/{id}/participants` answers 404
   `PILOT_NOT_ENABLED`; `GET /runs/{id}` still answers 200 for the owning team.
5. Nothing is deleted. Shared-run rows (`run_participants`, `run_collab_events`,
   `run_handoffs`, `high_impact_approvals`) and audit rows stay; adding the team
   back to the list makes them usable again, pending approvals included.
6. Record the change and time in the incident record. Image or schema rollback is
   a separate decision: [rollback.md](rollback.md).

What the rollback does not do: it does not stop runs from being created or running
(operational step 1 above), and while it is in force the owner team cannot read
roster, history or approvals through the API either (the gate covers reads); the
run status and the stored data remain.

Tested by `section4/tests/unit/test_pilot_flag_rollback.py` (run with the
section4 venv: `cd section4 && ./.venv/bin/python -m pytest tests/unit/test_pilot_flag_rollback.py -q`).
It proves: with the team removed from the list the owner team still reads the
run, every collaboration mutation (redirect, handoff, leader claim, approval
request and decision, by a non-leader participant and by the leader) is refused
with 404 `PILOT_NOT_ENABLED` and writes no rows; a foreign team gets 404
`NOT_FOUND` whatever the flag says; unset/empty is open, not a rollback; adding
the team back restores roster, history and the pending approval. Related pins:
`test_pilot_allowlist_gates_every_collab_route` and
`test_pilot_gate_refusal_is_not_audited_today` in
`section4/tests/unit/test_collab_backend.py` and `test_collab_denial_audit.py`
(the refusal leaves no audit row; the operator sees the config, not the attempts).

## 6. Evidence before expanding

Directive 010 is a human decision. Inputs: dated `pilot_metrics` reports, the
per-task feedback records, incident log, `docs/briefs/evidence/s2/`
(`acceptance-core-flow.md`, lane evidence files), alert history from
`http://localhost:9090/alerts`. Fewer than five valid observations per side is
insufficient comparison data, not a failed team (checklist).
