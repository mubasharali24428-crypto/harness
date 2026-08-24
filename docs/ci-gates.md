# CI gates — local/CI parity contract (M13-H3b)

> **Parity guarantee:** every gate CI enforces is a Makefile target, and the
> `.github/workflows/ci.yml` job steps invoke those targets **verbatim**
> (`make test-section4`, `make lint-section2`, …). The gate commands are
> *defined once* — in `Makefile` — so "it passed locally" and "CI passed" can
> no longer be two different statements. Drift is structurally impossible:
> there is no second copy of any gate command to drift.

## The one-command gate

```sh
make gate    # = static-gates + lint + test + frontend (everything a PR blocks on)
```

`make e2e` joins this list when lane H3a lands the Playwright specs
(pair contract H-P2: H3b defines the target, H3a consumes it — it already
works end-to-end and fails loudly with exit 2 while specs are absent).

## Target → CI job mapping

| Makefile target | What runs | CI job / step | Notes |
|---|---|---|---|
| `make static-gates` | `sh -n deploy/entrypoint.sh` · `docker compose -f deploy/docker-compose.yml config -q` · `python3 contracts/ts/generate.py --check` | `static-gates` | Three steps carry the same three commands verbatim; kept stepwise only because runner tooling differs per action image. |
| `make lint-section<N>` | `cd sectionN && ./.venv/bin/ruff check src tests scripts \|\| ./.venv/bin/ruff check src tests` | `python-tests` matrix → ruff step | The `\|\|` fallback mirrors the original CI line: sections without a `scripts/` tree would fail ruff on the missing path. |
| `make test-section<N>` | `cd sectionN && ./.venv/bin/python -m pytest tests/ -q` | `python-tests` matrix → pytest step | Uses each section's OWN venv (editable `harness_contracts` + section) — same as CI's provisioning step builds. N ∈ {1,2,3,4,5,6,8,10}, CI matrix order. |
| `make frontend-tsc` | `cd section7 && npx tsc --noEmit` | `frontend` → tsc step | |
| `make frontend-vitest` | `cd section7 && npx vitest run` | `frontend` → vitest step | `make frontend` runs both halves. |
| `make pg-probe-integration` | real-PG checkpoint probe (`integration/test_checkpoint1.py`) | `nightly-pg-probe` (schedule/dispatch only) | Requires `HARNESS_TEST_PG=1` + Postgres on :5499 (compose stack); the target refuses to fake-run without them. |
| `make pg-probe-s1` | S1 suite against real PG | `nightly-pg-probe` (schedule/dispatch only) | Same env contract as above. |
| `make pip-audit` | pip-audit over the S1 venv | `security-scan` → pip-audit step | WARN-posture this wave (`continue-on-error: true`). gitleaks stays a blocking action and has no make equivalent by design. |
| `make e2e` | `cd section7 && npx playwright test` | *(CI job joins via ATLAS when the suite stabilizes)* | Live since H3a's scaffolding landed: requires the compose stack (frontend :3000); exits **246** (skip, distinguishable from failure) if the stack health-check fails. |

Aggregators: `make lint`, `make test`, `make frontend`, `make pg-probe`,
`make gate`. Per-section targets exist so you can iterate exactly like the CI
matrix does: `make test-section6`, `make lint-section8`, …

## Provisioning vs gating

Only *setup* stays native to CI (venv creation, `npm ci`, pip installs,
service containers). That is deliberate: it is environment-specific and cannot
drift semantically. Every command whose result decides red/green lives in the
Makefile.

Local venvs must exist first (one-time per machine):

```sh
for s in 1 2 3 4 5 6 8 10; do
  (cd section$s && python3 -m venv .venv \
    && ./.venv/bin/pip install -q -e ../contracts \
    && ./.venv/bin/pip install -q -e ".[dev]" || ./.venv/bin/pip install -q -e .)
done
(cd section7 && npm ci --no-audit --no-fund)
```

## Nightly PG probe locally

```sh
docker compose -f deploy/docker-compose.yml up -d db   # host port 5499
HARNESS_TEST_PG=1 make pg-probe                        # refuses to run without the flag
```

## Version-pin conformance (revived under this lane)

`section1/tests/test_version_pin_conformance.py` revives the dead
`test_contracts_version_frozen()` that used to live in `conftest.py`
(pytest never collects tests from conftest — audit F6; its literal had also
drifted to `0.1.0`). It now asserts all FOUR version stamps agree:

1. `importlib.metadata.version("harness-contracts")` (release truth per raw-23)
2. `contracts/pyproject.toml [project].version`
3. `contracts/src/harness_contracts/__init__.py __version__`
4. `contracts/ts/types.d.ts` header comment

Failure policy: source-tree divergence ⇒ hard FAIL listing every stamp;
stale installed metadata from a *local* editable install of this tree ⇒ loud
warning (provisioning lag; heal with `./.venv/bin/pip install -e ../contracts`);
foreign stale artifact ⇒ hard FAIL. Consumer floor pins
(`section{2,8}: harness-contracts>=X`) are checked satisfiable too.
A contracts bump moves ALL stamps in ONE commit (AGENTS.md atomicity law);
the generated TS files stay honest via the `--check` static gate above.

## Parity gaps found during convergence (H-024/H-025 sweep)

- **Dead pin assertion** (fixed): `conftest.py::test_contracts_version_frozen`
  never ran; revived as the 4-way detector above.
- **Live version rot at lane start** (healed): `__init__.py`=0.2.0 vs
  `pyproject.toml`=0.3.0 vs TS header=v0.2.0 — exactly what the new detector
  catches; aligned to 0.3.0 in one commit's worth of edits.
- **Stale editable dist metadata** (documented): all eight local section venvs
  still carried `harness-contracts 0.2.0` stamps after the source bump; the
  detector warns instead of failing since CI installs fresh. Re-run
  `pip install -e ../contracts` per section to refresh stamps.
- No other drift between the pre-refactor ci.yml steps and the Makefile
  recipes was found: commands were transplanted verbatim.
