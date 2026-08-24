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
| `make static-gates` | `sh -n deploy/entrypoint.sh` · `docker compose -f deploy/docker-compose.yml config -q` · `python3 contracts/ts/generate.py --check` · M15-G6a law guards (below) | `static-gates` | Three tool steps carry the same three commands verbatim; kept stepwise only because runner tooling differs per action image. Since M15-G6a the aggregate ALSO runs the two law-guard targets below first. |
| `make guard-routes` | `bash section7/scripts/ci_route_module_guard.sh routes` | `static-gates` → route/isolation laws step (M15-G6a) | Route-module law: every `section*/app/**/page.tsx` default-export-only (W4 carryover #4). |
| `make guard-isolation` | `bash section7/scripts/ci_route_module_guard.sh isolation` | `static-gates` → route/isolation laws step (M15-G6a) | Section isolation: imports cross `sectionN→sectionM` boundaries fail; `contracts/` is the only legal shared dependency. |
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

## Route-module + isolation law guards (M15-G6a)

Two architectural laws that used to live only in convention are now static
gates, enforced by one POSIX-sh script with two modes:

| Guard | Law | Fails when |
|---|---|---|
| `make guard-routes` | **Route-module law** (W4 carryover #4) | any `section*/app/**/page.tsx` contains a non-default export (`export const metadata = …`, helpers, types, …). Next.js route modules must default-export ONLY — this is the index-signature violation class that cost us a Wave-4 fix cycle. Move extras into a sibling module and import them. |
| `make guard-isolation` | **Section-isolation law** (AGENTS.md layout rules; M13 audit) | code in `sectionN/` imports from `sectionM/` (N≠M), either Python-style (`from section1.models import …`) or module-specifier-style (`'../section2/foo'`). Sections share via `contracts/` alone (the `harness_contracts` package or its `@contracts/*` TS mirror); cross-section data reads go through raw SQL against frozen table names. |

Behavior both modes share:

- Violations print `file:line` plus the offending source line and the law
  text, then the target exits 1 — CI goes red.
- Exit codes: 0 clean · 1 violations · 2 usage/internal error (a broken or
  missing guard script can never silently pass: every Makefile invocation
  first runs `sh -n` on the script).
- Scope: all `section*/app/**/page.tsx` (routes mode); all source files under
  `section*/` excluding `node_modules`, `.next`, venvs, build dirs (isolation
  mode). `contracts/` itself and `integration/` checkpoint tests are outside
  the isolation guard on purpose. A section naming its own tree is legal.
- CI wiring: ONE step in the `static-gates` job runs both targets back to
  back; `make static-gates` (and therefore `make gate`) includes them too.

Trip-test evidence (violation planted, guard run, fixture removed):

```console
$ printf 'export const metadata = { title: %s };\n\nexport default function TripTestPage() {\n\treturn <main>x</main>;\n}\n' "'trip-test'" > section7/app/trip-test/page.tsx
$ make guard-routes; echo "exit=$?"
GUARD[routes] FAIL: section7/app/trip-test/page.tsx:1: non-default export in route module: export const metadata = { title: 'trip-test' };
                route modules (app/**/page.tsx) may only 'export default'
                ...
GUARD[routes] RESULT: 1 violation(s) across 16 route module(s).
exit=1

$ printf 'from section1.models import Project  # noqa: F401\n' > section3/tests/test_guard_trip.py
$ make guard-isolation; echo "exit=$?"
GUARD[isolation] FAIL: section3/tests/test_guard_trip.py:4: cross-section import: from section1.models import Project  # noqa: F401
                'section3' references 'section1' — sections may depend ONLY on contracts/
                ...
GUARD[isolation] RESULT: 1 violation(s) across 374 source file(s) in 9 section(s).
exit=1
```

Both fixtures were deleted immediately after; clean-tree runs print
`GUARD[routes] OK: 15 route module(s)` / `GUARD[isolation] OK: ~372 source
file(s)` with exit 0.


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
