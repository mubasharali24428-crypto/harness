# First governed task — operator guide

Reconciled with the source and deployed OpenAPI on 2026-11-12. The previous August
walkthrough used obsolete client-generated run IDs and unsafe volume-reset advice.
This revision has **not been executed end-to-end by a real design partner**. The separate
test agent must rehearse it; partner acceptance requires a real partner, not a synthetic login.

## 1. Before a partner connects anything

Read `deploy/ONBOARDING.md`, the current security limitations, and the approved pilot scope.
Do not connect production repositories, secrets, regulated data or payment-card data.
Do not use the local `harness-rc` scratch deployment as a customer environment.

Prerequisites: Docker Compose >=2.24.4, Python >=3.11, GNU Make, curl and jq.
A live model run additionally needs an approved provider, model, budget and data scope.
Contributor tests are separate from operator onboarding; do not ask a partner to run them.

Use the operator's private `.env`; never print or share it. If creating a new one:

```sh
cp .env.example .env
printf '\nHARNESS_MASTER_KEY=%s\n' "$(openssl rand -hex 32)" >> .env
```

Edit the file rather than repeatedly appending duplicate keys. Keep ownership verification
enabled for an exposed deployment. Configure SMTP for real email delivery; development OTP
codes in container logs are not a production authentication mechanism.

## 2. Boot and inspect, without deleting data

From the repository root, with Docker running:

```sh
export HARNESS_IMAGE_TAG=<approved-image-tag>
docker compose -p harness --env-file .env \
  -f deploy/docker-compose.yml --profile worker up -d --wait --wait-timeout 180
docker compose -p harness --env-file .env \
  -f deploy/docker-compose.yml --profile worker ps -a
python3 scripts/ops/stack_tags.py --project harness
```

Use an explicit reviewed image tag, not `latest`. The worker profile is required for live
isolated runs. Check the migrate one-shot exited 0 and inspect actual image IDs against the
release manifest. A matching tag alone does not prove matching source.

If migration reports schema drift, **stop**. Do not run `down -v`, delete a volume, or boot
an older image against a newer database. Follow `docs/runbooks/rollback.md` and
`docs/runbooks/backup-and-pitr.md`; use a scratch restore for rehearsal. The run volume has
a fixed name unless explicitly scoped, so a different Compose project name alone is not isolation.

## 3. Sign in and create a real project

Open the configured frontend URL (default `http://localhost:3000`). Use email ownership
verification. With SMTP configured, the code arrives by email. Only in a local development
rehearsal may the operator retrieve a code from the identity container logs.

Create a project in the UI. For an API rehearsal, use the authenticated identity endpoint:

```sh
curl --fail-with-body -sS -X POST http://localhost:8001/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"first-governed-task"}' > /tmp/harness-project.json
PROJECT_ID=$(jq -r '.id' /tmp/harness-project.json)
```

Keep tokens out of screenshots, shell history shared with others, and evidence documents.
GitHub OAuth is optional and requires the operator's own OAuth app; see `deploy/ONBOARDING.md`.

## 4. Propose, inspect and approve

The current contract is `POST /api/v1/orchestrator/plans`. The server creates the run ID;
the client must not invent one. The plan is currently a displayed planning template, not proof
of a model-generated task-specific plan.

```sh
curl --fail-with-body -sS -X POST http://localhost:8004/api/v1/orchestrator/plans \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"goal\":\"Create temperature.py and a unit test, then run the test\",\"projectId\":\"$PROJECT_ID\"}" \
  > /tmp/harness-plan.json
PLAN_ID=$(jq -r '.planId' /tmp/harness-plan.json)
jq . /tmp/harness-plan.json
```

Inspect the goal, expected result, steps, tests, risks and forbidden paths. A human decides:

```sh
curl --fail-with-body -sS -X POST \
  "http://localhost:8004/api/v1/orchestrator/plans/$PLAN_ID/approve" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}' \
  > /tmp/harness-approval.json
RUN_ID=$(jq -r '.runId' /tmp/harness-approval.json)
```

Rejection uses the same plan path with `/reject`. Do not approve merely to make a demo pass.

## 5. Start and observe the actual run

```sh
curl --fail-with-body -sS -X POST \
  "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/start" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
curl --fail-with-body -sS \
  "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/status" \
  -H "Authorization: Bearer $TOKEN" | jq .
curl --fail-with-body -sS \
  "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/engine" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Dry mode is expected to report that no provider is configured; it is not a successful coding
task. A live run requires the approved provider configuration and worker daemon. Do not infer
live execution from a UI badge. Record the actual run ID, model, events, cost, failures and
interventions. A completed engine call alone does not prove the requested tests passed.

## 6. Review real changes and accept deliberately

Use the run's Changes/Review UI to inspect the actual diff and recorded test output. The
human done-signal requires non-empty diff evidence and an authorized role; it must not be a
rubber stamp. Do not paste a fabricated diff to obtain a green review.

```sh
curl --fail-with-body -sS \
  "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/diff" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Only after inspecting real delivery, submit its actual diff through the UI or
`POST /api/v1/orchestrator/runs/$RUN_ID/done-signal` with `{"diffText":"<actual diff>"}`.
Resolve staged approvals before completion. Verify the resulting files and test command output
independently; `__pycache__` is not proof of a passing test. Merges remain manual.

## 7. Evidence for the first-task acceptance

Record in `docs/briefs/evidence/90day/week-04.md`:

- partner identity/consent and approved scope, without secrets;
- date, host, source revision, image IDs, provider/model and configuration limits;
- project, plan and run IDs; who approved, and when;
- actual diff, test command/output, review decisions, cost and interventions;
- whether the partner completed the task without operator assistance;
- friction, failures and the partner's continue/stop decision.

The separate test agent may rehearse with synthetic data first. A rehearsal does not satisfy
P-07's real-partner acceptance criterion.

## References

- `docs/api-versioning.md` and each service's `/openapi.json`
- `clients/python/README.md` and `clients/ts/README.md` for SDK usage
- `docs/runbooks/` for operations and restore procedures
- `docs/briefs/evidence/90day/demo/verification-2026-11-11.md` for dated W24 demo evidence,
  which does not certify the current RC or a customer deployment
