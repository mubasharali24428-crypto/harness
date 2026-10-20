# A5-S5 report — evidence, alerts, scheduling and operational acceptance

Final session of Agent A5. Shipped `packages/ops` (`harness-ops`): a
six-feature operations surface (F118–F120, F128–F130) that reuses the A5-S2
workflow connectors (signature schemes, idempotency, plan gate) and the A5-S4
telemetry archive. **Everything proposes, nothing executes**: CI failures,
chat approvals and schedule occurrences all produce plan-approval requests;
no route in this package can start a run. 77 new tests, all green; a fresh
six-wheel distribution (now including `harness_ops`) builds and passes a
no-editable-install smoke journey; 32 golden artifacts reproducibly captured
against real in-process HTTP transports.

## What shipped

### 1. Evidence export (F118) — `harness_ops.evidence`
Deterministic redacted audit packages: runs + accepted changes + incidents +
other outcomes + full timeline, scrubbed via the same `scrub_text` used by
telemetry (`[EMAIL]`/`[REDACTED]`), with per-run `retentionUntil`.
`verify_evidence_zip` recomputes a manifest and fails with explicit codes
(`EVIDENCE_TAMPERED`, `EVIDENCE_INCOMPLETE`, `EVIDENCE_CORRUPT`). Org export:
**runs 8 / changes 3 / incidents 4 / otherOutcomes 1 / timelineEvents 42,
accountedFor=true**; team-a member scope: 6/2/3/1/31. Goldens:
`evidence_export_org.zip`, `evidence_verify.json`, `evidence_team_a_counts.json`.

### 2. Alerts (F119) — `harness_ops.alerts`
Four rules with dedupe keys and an `opened → reminder → resolved` lifecycle:
`stalled_run` (default 12h — deliberately below the 24h abandonment window so
it is reachable), `blocked_run`, `expensive_run` (> $5.00), `claimed_but_
rejected` (the claim/reality gap). Evaluation is idempotent: round 1 opens
4 alerts and delivers 4 webhooks; round 2 with unchanged state returns
`suppressed=4, delivered=0`. Member lists are team-scoped (team-b sees only
`expensive_run:run-b2`). Goldens: `alerts_evaluate_round{1,2}.json`,
`alerts_list_{org,member_team_a,member_team_b}.json`, `webhook_delivery.json`.

### 3. Usage APIs and webhooks (F120) — `harness_ops.usage`
`GET /api/v1/ops/usage` returns the frozen definitions verbatim alongside:
tasks 8, acceptedChanges 3, claimedCompletions 4, totalCostUsd 24.7000,
costPerTask 3.0875, costPerClaimedCompletion 4.2750, costPerAcceptedChange
4.3667, reworkRate 0.4286 (float), abandonmentRate 0.125; `?team=` and
`?since=` windows supported (Oct 14+ → 14.0000 / tasks 6). Outbound webhooks
are HMAC-signed (`sha256=…` over `timestamp.body`), retried ≤3 attempts with
backoff `(0, 2, 5)s` on 5xx/429/network, and idempotent by `deliveryId`
(receiver dedupes; delivery history keeps one row per delivery). Goldens:
`usage_{org,team_a,forbidden,unauthenticated}.json`, `webhook_delivery_manual.json`.

### 4. CI provider integrations (F128) — `harness_ops.ci`
Provider-specific verification — GitHub `X-Hub-Signature-256`, GitLab
`X-Gitlab-Token` (constant-time), CircleCI HMAC — normalized failure events
into **proposed** plans (`awaitingApproval: true`, never started). Duplicate
deliveries dedupe by provider delivery id (`status: duplicate`); per-provider
rate limiting returns 429 `RATE_LIMITED`; unknown/unconfigured providers 404
`PROVIDER_UNKNOWN`. Goldens: `ci_github_{proposed,duplicate,signature_
invalid}.json`, `ci_gitlab_{proposed,token_invalid}.json`.

### 5. Chat approvals (F129) — `harness_ops.chat`
Slack interactivity endpoint with the v0 scheme signature check, then a
one-shot approval token (TTL 900s, bound to actor+plan+action, single use):
approve calls the **real** orchestrator `POST /plans/{id}/approve` and
returns its `runId`; replaying the same action returns `duplicate: true` with
the original outcome (no second approve); expiry → `TOKEN_EXPIRED`; wrong
role → `ROLE_FORBIDDEN`; reject records a rejection without touching the
orchestrator (no verified reject route — stated in the outcome text). Goldens:
`chat_{approve,replay_duplicate,reject,inspect,expired,role_forbidden}.json`,
`orchestrator_state.json`.

### 6. Schedules (F130) — `harness_ops.schedule`
IANA-zone hourly/daily/weekly schedules with DST `fold=0` semantics (spring
forward fires at the same UTC instant, fall back takes the FIRST occurrence;
asserted in goldens for both transitions), misfire policy `skip` vs `run_once`
(exactly one catch-up occurrence, flagged `catchup: true`), and propose-only
execution through the plan gate. Run claiming is `INSERT OR IGNORE` with a
5-minute stale-claim takeover, so concurrent sweeps cannot double-propose and
a crash self-heals; a failed proposal releases the claim for retry. Goldens:
`schedules_{create,trigger_empty,trigger_proposed,list}.json`,
`schedule_dst_grid.json`.

Also shipped per the S5 acceptance text: `runbook.md` (upgrade/rollback/
incident/support), `release-blockers.md` (truthful blockers + external
billing/IdP/backup/model-drill checklists) and
`scripts/agent-program/a5/integration_check.py` (inspection-only lane
evaluator; reports missing lanes as `absent`, exits 1 only on inconsistency).

## Verification (log in `verification.log`)

- `pytest -q` in `packages/ops`: **77 passed** (alerts 6, app 9, chat 12,
  ci 11, evidence 11, schedule 16, usage 12).
- `ruff check packages/ops` + S5 scripts: **All checks passed!**
- `capture_golden_s5.py` run twice → `diff -r` **byte-identical** (32 files);
  the only non-deterministic input (uuid delivery ids) is canonicalized
  explicitly in the recorded bodies (field `signatureCanonicalized`).
- `verify_distribution.py`: **6 wheels** built (contracts, extensions, wiki,
  workflows, telemetry, ops), installed into a fresh venv with no repo on
  `sys.path`, smoke journey green → `DISTRIBUTION_VERIFIED`
  (`interop/distribution-verify.log`).
- `integration_check.py`: `INTEGRATION_CHECK=OK` (all probes true: roles,
  telemetry fixtures, plan gate, ops routes, real orchestrator approve route,
  distribution log; S5 itself reports present with 32 goldens).

## Decisions log

- **stall_hours 12.0 < abandonment 24h** — with 24h the `stalled_run` rule
  could never fire (state flips to abandoned first). 12h is documented in
  the rule text so the overlap is explicit.
- **Reject is local-only** — Section 4 exposes no verified reject route; the
  S5 reject action records `recorded: true` and says so verbatim instead of
  inventing an endpoint.
- **Scheduling lives outside the app** — no background loop; the runbook
  gives the exact cron command, keeping the web process stateless.
- **Delivery idempotency is receiver-side** — sender guarantees a stable
  `deliveryId` per event and bounded retries; the golden receiver dedupes,
  matching how customers would integrate.
- **Golden determinism over raw bytes** — golden request bodies substitute
  auto-generated delivery ids with `<auto-deliveryId>` and re-sign with the
  fixed golden secret (`signatureCanonicalized: true` marks this); raw-wire
  signature verification is covered by tests, not goldens.

## Known limits carried forward

- SQLite single-node; no queue/HA; pilot-grade UI-free API surface.
- Live Slack app, real customer webhook endpoints and real CI providers were
  not contacted; all transports were local HTTP.
- VR031: instruments ship, **no pilot has run — no ROI numbers exist or are
  claimed**. VR036/037/038/042/044/050 remain external. See
  `release-blockers.md` for the exact pass conditions.

## Program close-out

This is the final A5 session. The aggregated 30-feature / 10-risk ledger and
the exact release/integration limits are published at
`docs/agent-program/a5/final-ledger.{json,md}` (generated from the five
session ledgers).
