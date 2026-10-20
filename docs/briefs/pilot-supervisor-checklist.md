# Pilot supervisor checklist

Use for the proposed supervised two-week pilot with 1–2 teams. This checklist is a manual operating procedure, not an automated monitor. Review the separate security findings before approving launch. [S1]

## Before admitting a team / each day

- [ ] Confirm a **nonempty** `HARNESS_PILOT_TEAMS` allowlist, ownership enforcement on, container confinement on, isolation on, and self-approval unset. Use the agreed free model; do not switch to paid models. Ensure the designated supervisor and incident record are available. [S2]
- [ ] Confirm the operator-prepared `HARNESS_WORKSPACE_SEED` is the intended clean repository checkout and refresh it before the day's new runs. New runs copy it without `.git`; existing runs keep their own copy. Unset means empty workspace. Provisioning is now present in the shared checkout; this checklist does not claim it was retested here. [S3]
- [ ] Review audit events: `authz.denied`, `approval.self_denied`, `participant.joined`, `leader.assigned`, `redirect.recorded`, `redirect.scope_changed`, `handoff.requested/accepted/rejected`, `approval.requested/approved/rejected/invalidated/expired/claimed/completed`, `run.started`, `run.stopped`, `run.cancel_requested`, `run.cancel_confirmed`. Check actor/run, unusual scope changes, repeated denials, failed outcomes and approvals stuck executing. Audit absence is not proof that no denied action occurred. [S4]
- [ ] Review stuck runs and pending approvals. Staged approvals bind full old/new file bytes, including binary content and content beyond the displayed diff; apply checks and lands the same withheld snapshot. The leader decides; a reachable leader may offer handoff. The recipient must accept. Default approval TTL is 3,600 seconds; do not assume expiry itself causes automatic recovery. If the leader is absent, a team admin can take over by claiming leadership on the run (`POST .../leader`); this demotes the old leader, cancels any pending handoff, and writes a `leader.takeover` audit row. Engineers still need handoff. [S5]
- [ ] For `stopping` or `run.cancel_requested` without `run.cancel_confirmed`, have the operator inspect `docker ps`, identify and kill only the affected run's container, then verify it is gone and record the incident. Do not treat the UI state or `run.stopped` alone as cancellation confirmation. [S6]
- [ ] Check session/workspace disk growth. Run retention dry-run, inspect candidate run IDs and preserve incident evidence, then authorize the apply pass. No deletion is performed by this checklist. [S7]

With the intended database and Section 4 environment configured:

```sh
HARNESS_DATABASE_URL=... python -m harness_orchestrator.retention --days 30
HARNESS_DATABASE_URL=... python -m harness_orchestrator.retention --days 30 --apply
```

Dry-run is default. Eligibility uses old completion/failure/PR events or a stop record, no newer events, and no pending/approved/executing approval. Apply removes baseline/staged/session directories, blanks terminal approval diffs and removes collaboration events. It preserves audit rows and approval metadata. Run outputs are retained unless `--include-workspace` is explicitly supplied. Review this distinction before deletion; audit preservation does not mean all collaboration text is retained. `HARNESS_RETENTION_DAYS` controls the default age. [S7]

## Weekly and at pilot end

```sh
HARNESS_DATABASE_URL=... python -m harness_orchestrator.pilot_metrics TEAM_ID
HARNESS_DATABASE_URL=... python -m harness_orchestrator.pilot_metrics TEAM_ID --baseline baseline.json
```

Save dated reports; review run counts, elapsed times, redirects, approval states/latency and cancellation confirmations. Use the metrics tool or runs index rather than `agent_runs.state`. Review actual baseline recording sheets; at least five valid durations per side are needed for `claimable`, and even then the comparison is descriptive only. Accepted counts distinct finished runs with a `review.approved` audit row, not independently confirmed human task decisions. Refused collaboration requests (foreign run, role, state) each leave an `authz.denied` audit row; review them for probing. Without date flags reports cover all team history. For a weekly cohort, add `--since YYYY-MM-DD --until YYYY-MM-DD`: UTC creation time, since inclusive and until exclusive. All activity for selected runs is included, even outside those dates; this is not an activity-only weekly report. [S8]

## Escalation and rollback

Support actions from the release plan: wrong-team 404 → verify membership, do not widen access; `RUN_NOT_PERSISTED` → restart the run; `SELF_APPROVAL` → another eligible leader decides. Capture the run ID, time, action and redacted error in the incident record. [S9]

The release plan's rollback steps, copied verbatim:

1. Turn off shared-run features for the team: remove it from `HARNESS_PILOT_TEAMS` and restart the orchestrator (the 15 collab routes then 404 for it). The list must stay **non-empty**: an empty or unset list opens collaboration to every team. This gate does **not** stop a team from starting or running runs; there is no code-level admission stop. Stopping new runs is operational: tell the team, stop their active runs (step 2), and if needed stop the orchestrator.
2. Stop active runs via `POST /runs/{id}/stop`; confirm `run.cancel_confirmed` in the audit log (a `stopping` result with no confirmation means the container is still alive: `docker ps` and kill the per-run container).
3. Deploy the previous orchestrator/UI build. The collaboration tables (migration 0035) are additive; rollback = leave them unused. Dropping them (`run_participants`, `run_collab_events`, `run_handoffs`, `high_impact_approvals`) is a last resort and loses collaboration history.
4. Audit rows are never purged by any job; they remain after rollback.

**Code qualification to step 1:** an empty/unset allowlist permits every team. When excluding the last team, keep an explicitly nonmatching, nonempty value approved by the operator. Also, the gate covers collaboration routes, not general run creation/start; removing a team alone does not enforce a full admission stop. The operator must separately halt admission operationally. The API stop path has the `/api/v1/orchestrator` prefix. Step 4's audit preservation was verified for this retention job, not every possible external job. [S10]

## Go / pause / finish decision (recommended supervisor criteria)

- [ ] **Go only if** enrollment, confinement, ownership, free-model configuration, supervisor coverage and baseline collection are ready; review unresolved security findings and evidence gaps explicitly.
- [ ] **Pause and escalate** on suspected cross-team access, exposed credentials, unexpected staged content, an unconfirmed stop, unverifiable approvals or missing safety-critical audit evidence. Stop affected runs and use the rollback procedure when necessary.
- [ ] **Pause for recovery** if the leader cannot complete handoff, storage pressure threatens operation, or workspace provenance is unclear. Do not improvise broader access or disable isolation.
- [ ] **Finish the two-week observation** with dated metrics, baseline sheets, incidents, unresolved limitations and a human decision. Fewer than five valid observations on either side means insufficient comparison data, not a failed team. No automatic production rollout or ROI claim follows.

## Claim/source appendix (checked 2026-10-18)

| Claim | Source checked | Result |
|---|---|---|
| S1 scope and manual supervision | `docs/briefs/multiplayer-pilot-release-plan.md` §§1,4; go/pause criteria are recommendations  `docs/briefs/multiplayer-pilot-release-plan.md:5` | Confirmed by source; operational recommendations are manual. |
| S2 flags and cost policy | release plan §2; `section4/src/harness_orchestrator/collab.py` pilot_allowed/_self_approval_allowed; `api.py` _pilot_gate; repository AGENTS.md and task's free-model restriction  `section4/src/harness_orchestrator/collab.py:71` | Confirmed by source; operational recommendations are manual. |
| S3 seed behavior | release plan §2; `section4/src/harness_orchestrator/engine.py` provision_workspace; `service.py` _ensure_baseline  `section4/src/harness_orchestrator/engine.py:129` | Confirmed by source; operational recommendations are manual. |
| S4 event names and audit limits | `section4/src/harness_orchestrator/collab.py` _audit and callers; `authz.py` lifecycle constants; `api.py` stop_agent/_authz_dep; `section1/src/harness_identity/audit.py` APPEND_ONLY_ACTIONS  `section4/src/harness_orchestrator/collab.py:110` | Confirmed by source; operational recommendations are manual. |
| S5 leader/TTL/recovery limits | `collab.py` _approval_ttl/claim_leader/request_handoff/decide_handoff/decide_approval; release plan §4  `section4/src/harness_orchestrator/collab.py:61; section4/src/harness_orchestrator/staging.py:109,178,257` | Confirmed by source; operational recommendations are manual. |
| S6 stop confirmation | `api.py` stop_agent; `service.py` engine_quiesced; `engine.py` cancel; release plan §3–4  `section4/src/harness_orchestrator/api.py:670` | Confirmed by source; operational recommendations are manual. |
| S7 retention command, roots, eligibility, data preserved/deleted | `section4/src/harness_orchestrator/retention.py` purge/_rm/main; release plan retention section  `section4/src/harness_orchestrator/retention.py:56,113` | Confirmed by source; operational recommendations are manual. |
| S8 metrics schema/time/counts/baseline/creation window | `section4/src/harness_orchestrator/pilot_metrics.py` collect/compare/main; `runs_index.py` _FINISHED_EVENT_TYPES; release plan metrics section  `section4/src/harness_orchestrator/pilot_metrics.py:43,81,145` | Confirmed by source; operational recommendations are manual. |
| S9 errors and support | release plan §4; `api.py` _persist_run/_authz_dep; `collab.py` decide_approval; incident fields are recommended manual practice  `section4/src/harness_orchestrator/api.py:833; section4/src/harness_orchestrator/collab.py:586` | Confirmed by source; operational recommendations are manual. |
| S10 verbatim rollback plus qualifications | release plan §3; `collab.py` pilot_allowed; `api.py` router prefix, start_execution/stop_agent and collaboration gates; migration `section1/migrations/0035_run_collaboration.sql`; `retention.py` deletion statements  `docs/briefs/multiplayer-pilot-release-plan.md:26; section4/src/harness_orchestrator/api.py:618,805` | Confirmed by source; operational recommendations are manual. |

No rollback, retention deletion, external notification, deployment or pilot admission was executed in preparing this document.
