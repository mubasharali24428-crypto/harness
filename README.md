# AI Engineering Harness

Self-hosted platform where AI coding agents do real engineering work under
human governance: mandatory human plan approval, sandboxed execution,
event-sourced audit log, **manual merges only** — no auto-merge anywhere.

- New operator? Start with **[`deploy/ONBOARDING.md`](deploy/ONBOARDING.md)**:
  `cp .env.example .env`, then `docker compose -f deploy/docker-compose.yml up -d`.
  `.env.example` documents every environment variable (name, purpose, default,
  required-or-optional).
- Notable changes per release: **[CHANGELOG.md](CHANGELOG.md)**.
- Current status, wave history, and known gaps: **[STATUS.md](STATUS.md)**.
- Builder/agent rules (frozen contracts, migration law): **[AGENTS.md](AGENTS.md)**.
- REST/WS conventions, `/api/v1` versioning, deprecation policy:
  **[docs/api-versioning.md](docs/api-versioning.md)** (normative source:
  [`contracts/api-conventions.md`](contracts/api-conventions.md)).
- Project vocabulary as implemented: **[docs/GLOSSARY.md](docs/GLOSSARY.md)**.
- Operations runbooks: **[docs/runbooks/](docs/runbooks/)**
  (`ops.md`, `backup-and-pitr.md`, `degraded-mode.md`) and maintenance
  schedules (**[docs/runbooks/maintenance.md](docs/runbooks/maintenance.md)**).
- CI/local gates (`make gate`): **[docs/ci-gates.md](docs/ci-gates.md)**;
  DB connection budget: **[docs/pool-math.md](docs/pool-math.md)**.
- Layout of the ten sections + contracts package: see the tree in **[AGENTS.md](AGENTS.md)**.

## Quick start

```sh
cp .env.example .env        # then set HARNESS_MASTER_KEY=$(openssl rand -hex 32)
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs identity | grep HARNESS-OTP   # dev OTP codes
open http://localhost:3000
```

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
targets shared byte-for-byte with CI (M13-H3b):

```sh
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
next free number, checking `ls section1/migrations/` first). Apply the
frozen baseline with
`psql -q harness_fresh -f migrations/0001_initial.sql` from `section1/`.

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
