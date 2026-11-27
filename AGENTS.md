# AGENTS.md — AI Engineering Harness monorepo

Builders (human or agent): read this before touching anything. Contracts are FROZEN —
see `contracts/`. Breaking a contract without a version bump + mock updates in the same
commit is the one unforgivable sin here.

## Layout

```
harness/
├── contracts/          # FROZEN shared package (owned by Section 1)
│   ├── src/harness_contracts/   # events, brief, plan, roles, signatures, api
│   ├── api-conventions.md       # REST/WS/auth rules every section MUST follow
│   └── ts/types.d.ts            # TS mirror (v0.2.0) for frontend sections
├── section1/           # Data & Identity: schema, auth, audit, event bus delivery
├── section2/ … section10/  # one folder per section; import ONLY harness_contracts
├── task0/              # DeepSeek Harness verification (PASSED 2026-08-21)
└── deploy/             # Section 10 assembly point
```

## Docs map (X-05 diet, 2026-11-22)

Live documents: `README.md` (start here) · `STATUS.md` (current readiness only —
history split to the archive) · `FUTURE PLAN 2.0.md` (the working plan, incl. §20 10X
program) · `docs/briefs/PROJECT-STATE.md` · `docs/TUTORIAL.md` · `docs/runbooks/` ·
`docs/ci-gates.md` · `docs/GLOSSARY.md`.

Superseded plans and historical status entries live under `docs/archive/` — read-only
history, never edited: `FUTURE PLAN.md`, `roadmap-3.0.md`,
`90-day-execution-plan-2026-11-11.md`, the 24 `m2-w*-plan.md` legs, and
`STATUS-history-to-2026-11-06.md`.

## Commands (Section 1)

Run from the repo root (paths are explicit, so nothing depends on your cwd):

```sh
cd section1
./.venv/bin/python -m pytest tests/ -q     # tests (must pass before any PR)
./.venv/bin/ruff check src tests scripts    # lint (must be clean)
docker compose --env-file .env -f deploy/docker-compose.yml up -d migrate
                                            # apply migration ledger (one-shot `migrate`; run from repo root)
```

## Rules recap (full list in HARNESS_PLAN_V2.md)

- No auto-merge anywhere. No forced model downgrade. No per-keystroke analysis.
- Secrets never leave Section 1/3 storage or enter logs/model context.
- Nothing outside Section 5's allow-list executes; forbidden_paths are hard constraints.
- Schema changes = new numbered migration in `section1/migrations/`, never an edit to 0001.
- Event bus: Section 4 owns semantics, Section 1 owns delivery (`agent_events` persistence + replay).
- Every section ships golden-fixture conformance tests against `harness_contracts`.

## Environment

- Python ≥3.11 venvs per section; Postgres 18 local (`harness_dev`); Docker available.
- DeepSeek Harness: MIT, official PyPI SDK `deepseek-harness-sdk`, verified working with
  local Ollama endpoints (`DEEPSEEK_BASE_URL=http://127.0.0.1:11434/v1`). Pin the SDK
  version; re-run `task0/hello.py` on every bump.


<claude-mem-context>
# Memory Context

# [harness] recent context, 2026-11-02 6:41am PST

No previous sessions found.
</claude-mem-context>