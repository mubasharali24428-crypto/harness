# W4–W20 blocked-item work orders — what was done vs what a human must do

Follow-on to [gap-reconciliation-2026-10-31](gap-reconciliation-2026-10-31.md).
The operator asked to "do" the ten items the reconciliation marked blocked/open.
Three were genuinely completable here and are now **closed with evidence**;
the rest are hard human/external gates or large unbuilt features. For those this
page is a decision-ready work order — the work an agent *can* do (scope the
brief, prepare the checklist, build the isolated slice) plus the one action only
a person or an external party can take. Nothing here invents a sign-off, a run
on a machine we do not have, or a review that did not happen.

## Closed this session (build + proof, committed)

| Item | What landed | Evidence |
|---|---|---|
| **W13** BYO per-run tool-call accounting | `runner_api.post_job_events` now bounds a run's tool calls against its Part 44 `maxToolCalls` and refuses an over-cap batch (`409 TOOL_CALL_LIMIT`) before any write; no limits row = no cap | `section1/tests/test_runner_exec.py::test_run_tool_call_limit_bounds_byo_events` (positive + negative control); commit `4b22ede` |
| **W13** BYO failure notifications | Root cause found: the notification bus-tap was wired only on the orchestrator app, but the runner router lives on the **identity** app, so a BYO `engine_failed` blocked a run silently. `app_identity` now attaches `make_event_notifier` to its bus (no second worker → no double-send) | `section1/tests/test_deploy_observability.py::test_identity_assembly_taps_run_terminal_events_for_notifications`; commit `4b22ede` |
| **W19** event-bus delivery across Postgres NOTIFY replicas | New drill: a second identity replica on the shared DB receives, purely via NOTIFY, all 40 events its own process never inserted | `docs/briefs/evidence/w19/replica-notify-*.json` PASS 5/5; `scripts/e2e/w19_replica_notify_drill.py`; commit `4b22ede` |
| **W19** indexing admission across replicas — **enforced** | Replaced the per-process `_active_projects` guard with a durable reservation (migration `0072 project_index_reservations`) + one atomic lease upsert; dead-owner leases reclaimed, owner-checked release | two-instance-on-one-DB tests (`refuse` / `reclaim-expired` / `release`), and live PG-18 proof (A `INSERT 0 1`, B refused `INSERT 0 0`, owner stayed A); commit `cb88801` |

These do not need a human; they are done and green (section1 78 runner/notify/obs,
section4 byo 10, section5 byo 15, section11 59 — all passing with live PG where
relevant). They also do not touch the concurrent workflow-contract lane's files.

## Open — hard human / external gates (cannot be produced from this environment)

Each row states the exact action, the owner, and what is already prepared so the
human only decides. An agent completing these would be fabricating evidence.

- **W4 third-party MCP server review.** Needs a *real* external MCP server
  reachable over the network and an operator to review it; the recorded evidence
  is a local stub. Action: pick a vendor MCP server, run
  `scripts/e2e/w4_egress_network_drill.py`-style against it with the allow-list,
  and have the integrations owner sign the review. Not runnable offline; not
  done.
- **W6–W7 Hermes rights & distribution, toolchain prereqs, H3 `.hms` LSP.** Legal
  / product decisions + a language-server that does not exist yet. The W7 Python
  LSP proof still does **not** establish `.hms` support — say so, do not upgrade
  the claim. Action: counsel clears rights; a runtime owner scopes H3 `.hms`.
- **W7 full task in a real VS Code window.** Packaging + activation smoke passed;
  the missing leg is a human typing an end-to-end task in a GUI window. This
  environment is headless — there is no display to drive. Action: a person runs
  the `.vsix` in a real VS Code and records the task. Runbook: the vsix-smoke
  note in `docs/briefs/evidence/w7/`.
- **W13 model-driven agent loop on a real remote customer host.** The execution
  leg runs approved *test commands* on the services' own host. A remote customer
  machine is not available here, and a loop that edits code and opens a PR is a
  feature, not a drill. Action: provision one external test host + build the
  agent loop; until then the roadmap gate stays unmarked (see W13 "Carry-over").
- **W15 scheduled restore actually fires.** The trigger is the external QoderWake
  automation `tr_a4315e8e62b81c7819aca10d` (next firing 2026-11-01T12:00Z); its
  blocker was *account credits*, which cannot be inspected or topped-up from the
  repo. The manual restore drill passes. Action: after 2026-11-01, confirm the
  automation's run record and dated evidence; if credits were the cause, verify
  the account funded. Not observable from here today.
- **W16 counsel sign-off + external security review.** Placeholder TERMS/PRIVACY
  stand. A review packet exists under `docs/legal/`. Action: counsel signs; a
  purchased third-party pentest runs. Both are paid external services; not
  producible in-repo.
- **W17 SLO owners, on-call, alert delivery endpoint, target-deployment load.**
  Verified live this session: Prometheus → Alertmanager routing works (2 alerts
  arriving), but the only receiver `web.hook → http://127.0.0.1:5001/` goes to
  nothing — delivery to a named human is impossible until an owner supplies a
  real endpoint. The eligible-request counter and event-freshness histogram are
  still unimplemented. Load on the *intended* deployment is unmeasured (laptop
  dry-engine exceeded 250 ms). Actions (owner decisions): name the indicator
  owner + on-call; pick the Alertmanager destination; approve a provider-load
  budget on the target deployment; adopt/revise the three proposed SLOs.
- **W18–W19 independent release-candidate review of the importer; W20 release
  decision record.** An *independent* reviewer and an *authorized* decision +
  date cannot be manufactured by the builder. Credential filtering is still
  heuristic and concurrent writes unprotected. Action: assign a reviewer who did
  not write the importer; a release approver fills the record.

## Open — large unbuilt features (scoped, not started here)

These are implementation projects with real acceptance work, isolated from the
concurrent lane but each bigger than a single safe edit; not "verification gaps."

- **W13 re-dispatch after a lost lease.** Requires dropping/altering the
  `runner_jobs` primary key (`run_id` is the PK, so a second offer of the same
  run needs a new key, e.g. `(run_id, attempt)`), a human-gated re-queue path,
  and a live drill that never double-executes. Design drafted; the schema change
  + tests are the work. Deliberately not half-shipped this session.
- **W13 mid-command human cancellation in a live drill.** `cancel` on the job
  heartbeat + the runner killing the command are unit-tested (fake server, real
  gate). The missing leg is a live run stopped mid long-command on the deployed
  stack; the deployed stack does not yet carry these W13 code changes, so the
  drill would need a rebuild first.
- **W13 transport hardening (mTLS/request signing, per-runner rate limits).**
  The identity rate limiter exists but is not wired onto the runner routes;
  request signing is on top of the bearer token. Additive but security-relevant
  — needs a threat-model pass, not a quick patch.
- **W13 runner management UI (registration/revocation/rotation/lease status).**
  API/SDK only today; a real Next.js admin page in Section 7 is the work. Not
  started: it touches `section7`, where the concurrent lane currently has an
  unresolved conflict, so landing a page here would collide.

## Method note

HEAD was actively advanced by a concurrent workflow-contract lane during this
session (it landed the previously-held W17 terminal-publish retry in `332d273`).
All three of this session's commits use pathspec scoping so none of the lane's
in-progress files or its open `section7/app/login/page.tsx` conflict were
touched or swept into my commits.
