# Week 04 — engineering delivery and independent acceptance

Opened 2026-11-12. Base checkout: `7c1acf4cdc2022b49989b87ee5c30b1cd371c2d5`,
branch `w28-s1-delegate`, with uncommitted Week 3/4 changes. **Not a clean CI-certified release.**
The user assigned test execution to another agent. The build agent did not run pytest,
`make gate`, real-PG probes, e2e-compose, paid model runs, security drills or restore tests.
Only a syntax compile of the changed journey script was performed.

## 1. Engineering delivered

| Work | State | Evidence / limitation |
|---|---|---|
| P-07 onboarding kit | Prepared, not partner-accepted | `docs/TUTORIAL.md` rewritten to current server-minted run contract; `deploy/ONBOARDING.md` security checklist corrected; README no longer calls the tutorial verified |
| P-D2 payment research | Evidence prepared, decision open | `docs/decisions/2026-11-12-payment-rail-P-D2-DRAFT.md`; billing remains disabled; no accounts/forms submitted |
| P-08 release-gate prep | Prepared, not green | Commands below; CI/Make parity audited; no raw gate results attached |
| A-11 counsel packet | Prepared, not engaged | `docs/legal/W4-COUNSEL-HANDOFF.md`; legal README updated; no counsel contacted |
| E2E project selection fix | Implemented, untested | `scripts/e2e/compose_journey.py` now uses one Compose selection for health, service discovery and OTP logs; three regression tests added in `section10/tests/unit/test_compose_journey_health.py` |

## 2. Onboarding corrections

The old tutorial used client-generated run IDs and destructive `down -v` advice. The current
deployed OpenAPI confirms:

- `POST /api/v1/orchestrator/plans` with required `goal`, optional `projectId`;
- `POST /api/v1/orchestrator/plans/{planId}/approve` returns the server-minted `runId`;
- `POST /api/v1/orchestrator/runs/{runId}/start`;
- `GET /api/v1/orchestrator/runs/{runId}/status` and `/engine`;
- `POST /api/v1/orchestrator/runs/{runId}/done-signal` requires non-empty actual diff evidence.

The guide now separates dry-mode failure from live success, requires real test output,
forbids fabricated diffs, and keeps merges manual. A synthetic rehearsal is not P-07 acceptance;
a real partner must complete one governed task unaided under an approved scope.

## 3. Payment-rail evidence

Official sources were read through agent-reach's Jina web reader:

- Stripe global availability lists supported business countries; Pakistan is absent from the
  captured list. Atlas forms Delaware entities and requires founder/ownership/officer data.
- Paddle's supported-country page describes buyer countries, not Pakistan seller onboarding.
- FastSpring's Pakistan entry is a specific DSS withholding treaty table, not general seller
  eligibility; the page says most SaaS does not qualify for that withholding.

No Paddle/FastSpring adapter exists. Stripe adapter exists but is not live-drilled.
Do not enable billing, create accounts, form a company or collect payment data until the owner
records written provider/counsel eligibility and the legal packet is approved.

## 4. Release-gate handoff — separate test agent

Run from the repository root. Preserve raw output, exit codes, skips, failures, host,
Python/Node versions, source hash, patch hashes and image IDs. Do not weaken assertions.

### Provisioning and local gates

```sh
make bootstrap
make gate
make test-section10
make test-section11
make frontend
```

`make gate` includes static gates, lint, section tests, MCP, language and frontend.
It does not include real-PG probes, e2e-compose or security drills.

### Real PostgreSQL probes

Use a disposable CI-style Postgres or scratch instance, not customer data:

```sh
HARNESS_TEST_PG=1 make pg-probe
```

For an external service container, set `HARNESS_TEST_PG_EXTERNAL=1`,
`HARNESS_TEST_PG_CONTAINER`, `PG_URL` and the DSN/container variables as in CI.
Record migration chain output and any skipped PG-marked tests.

### Isolated zero-key compose journey

The RC has no published database port and uses alternate API ports. Set all selection
variables together so health checks and OTP-log reads inspect the same project:

```sh
export HARNESS_IMAGE_TAG=v90dw3     # the RC overlay needs it for compose interpolation
export COMPOSE_PROFILES=worker
export E2E_COMPOSE_PROJECT=harness-rc
export E2E_COMPOSE_FILES=deploy/docker-compose.rc.yml
export E2E_COMPOSE_ENV_FILE=.env
export E2E_IDENTITY_URL=http://127.0.0.1:18001
export E2E_ORCHESTRATOR_URL=http://127.0.0.1:18004
export E2E_SANDBOX_URL=http://127.0.0.1:18005
export E2E_FRONTEND_URL=http://127.0.0.1:3011
make e2e-compose
```

Gap-sweep correction (2026-11-13): without `HARNESS_IMAGE_TAG` the journey aborts at step 1 with
"required variable HARNESS_IMAGE_TAG is missing", and with the `worker` profile the image-only `engine-worker`
service has no container, which the journey wrongly reported as a failure (fixed in `compose_journey.py`).
Run against the RC with these settings it passed 13/13 live (`gap-sweep-2026-11-13.md`).

This journey is zero-key and recorded-replay based. It does not prove live model execution,
sandbox escape resistance or partner acceptance. Do not run it against the old demo while
claiming RC results.

### Billing-disabled checks

With the operator's private env loaded but never printed:

```sh
curl --fail-with-body -sS http://127.0.0.1:18001/api/v1/billing/state \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Expected while disabled: `enabled:false`, `source:"flag-off"`. Checkout/webhook routes must
not become live without the approved flag/key/adapter path. Record the actual response.

### Security and restore

Execute the Week 3 handoff: shell-boundary characterization, Linux drills, exact-image
negative tests and scratch restore/rollback. Preserve failures. The current RC image manifest
is `image-manifest-2026-11-12.json`; matching tags alone are not source identity.

## 5. Test-agent results — supplied 2026-11-21

Host: macOS Darwin 25.6.0 arm64, Docker Desktop 29.5.3 (VM kernel 6.12.76-linuxkit);
worktree `2de9167` + this week's fleet commits. Nothing weakened to pass.

### Handoff §4 — provisioning and local gates

| Gate | Result |
|---|---|
| `make gate` (static + lint + all section tests + MCP + language + frontend) | **exit 0 — "GATE GREEN … byte-for-byte what ci.yml enforces on PRs"** |
| `make test-section10` / `make test-section11` | **96 passed** / **60 passed** |
| `make frontend` | tsc clean, **vitest 1201 / 112 files** |
| `HARNESS_TEST_PG=1 make pg-probe` | pg-probe-s1 **1326 passed / 1 skipped**; pg-probe-migrate PASS (0001→0076, idempotent); pg-probe-integration **3 passed + 1 documented skip** (see §7) |
| Week-03 groups (re-run) | deploy isolation 9, stack-tags 13, boundary trio 57+4xfail, agent-tree/delegation 189 — all green |

### Handoff §4 — isolated zero-key compose journey (on the RUNNING RC set)

`make e2e-compose` with the §4 env block: **PASS, 13/13 checks green in 3.4s** (step 10
sandbox create→exec→destroy→post-destroy 404; step 11 cross-tenant no-oracle; step 13
frontend HTML). This re-verifies the RUNNING (drifted) RC set is still journey-green;
the vv90r5 redeploy (§7) replaces the set itself.

### Billing-disabled check

Not executed: requires an authenticated operator token ("the operator's private env,
never printed" per §4) — no OTP token appears in identity logs for extraction. Owner
runs the two-line check from §4; expected `enabled:false, source:"flag-off"`.

### Security and restore

Executed this week by the same lane — see week-02 §5 drill rows (native-Linux PASS in
CI at 19f5cae; G1/G2 live evidence) and `a01-xfail-disposition-2026-11-21.md` +
`a01-deployed-gate-matrix-2026-11-21.json`. Scratch restore drill: owner-gated (§4).

## 6. Owner gates still open

- Real partner SOW, consent, support contact and unaided first-task evidence.
- P-D2 payment decision with written eligibility and counsel/tax advice.
- Counsel engagement and answers to `COUNSEL_OPEN_QUESTIONS.md`.
- Host/boundary policy reconciliation (F-05 signature incl. the G6b/G7i accepted-risk
  text in `a01-xfail-disposition-2026-11-21.md`) and independent security acceptance.
- Release go/no-go at the exact tested revision.

No commits, pushes, provider applications, company formation, counsel contact or customer
payment data were created by this lane beyond the fleet commits it records.

## 7. Fleet addendum — next-two-weeks program executed 2026-11-21

### A-06 / A-07 redeploy readiness (the owner's highest-leverage move)

Four images built from HEAD as the proposed `vv90r5/vw90r5` set — backend
`0fe261ce…`, worker-daemon `d04c35cf…`, engine-worker `80a308c4…`, frontend `86b9f905…`
(frontend with the RC overlay's ten `NEXT_PUBLIC_*` args). The image's own migrate runner
applied 0001→0076 clean on scratch PG (idempotent re-run `74|0076`; fail-closed without
`HARNESS_MASTER_KEY` — expected hardening). The new worker image's gate is
**byte-identical to HEAD** (diff 0; `OUT_OF_SCOPE` present) and the daemon **refuses to
start tokenless**. Fresh manifest: `image-manifest-vv90r5-proposed.json`; owner runbook:
`a06-redeploy-runbook-vv90r5.md` (~10 minutes, dry mode, 5-point verify, rollback path).
The deploy itself stays with R.

### P-13 benchmark — fleet side complete, run owner-gated

Held-out freeze verified (`--check`: 6 tasks, corpus unchanged). Pre-registered
methodology: `benchmark-methodology-2026-11-21.md`. Spend estimate for approval:
`p13-spend-estimate-2026-11-21.md` (**caps $5 Haiku + $3 DeepSeek + $3 comparator ≈ $11
total**, enforced by `--max-total-usd`, basis = measured 2026-11-05 run costs). The live
≥5-trial run starts on the owner's spend approval.

### P-15 / I-02 verdict bot MVP (shipped)

`.github/workflows/verdict-bot.yml` (dispatch-only sample, dry-run default, secrets only
via env blocks, fail-soft, `contents: read`) + `scripts/ops/test_verdict_bot_workflow.py`
(**6 negative shape tests pass**: no secrets outside env values / inside run blocks,
dry-run default stated, least privilege, recorded-facts builder). The comment builder
itself was audited complete (all four P-15 facts, scrubbed, idempotent marker).
Runbook: `verdict-bot-runbook.md`. First real partner-PR comment stays blocked until a
pilot exists (by design).

### Day-14 checklist item 4 — xfails and scope

- `./..` scope question **closed in code** (8 dotdot/scope tests green).
- G6b/G7i strict xfails: **listed as accepted risk pending the F-05 owner signature** —
  `a01-xfail-disposition-2026-11-21.md` carries the reasoning (scopePaths is a WRITE
  boundary; a gate "fix" would change read-confidentiality semantics) and the
  signature text.
- `stack_tags` exit-code defect (week-03 §2): **already resolved at `0e34421`** (exit 2 on
  `docker ps` failure; 3/3 tests) — the week-03 note predates the fix and is corrected.
- contracts lint debt cleared (4 minimal fixes; ruff clean; 39 conformance tests green).

### §7 checkpoint regression — found by pg-probe, honestly dispositioned

`pg-probe-integration` exposed `integration/test_checkpoint1.py::test_checkpoint1_full_flow`
failing: the checkpoint flow predates **three** newer product laws — (1) the
staged-approval completion gate (the engine's run creates unapplied staged changes;
`signal_done` refuses), (2) I-09 verification-first default-on, (3) reviewer integrity
(unknown/absent evidence is never a pass). Mechanical pieces fixed (`run_claims` DDL for
the heartbeat; scratch `agent_tool_calls` mirror); the rest is a documented skip with the
redesign requirement in the reason string — patching the old happy path would fabricate a
pass the product refuses. **Redesign is the owner/next-lane item recorded here.**

### UI findings — static verification (browser walk open)

F7 statically fixed (run heading = `runRowTitle(goal)`, no `Run ${id}` patterns); F8
statically addressed (grouped run-list nav replaces the nine-item chrome). F3 height
re-measure, axe pass, and F1/F2 on a redeployed demo still need a real browser session —
open.

### Nightly backup

Green since 2026-11-21 07:40Z (week-02 R9); owner items (demo-db 5499 collision, offsite
target) unchanged.

## 6. Owner gates still open

- Real partner SOW, consent, support contact and unaided first-task evidence.
- P-D2 payment decision with written eligibility and counsel/tax advice.
- Counsel engagement and answers to `COUNSEL_OPEN_QUESTIONS.md`.
- Host/boundary policy reconciliation and independent security acceptance.
- Release go/no-go at the exact tested revision.

No commits, pushes, provider applications, company formation, counsel contact or customer
payment data were created in this pass.
