# AI Engineering Harness

Self-hosted platform where AI coding agents do real engineering work under
human governance: mandatory human plan approval, sandboxed execution,
event-sourced audit log, **manual merges only** — no auto-merge anywhere.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Terms](https://img.shields.io/badge/Terms-skeleton--pending%20counsel-orange)](TERMS.md)
[![Privacy](https://img.shields.io/badge/Privacy-self--hosted--first-blue)](PRIVACY.md)
[![Pricing](https://img.shields.io/badge/Pricing-Free%20%2F%20Pro%20%2F%20Team-green)](PRICING.md)

**Legal & pricing:** [`LICENSE`](LICENSE) (MIT — default choice, see header
comment for the decision point) · [`TERMS.md`](TERMS.md) ·
[`PRIVACY.md`](PRIVACY.md) · [`PRICING.md`](PRICING.md)
(Free/Pro/Team tiers mapped to real `PLAN_TIERS` + metering).

- New operator? Start with **[`docs/TUTORIAL.md`](docs/TUTORIAL.md)** — a
  verified walkthrough: boot the stack, log in, run one governed agent task,
  read its events.
- Deploying? **[`deploy/ONBOARDING.md`](deploy/ONBOARDING.md)**:
  `cp .env.example .env`, then `docker compose --env-file .env -f deploy/docker-compose.yml up -d`.
  `.env.example` documents every environment variable (name, purpose, default,
  required-or-optional).
- Notable changes per release: **[CHANGELOG.md](CHANGELOG.md)**.
- Current status, wave history, and known gaps: **[STATUS.md](STATUS.md)**.
- Builder/agent rules (frozen contracts, migration law): **[AGENTS.md](AGENTS.md)**.
- REST/WS conventions, `/api/v1` versioning, deprecation policy:
  **[docs/api-versioning.md](docs/api-versioning.md)** (normative source:
  [`contracts/api-conventions.md`](contracts/api-conventions.md)).
- Project vocabulary as implemented: **[docs/GLOSSARY.md](docs/GLOSSARY.md)**.
- Extending the platform (engine `backend_runner` injection, reviewer
  thresholds, project rules): **[docs/extensions.md](docs/extensions.md)**.
- Operations runbooks: **[docs/runbooks/](docs/runbooks/)**
  (`ops.md` incl. health probes / stalled runs / log correlation,
  `rollback.md`, `backup-and-pitr.md`, `degraded-mode.md`, `alerting.md`) and
  maintenance schedules
  (**[docs/runbooks/maintenance.md](docs/runbooks/maintenance.md)**).
- CI/local gates (`make gate`): **[docs/ci-gates.md](docs/ci-gates.md)**;
  DB connection budget: **[docs/pool-math.md](docs/pool-math.md)**.
- Layout of the ten sections + contracts package: see the tree in **[AGENTS.md](AGENTS.md)**.

## Quick start

```sh
cp .env.example .env
printf '\nHARNESS_MASTER_KEY=%s\n' "$(openssl rand -hex 32)" >> .env   # required to boot
docker compose --env-file .env -f deploy/docker-compose.yml up -d      # first boot builds images
open http://localhost:3000
```

Log in with **Request login code** (email OTP). With no SMTP configured, dev
OTP codes print to the identity logs:

```sh
docker compose -f deploy/docker-compose.yml logs identity | grep HARNESS-OTP
```

> **Why `--env-file .env`:** invoked as `docker compose -f deploy/…` from the
> repo root, some Docker Compose versions do not read a repo-root `.env` (they
> look next to the compose file) — your master key and provider config would
> be silently ignored. `--env-file .env` works on every version.

The first run you start will end `blocked` with an honest `not_configured`
engine result — shipped defaults use dry mode (`ENGINE_LIVE_MODE=dry`) so no
run ever pretends to have a model behind it. To see real agent work in five
minutes, point the engine at any OpenAI-compatible endpoint (local Ollama
counts):

```sh
cat >> .env <<'EOF'
ENGINE_LIVE_MODE=live
DEEPSEEK_API_KEY=ollama-local          # value ignored by local providers; must be non-empty
DEEPSEEK_BASE_URL=http://host.docker.internal:11434/v1
DSH_MODEL=qwen3.5:9b                   # any tag from `ollama list`
EOF
docker compose --env-file .env -f deploy/docker-compose.yml up -d
```

OpenRouter (or any OpenAI-compatible endpoint that speaks the OpenAI schema)
also needs the transport patch: the bundled runtime otherwise calls the
Anthropic Messages API and OpenRouter rejects it (`400 Invalid Anthropic
Messages API request`).

```sh
cat >> .env <<'EOF'
ENGINE_LIVE_MODE=live
DEEPSEEK_API_KEY=sk-or-...                                  # OpenRouter key
DEEPSEEK_BASE_URL=https://openrouter.ai/api/v1
DSH_MODEL=deepseek/deepseek-v4.1-flash                      # any OpenRouter model id
DSH_PATCH_FILES=/opt/harness/patches/openrouter.patch.yml
EOF
docker compose --env-file .env -f deploy/docker-compose.yml up -d
```

For a direct engine run outside Compose, point `DSH_PATCH_FILES` at
`deploy/patches/openrouter.patch.yml` instead.

### Pilot sandbox boundary

Sandboxed pilot execution can use macOS Seatbelt or Linux Landlock when the
runtime probe passes. On Linux, Landlock applies workspace/private-temp write
rules and a read/execute allow-list; paths outside that allow-list, including
sibling task workspaces, are unreadable. `auto` fails closed if the host cannot
enforce the boundary. `SANDBOX_USE_DOCKER=true` selects the explicit per-task
`DockerSandboxRunner`, which fails closed if Docker or the pre-pulled image is
unavailable. It is not enabled by default because the Compose service is a
shared backend container without a Docker socket mount. Per-task Docker also
defaults to `network_mode=none`; setting `HARNESS_SANDBOX_ALLOW_NETWORK=1`
attaches the container only to a gateway-less network whose sole peer is an
allow-list HTTPS CONNECT proxy (`section5/src/harness_sandbox/egress_proxy.py`).
That proxy path has not yet been drilled against a live Docker daemon, and a
dedicated sandbox worker deployment still needs review. This remains pilot
grade and is not a production-readiness claim.

**[docs/TUTORIAL.md](docs/TUTORIAL.md)** walks login → project → propose →
approve → live run → done-signal → events, command by command.

The stack boots as split services behind compose: frontend on **:3000**,
identity on **:8001**, github on **:8002**, models on **:8003**,
orchestrator on **:8004**, sandbox on **:8005**, meeting on **:8006**,
review on **:8008**; Postgres is internal to the compose network (host
mapping **localhost:5499** when exposed). Optional profiles add the WAL-G
backup sidecar (`walg`), events-retention sweep, scheduled VACUUM
(`maintenance`), TLS proxy (`proxy`), and the legacy all-in-one backend
(`legacy-backend`) — see `.env.example` and `deploy/docker-compose.yml`.

## Development

Python ≥3.11 venvs live inside each section; the unified gates are Makefile
targets shared byte-for-byte with CI (M13-H3b). A fresh clone has no venvs —
`make bootstrap` creates all of them (plus frontend deps) exactly as CI does
(needs Python 3.11+ on PATH and Node/npm; `make gate` also needs the `docker`
CLI for `compose config`, but no running daemon):

```sh
make bootstrap              # one-time: 8 section venvs + section7 node_modules
make test-section1          # cd section1 && ./.venv/bin/python -m pytest tests/ -q
make lint-section1          # cd section1 && ./.venv/bin/ruff check src tests [scripts]
make gate                   # static-gates + lint + test + frontend (what PRs must pass)
make e2e                    # section7 Playwright hero-journey (needs the compose stack)
make pg-probe               # real-Postgres integration probe (needs HARNESS_TEST_PG=1)
```

Frontend (section7, Next.js): `npm run dev` / `npm run typecheck` /
`npm run test` (vitest) / `npm run e2e` (Playwright).
Schema changes are new numbered migrations in `section1/migrations/`
(0001 is the frozen baseline — never edit an applied migration; pick the
next free number, checking `ls section1/migrations/` first). You normally
never apply migrations by hand: the compose `migrate` one-shot service runs
the whole ledger on every stack boot. Migrations are forward-only (no down
scripts): "rolling back" the schema means restoring a backup, see
[`docs/runbooks/rollback.md`](docs/runbooks/rollback.md). To prove the whole
chain on a throwaway Postgres: `HARNESS_TEST_PG=1 make pg-probe-migrate`
(needs a Postgres reachable per `PG_URL` in the Makefile, default
`localhost:5499`).

## Terminology

These names are canonical in all user-facing docs and UI copy. Use them
consistently; do not introduce synonyms. Historical documents and audit
archives are not retroactively rewritten. Full definitions as implemented:
**[docs/GLOSSARY.md](docs/GLOSSARY.md)**.

| Canonical term | Meaning | Do not use |
|---|---|---|
| **engine** | The Section 4 component that executes an approved plan against a model provider (`section4/src/harness_orchestrator/engine.py`), configured by `ENGINE_LIVE_MODE` / `DEEPSEEK_*` / `DSH_*`. | "agent harness", "the harness" (that's this whole platform), "model router" (that's S3) |
| **DeepSeek Harness (dsh)** | The *external* upstream product/SDK the engine integrates with — used only when referring to that third-party software or its concepts. | "dsh" as a name for our engine or platform |
| **run** | One end-to-end execution of an approved plan (status lifecycle, queue claim, events). | "job", "task", "execution" |
| **team** | The org unit owning projects, members, budgets, and API keys. | "org", "organization", "workspace" |
| **Meeting Area** | The section 6 meeting surface where briefs are drafted. | "meeting", "huddle" |
| **Review Area** | The section 8 review gate where draft PRs are approved. | "review", "review page" |

Internal service codes (`S1`…`S10`) are for developers only — never render
them to end users.
