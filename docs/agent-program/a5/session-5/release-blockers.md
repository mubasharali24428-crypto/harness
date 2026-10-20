# A5-S5 release blockers — truthful status

Nothing in this repo has been deployed, auto-merged, or shown to a customer.
A5-S5 completed its lane locally; the items below are, in order, what still
stands between this slice and any real rollout. Each blocker states its
verification owner and the exact pass condition.

## Hard release blockers

1. **Combined-lane assembly has not run.** S5 wired and tested only its own
   production assembly. Chat approvals, CI intake and schedules call the
   Section 4 orchestrator through its real approve/propose routes, but a
   combined deployment (orchestrator + ops + auth assembly + telemetry
   archive in one environment) has never been executed. Pass condition:
   `scripts/agent-program/a5/integration_check.py` reports all lanes present
   AND a Section-level combined run passes.
2. **No deployment.** Rollout (process manager, TLS, cron entries per
   `runbook.md`) is operator work outside this program. Pass condition:
   staging environment following `runbook.md` §4 with the §3 smoke green.
3. **VR031 — measurable developer value.** Instruments (telemetry metrics,
   usage APIs) ship and are tested against fixtures, but **no pilot has run**:
   there are no measured ROI/productivity numbers and none are claimed.
   Pass condition: ≥ 1 real pilot cohort with pre-registered metrics
   (`docs/agent-program/a5/session-4/pilot-measurement.md`), reported with
   honest sample sizes — or no ROI claim at all.
4. **VR036/VR037/VR038/VR042/VR044/VR050 — market/buyer/procurement/pricing/
   willingness-to-pay/distribution.** Not code-closable. S5's contribution is
   the evidence machinery (usage/evidence exports) that a future validation
   conversation would stand on. **No customer acceptance is implied anywhere
   in these artifacts.** Pass condition: external validation with named
   buyers — not performed here.
5. **Live provider integration unvalidated.** CI webhooks (GitHub/GitLab/
   CircleCI), Slack interactivity and outbound customer webhooks were
   exercised against real HTTP transports locally, but never against a real
   provider/customer endpoint. Pass condition: one end-to-end event per
   provider in staging, with signature verification on the provider side.

## External checklists (operator-owned)

### Billing / finance reconciliation
- [ ] Provider cost ledger connected to the real archive (no fixtures).
- [ ] `usage_snapshot` totals reconciled against the provider invoice for one
      full billing period; overhead bucket semantics confirmed with finance.
- [ ] Webhook consumers (finance/security) registered with owned secrets.

### IdP / SSO
- [ ] `get_principal_dep` wired to the real IdP; role mapping reviewed.
- [ ] Cross-team 403 (`TEAM_FORBIDDEN`) re-verified with real tokens.
- [ ] Break-glass admin path documented and access-reviewed.

### Backup / DR drill
- [ ] Scheduled `sqlite3 .backup` for `OPS_DB` and `OPS_ARCHIVE_DB`.
- [ ] One restore drill per quarter; evidence export verified after restore.

### Model-failure drill
- [ ] Orchestrator/model outage: confirm CI/chat/schedule proposals fail
      closed (`PLAN_GATE_REJECTED`/502, no state change, claims released).
- [ ] Plan-approval requirement re-verified: no scheduled or CI work can
      start without an explicit approval.

## Known non-blocking limits (pilot-grade accepted)
- SQLite single-node storage; no queue, no HA (VR014 hardening ships with
  Section 10 assembly; VR020: no pen-test/SOC2).
- Support surface today = package READMEs + `runbook.md` (VR040 open).
- Evidence/usage numbers inherit telemetry coverage: missing cost rows
  understate, never overstate.
