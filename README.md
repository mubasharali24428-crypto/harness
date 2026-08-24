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
- Operations runbooks: **[docs/runbooks/](docs/runbooks/)** (backup & PITR).
- Layout of the ten sections + contracts package: see the tree in **[AGENTS.md](AGENTS.md)**.

## Terminology

These names are canonical in all user-facing docs and UI copy. Use them
consistently; do not introduce synonyms. Historical documents and audit
archives are not retroactively rewritten.

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
