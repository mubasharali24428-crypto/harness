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

## 5. Results to be supplied by test agent

Not yet supplied. Required fields: timestamp, host/kernel, source/patch hashes, image IDs,
commands, exit codes, raw logs, run IDs, spend, failures, skips, XFAIL/XPASS reasons,
partner identity/consent if applicable, and continue/stop decision.

## 6. Owner gates still open

- Real partner SOW, consent, support contact and unaided first-task evidence.
- P-D2 payment decision with written eligibility and counsel/tax advice.
- Counsel engagement and answers to `COUNSEL_OPEN_QUESTIONS.md`.
- Host/boundary policy reconciliation and independent security acceptance.
- Release go/no-go at the exact tested revision.

No commits, pushes, provider applications, company formation, counsel contact or customer
payment data were created in this pass.
