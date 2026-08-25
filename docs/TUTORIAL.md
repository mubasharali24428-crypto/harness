# TUTORIAL — your first hour with the Harness

This walkthrough was executed command-by-command against a fresh stack (August
2026). Everything below shows what the system actually returns — including one
honest dead end you are meant to see. Time budget: ~15 minutes once the stack
is up (stack boot itself is a few minutes the very first time, mostly Docker
image builds).

You will: bring up the stack, log in, create a project, walk one agent run
through the full human-governance loop (propose → **you approve** → execute →
review), and read the event trail it left behind.

Prerequisites: Docker, Python ≥ 3.11, GNU make, curl + jq. For the live-run
half, any OpenAI-compatible provider works — the examples use local
[Ollama](https://ollama.com).

---

## 0. Contributor setup (one time)

The repo's gates (`make test-section1`, `make lint`, `make gate`) each run
inside a per-section virtualenv. Nothing creates those venvs implicitly — this
is the one command that does:

```sh
make bootstrap
```

It provisions all eight backend-section venvs exactly the way CI does
(editable install of `contracts/` first, then the section with its `dev`
extras) and installs frontend deps from the committed lockfile. Takes ~90
seconds. It is idempotent — re-run any time. `make clean-venvs` removes the
venvs if you want to start over.

Prove it worked:

```sh
make test-section1     # cd section1 && ./.venv/bin/python -m pytest tests/ -q
```

## 1. Configure and boot the stack

```sh
cp .env.example .env
# then set a real master key — uncomment the HARNESS_MASTER_KEY line in .env,
# or simply append:
printf '\nHARNESS_MASTER_KEY=%s\n' "$(openssl rand -hex 32)" >> .env
```

`.env.example` documents every variable (name, purpose, default,
required-or-optional). Only `HARNESS_MASTER_KEY` is required for a dev boot.

> **Why `--env-file`:** when compose is invoked as
> `docker compose -f deploy/docker-compose.yml …` from the repo root, some
> Docker Compose versions do **not** read the repo-root `.env` — they look
> next to the compose file. Passing `--env-file .env` explicitly works on
> every version and is what this tutorial uses.

```sh
docker compose --env-file .env -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps    # expect ~9 services Up/healthy
```

First boot builds images and runs the one-shot `migrate` service (schema
migrations + demo-repo seed) before the services start. If you ever see
`migrate … didn't complete successfully`, read its log before anything else:

```sh
docker compose -f deploy/docker-compose.yml logs migrate
```

Two known causes: an empty `HARNESS_MASTER_KEY` (fix: step 1 above), or a
*pre-existing* Postgres volume whose schema predates your current checkout
(fix: `docker compose --env-file .env -f deploy/docker-compose.yml down -v`
— **destroys local data**, dev machines only).

## 2. Log in (email + OTP)

The stack ships with ownership verification ON: a plain email login gets
`403 OWNERSHIP_REQUIRED` until the address proves itself with a one-time
code. Without SMTP configured, dev OTP codes print to the identity logs:

```sh
curl -s -X POST http://localhost:8001/api/v1/auth/request-login \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com"}'
docker compose -f deploy/docker-compose.yml logs identity | grep HARNESS-OTP
# identity-1  |  HARNESS-OTP login code for you@example.com: 374377

OTP=374377   # yours will differ
curl -s -X POST http://localhost:8001/api/v1/auth/verify-otp \
  -H 'content-type: application/json' \
  -d "{\"email\":\"you@example.com\",\"code\":\"$OTP\"}" | tee /tmp/login.json
```

Response (your token is the long string under `token`; save it):

```json
{
  "token": "harn_…",
  "principal": {
    "userId": "69b7c0d9-…",
    "teamId": "22596018-…",
    "role": "admin"
  }
}
```

Verifying an email auto-mints your solo team with you as admin. Save the
token and set convenience vars:

```sh
TOKEN=$(jq -r .token /tmp/login.json)
AUTH="authorization: Bearer $TOKEN"
```

(The browser at <http://localhost:3000> walks through this same flow on the
login page's *Request login code* tab.)

## 3. Create a project

Projects group runs, budgets, rules, and the event stream:

```sh
curl -s -X POST http://localhost:8001/api/v1/projects \
  -H "$AUTH" -H 'content-type: application/json' \
  -d '{"name":"tutorial-first-project"}' | tee /tmp/proj.json
```

```json
{
  "id": "8c5409f0-c458-4355-968f-dd4e6bf3fd1f",
  "name": "tutorial-first-project",
  "repoFullName": null
}
```

```sh
PROJECT_ID=$(jq -r .id /tmp/proj.json)
```

## 4. Propose a plan — the human gate

An agent never touches code until a human approves a plan. Ask the
orchestrator (:8004) for one:

```sh
RUN_ID="run-tutorial$(date +%s)"   # the client supplies the run id here
curl -s -X POST "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/plan" \
  -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"goal\":\"Write a hello.py that prints the current UTC time\",\"projectId\":\"$PROJECT_ID\"}"
```

```json
{"runId": "run-tutorial33599", "planId": "plan-9a5a7a1f"}
```

Read the plan back, then decide:

```sh
PLAN_ID=plan-9a5a7a1f
curl -s "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/plan" -H "$AUTH" | jq .
curl -s -X POST "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/plan/approve" \
  -H "$AUTH" -H 'content-type: application/json' -d "{\"planId\":\"$PLAN_ID\"}"
```

Note the approval body carries `planId` — the server checks the approved plan
belongs to this run. In the UI this is the dashboard's Plan Gate.

## 5. First run — and the honest dead end (dry mode)

```sh
curl -s -X POST "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/start" \
  -H "$AUTH"
```

With the shipped defaults (`ENGINE_LIVE_MODE=dry`) the run executes against an
honest stub executor and finishes **blocked**, by design:

```sh
curl -s "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/status" -H "$AUTH"
# {"runId": "...", "state": "blocked", "planId": "...", "engineMode": "dry", ...}
curl -s "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/engine" -H "$AUTH" | jq .
```

```json
{
  "sessionId": "run-tutorial33599",
  "engine": "stub",
  "status": "not_configured",
  "finalResponse": "DeepSeek Harness not configured: set ENGINE_LIVE_MODE=live
  with DEEPSEEK_API_KEY (and DEEPSEEK_BASE_URL for OpenAI-compatible
  endpoints) to enable live runs."
}
```

Nothing is faked: dry mode tells you plainly that no provider is configured.
To see an agent actually work, configure one — next step.

## 6. Flip to LIVE mode (local Ollama example)

Any OpenAI-compatible endpoint works. With Ollama running locally
(`ollama pull qwen3.5:9b` or any model you have):

```sh
cat >> .env <<'EOF'
ENGINE_LIVE_MODE=live
DEEPSEEK_API_KEY=ollama-local          # Ollama ignores the value; must be non-empty
DEEPSEEK_BASE_URL=http://host.docker.internal:11434/v1
DSH_MODEL=qwen3.5:9b                   # any tag from `ollama list`
EOF
docker compose --env-file .env -f deploy/docker-compose.yml up -d   # recreates affected services
```

(Hosted providers: same three variables, real key, their base URL, their
model name.)

Repeat steps 4–5 (new `$RUN_ID`, propose → approve → start). This time:

```sh
sleep 30
curl -s "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/status" -H "$AUTH" | jq '{state}'
# {"state": "awaiting_done_signal"}
curl -s "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/engine" -H "$AUTH" | jq '{status, engine, finalResponse}' 
```

```json
{
  "status": "completed",
  "engine": "deepseek-harness-sdk:qwen3.5:9b",
  "finalResponse": "I've created hello.py that prints the current UTC time…"
}
```

`engine` always names the model that actually answered — no silent fallback.

## 7. Done-signal → automated review

The agent signals completion with its diff; the reviewer sub-agent checks it
(syntax scan, merge-conflict markers, brief alignment):

```sh
curl -s -X POST "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/done-signal" \
  -H "$AUTH" -H 'content-type: application/json' \
  -d '{"diffText":"diff --git a/hello.py b/hello.py\nnew file\n+++ b/hello.py\n+from datetime import datetime, timezone\n+print(datetime.now(timezone.utc))"}' | jq .
```

```json
{
  "runId": "run-live-33756",
  "findings": {
    "passed": true,
    "syntaxOk": true,
    "conflictsDetected": false,
    "briefAligned": true,
    "summary": "Review passed cleanly."
  }
}
```

```sh
curl -s "http://localhost:8004/api/v1/orchestrator/runs/$RUN_ID/status" -H "$AUTH" | jq '{state}'
# {"state": "done"}
```

## 8. Read the events — the audit trail

Every transition appended immutable, ordered events to the project stream.
Replay them from the identity service (:8001):

```sh
curl -s "http://localhost:8001/api/v1/projects/$PROJECT_ID/events/replay?afterSeq=0" \
  -H "$AUTH" | jq '[.items[] | {seq, type}]'
```

A completed tutorial run leaves ten:

| seq | type |
|---|---|
| 1 | plan_proposed |
| 2 | plan_approved |
| 3 | execution_started |
| 4 | engine_failed ← the dry-mode dead end, still on the record |
| 5 | plan_proposed |
| 6 | plan_approved |
| 7 | execution_started |
| 8 | engine_completed |
| 9 | done_signal_received |
| 10 | review_passed |

Each envelope also carries `ts`, `projectId`, and `runId`. Live tailing from a
browser uses the ws-ticket dance (`POST /api/v1/ws-ticket`, single-use 30 s
ticket, then WebSocket); REST replay covers scripting.

## Where to go next

- **UI tour:** <http://localhost:3000> → the `/onboarding` checklist mirrors
  these steps and deep-links each one.
- **GitHub connect** (real repos): `deploy/ONBOARDING.md` § GitHub OAuth —
  requires registering your own OAuth app; everything else works without it.
- **API surface:** per-service OpenAPI at `:8001/:8002/:8003/:8004/:8005/:8006/:8008/openapi.json`;
  conventions in `docs/api-versioning.md`.
- **Contribute:** `make bootstrap` (step 0), then `make gate` — the exact
  command CI runs on PRs. Layout + laws: `AGENTS.md`. Glossary:
  `docs/GLOSSARY.md`.

## Known sharp edges (as executed August 2026)

- **Stale containers, current source:** images only change when rebuilt. If a
  route the docs mention 404s, `docker compose --env-file .env -f
  deploy/docker-compose.yml build && docker compose --env-file .env -f
  deploy/docker-compose.yml up -d` first.
- **Pre-existing volumes vs. new migrations:** `migrate` failing with
  `DuplicateColumnError` means your volume predates the tracked migration
  ledger (`section1/migrations/`). Dev escape hatch: `down -v` and reboot
  (wipes local data).
- **No DELETE /projects/{id}:** probe projects accumulate; there is currently
  no cleanup endpoint.
- Some error responses (FastAPI validation arrays) do not yet use the frozen
  error envelope everywhere — being closed under M17-B3; don't hard-code
  against bare `detail` arrays.
