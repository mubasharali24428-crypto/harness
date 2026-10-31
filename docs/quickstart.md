# Quickstart

Ten minutes from a checkout to a governed run you can read back: passwordless
login, a seeded run you can inspect step by step, the event replay, and the audit
row that proves it happened. Every command below is one the stack's own journey
test runs (`make e2e-compose`, 12 checks) — nothing here is illustrative-only.

## 0. Prerequisites

* Docker + Docker Compose v2, Python 3.11+ for the `curl`-free snippets,
* ~4 GB RAM free, ports 3000/8001-8008/5499.

## 1. Start the stack

```sh
cd harness
cp .env.example .env
printf 'HARNESS_MASTER_KEY=%s\n' "$(python3 -c 'import secrets;print(secrets.token_hex(32))')" >> .env
docker compose --env-file .env -f deploy/docker-compose.yml up -d
docker compose --env-file .env -f deploy/docker-compose.yml run --rm migrate
```

`--env-file .env` is required: Compose reads `.env` from the compose file's
directory (`deploy/`), so without it the key above never reaches the services
and `migrate` refuses to start (found by the W23 fresh-setup run).

Wait until identity is *ready* (readiness gates the database and migrations, not
just the process):

```sh
curl -fsS http://127.0.0.1:8001/ready && echo
curl -fsS http://127.0.0.1:8004/ready && echo
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/   # 200
```

The UI is at <http://127.0.0.1:3000>. If any of these hang, `make e2e-compose`
prints the same checks with per-container status.

## 2. Log in (passwordless)

```sh
EMAIL=me@example.com
curl -sS -X POST http://127.0.0.1:8001/api/v1/auth/request-login \
  -H 'content-type: application/json' -d "{\"email\":\"$EMAIL\"}" >/dev/null
CODE=$(docker logs harness-identity-1 2>&1 \
  | grep "login code for $EMAIL" | tail -1 | sed -E 's/.*: ([0-9]+)$/\1/')
AUTH=$(curl -sS -X POST http://127.0.0.1:8001/api/v1/auth/verify-otp \
  -H 'content-type: application/json' -d "{\"email\":\"$EMAIL\",\"code\":\"$CODE\"}")
TOKEN=$(echo "$AUTH" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
TEAM=$(echo "$AUTH"  | python3 -c 'import json,sys;print(json.load(sys.stdin)["principal"]["teamId"])')
echo "${TOKEN:0:12}… team $TEAM"
```

The first login creates your account and a solo team where you are the admin. The
code is printed to the identity log **only** in a local deployment — with SMTP
configured it goes to your inbox instead.

## 3. Seed a run and read it

```sh
SEED=$(curl -sS -X POST http://127.0.0.1:8004/api/v1/orchestrator/demo/seed \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{}')
echo "$SEED" | python3 -m json.tool
PROJECT=$(echo "$SEED" | python3 -c 'import json,sys;print(json.load(sys.stdin)["projectId"])')
RUN=$(echo "$SEED" | python3 -c 'import json,sys;print(json.load(sys.stdin)["runId"])')

curl -sS "http://127.0.0.1:8004/api/v1/orchestrator/runs/$RUN" \
  -H "authorization: Bearer $TOKEN" | python3 -m json.tool | head -40
```

You get `state: "pr_opened"` and the steps that got there. The seed is a
**recorded replay**, and the API says so: `GET …/runs/$RUN/engine` reports
`mode: "replayed"`, never a live claim. Re-running the seed is idempotent.

## 4. See the events and the audit trail

```sh
curl -sS "http://127.0.0.1:8001/api/v1/projects/$PROJECT/events/replay?limit=50" \
  -H "authorization: Bearer $TOKEN" | python3 -m json.tool | head -30

# A read is not audited; an admin action is. Mint an invite, then read the page.
curl -sS -X POST "http://127.0.0.1:8001/api/v1/teams/$TEAM/invites" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d "{\"email\":\"invitee-$EMAIL\",\"role\":\"engineer\"}" >/dev/null
curl -sS "http://127.0.0.1:8001/api/v1/audit?limit=20" \
  -H "authorization: Bearer $TOKEN" | python3 -m json.tool | head -30
```

Every replayed event carries `payload.demo.replay: true`, so a seeded record can
never be mistaken for a real run. The audit page answers with the `invite.minted`
row you just caused: it is team-scoped, append-only, and it is the same trail a
SIEM sink pushes (`docs/runbooks/ops.md` §3.10).

## 5. Do it for real on your repo

1. Import your conventions — `docs/migrating-from-claude-code-and-codex.md`.
2. Create a project pointing at the repository, then open a run. Harness asks for
   a **human-approved plan** before the agent touches anything.
3. Read the diff, the review, and the cost line; merge yourself. There is no
   auto-merge on any tier.

The two repos under [`examples/`](../examples/) are the shortest path through that
loop: one has a failing test to fix, one has a change waiting for review.

## 6. What to read next

| Topic | Document |
|---|---|
| What each tier actually enforces | `PRICING.md` |
| Why a run did what it did | `docs/audit-and-events.md`, `docs/lifecycle.md` |
| Sandbox and network boundary | `docs/agents.md`, `docs/runbooks/ops.md` |
| SDKs (Python + TypeScript) | `clients/python/README.md`, `clients/ts/README.md` |
| Troubleshooting | `docs/runbooks/ops.md` |

## Verifying this page

`make e2e-compose` runs the same steps against the compose stack and fails on any
divergence (readiness, login, seed idempotency, run aggregate, replay markers,
audit row, sandbox lifecycle). If this page and that script disagree, the script is
right.
