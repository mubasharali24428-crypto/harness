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
| `make static-gates` | `sh -n deploy/entrypoint.sh` · `docker compose -f deploy/docker-compose.yml config -q` · `python3 contracts/ts/generate.py --check` · `python3 scripts/spec_drift.py` · `python3 scripts/eval/run_eval.py --check` · `python3 scripts/check_env_knobs.py` · M15-G6a law guards (below) | `static-gates` | Five steps carry five of the six recipe commands verbatim; kept stepwise only because runner tooling differs per action image. Since M15-G6a the aggregate ALSO runs the two law-guard targets below first. **Known gap:** the recipe's `spec_drift.py` line has no CI step — it imports the FastAPI apps, so it needs a section venv this job does not build; it gates `make static-gates` / `make gate` locally. |
| `make guard-routes` | `bash section7/scripts/ci_route_module_guard.sh routes` | `static-gates` → route/isolation laws step (M15-G6a) | Route-module law: every `section*/app/**/page.tsx` default-export-only (W4 carryover #4). |
| `make guard-isolation` | `bash section7/scripts/ci_route_module_guard.sh isolation` | `static-gates` → route/isolation laws step (M15-G6a) | Section isolation: imports cross `sectionN→sectionM` boundaries fail; `contracts/` is the only legal shared dependency. |
| `make lint-section<N>` | `cd sectionN && ./.venv/bin/ruff check src tests scripts \|\| ./.venv/bin/ruff check src tests` | `python-tests` matrix → ruff step | The `\|\|` fallback mirrors the original CI line: sections without a `scripts/` tree would fail ruff on the missing path. |
| `make test-section<N>` | `cd sectionN && ./.venv/bin/python -m pytest tests/ -q` | `python-tests` matrix → pytest step | Uses each section's OWN venv (editable `harness_contracts` + section) — same as CI's provisioning step builds. N ∈ {1,2,3,4,5,6,8,10,11}, CI matrix order. |
| `make test-mcp` / `make lint-mcp` | `cd mcp && pytest` / ruff | `mcp` job | MCP server is not a section; contracts are installed first, same as bootstrap. |
| `make test-language` / `make lint-language` | `cd packages/language && ./.venv/bin/python -m pytest tests -q` / `./.venv/bin/ruff check src tests scripts` | `language` job | Hx (Harness dialect of Hermes): the checker (`hxc`) and the workflow driver (`hxr`). Not a section: `packages/language` installs `../../contracts` first, same as bootstrap. Spec: `docs/hx-language-spec.md`; why two halves: `docs/hx-execution-architecture.md`. |
| `make frontend-tsc` | `cd section7 && npx tsc --noEmit` | `frontend` → tsc step | |
| `make frontend-vitest` | `cd section7 && npx vitest run` | `frontend` → vitest step | `make frontend` runs both halves. |
| `make pg-probe-integration` | real-PG checkpoint probe (`integration/test_checkpoint1.py`) | `nightly-pg-probe` (schedule/dispatch only) | Requires `HARNESS_TEST_PG=1` + Postgres on :5499 (compose stack); the target refuses to fake-run without them. Nightly passes `HARNESS_TEST_PG_EXTERNAL=1` + `HARNESS_TEST_PG_CONTAINER=<service id>` so `pg-up` is skipped — the job's own service container already owns :5499. |
| `make pg-probe-s1` | S1 suite against real PG | `nightly-pg-probe` (schedule/dispatch only) | Same env contract as above. |
| `make pg-probe-migrate` | `deploy/runners/migrate_selftest.py`: executes **deploy/runners/migrate.py itself** against real PG on a per-run scratch database — full disk chain must apply clean (0001→latest) and a second pass must be a no-op | `migrations-pg` (every PR + push) | M17-B2 / OPS-15: before this gate the migration runner had never been executed by CI — suites used SQLite `create_all`, even the nightly probe built schema via ORM. Same env guard as the other pg-probes (`HARNESS_TEST_PG=1`, Postgres on :5499); never touches the live cluster's data. |
| `make pip-audit` | pip-audit over the S1 venv | `security-scan` → pip-audit step | WARN-posture this wave (`continue-on-error: true`). gitleaks stays a blocking action and has no make equivalent by design. |
| `make` (no target) | `./scripts/native_linux_drill.sh`: builds the backend image, runs the engine shell gate in a container with a bind-mounted workspace | `native-linux-container` (master pushes + dispatch only) | Not a PR gate: it builds an image and needs 20+ minutes on a cold cache. Proves the Landlock write path on a real Linux kernel and that a sibling directory is unreadable; uploads the evidence JSON. |
| `make e2e` | `cd section7 && npx playwright test` | *(CI job joins via ATLAS when the suite stabilizes)* | Live since H3a's scaffolding landed: requires the compose stack (frontend :3000); exits **246** (skip, distinguishable from failure) if the stack health-check fails. |
| `make e2e-compose` | `python3 scripts/e2e/compose_journey.py` — 12-check zero-key journey against the booted stack: readiness · passwordless login · demo seed (+idempotency) · run aggregate · engine `replayed` truth · event replay · audit · sandbox create→exec→destroy · cross-tenant no-oracle 404s · metrics families · frontend HTML | `e2e-compose` (every PR + push + dispatch) | OPS-20 release gate. The job boots the real compose stack with a generated `HARNESS_MASTER_KEY` (`openssl rand -hex 32` — migrate refuses an empty secret by design), verifies the migrate one-shot exited 0 before the journey, uploads compose logs as an artifact on failure, and always tears down with `down -v`. Catches live-only bug classes the SQLite/macOS lanes structurally cannot: it found **M18-C8** (demo-seed FK insert order on PG) and **M18-C9** (landlock version-heuristic vs. Docker seccomp — every in-container exec died in preexec_fn). |

## Nightly-only coverage (`schedule` / `workflow_dispatch`)

The `nightly-pg-probe` job is the only lane with a real Postgres service and
sibling venvs, so it is where the deliberately-skipped suites actually run.
Normal PR jobs skip these with an honest `reason=` string; nothing is silently
green.

| Step | Runs | Why it cannot be a PR gate |
|---|---|---|
| `make pg-probe-integration` | `integration/test_checkpoint1.py` — fanout + rehydration against real PG | needs `HARNESS_TEST_PG=1` and a migrated PG instance |
| `make pg-probe-s1` | whole section1 suite with `HARNESS_TEST_PG=1` | ~40 PG-marked tests (`HARNESS_TEST_PG not set` on PRs) |
| `pytest tests/unit -k live_pg` (section4) | every `*_live_pg.py` probe — session 4/5/6, collab chaos, approvals, race codes, budget delivery, workspace jobs, cost rollup, demo seed, purge/lease sweep | each builds its own scratch database via `docker exec psql` |
| `pytest tests/test_live_pg.py` (section11) | context index live-PG probe | spins up its own `pgvector/pgvector:pg18` container on :5591 |
| `pytest tests/test_context_resolver.py -k roundtrip` (section4) | the @refs round-trip against the REAL section11 app over HTTP | needs the section11 venv, which only this job installs |

Still environment-gated on purpose (no lane can satisfy them everywhere, and
they are not coverage holes): landlock tests (`sys.platform != "linux"`),
Seatbelt tests (`macOS sandbox-exec` only), `HARNESS_P0_DOCKER` /
`HARNESS_P0_LIVE` opt-ins, `promtool`-dependent alert-rule checks, and
provider-key live tests. On `ubuntu-latest` the landlock half runs for real in
`pytest (section5)`; the Seatbelt half runs on a macOS contributor machine.

Aggregators: `make lint`, `make test`, `make frontend`, `make pg-probe`,
`make gate`. Per-section targets exist so you can iterate exactly like the CI
matrix does: `make test-section6`, `make lint-section8`, … Non-section packages
have their own pair (`lint-mcp`/`test-mcp`, `lint-language`/`test-language`) and
both pairs are part of `make gate`.

## Route-module + isolation law guards (M15-G6a)

Two architectural laws that used to live only in convention are now static
gates, enforced by one POSIX-sh script with two modes:

| Guard | Law | Fails when |
|---|---|---|
| `make guard-routes` | **Route-module law** (W4 carryover #4) | any `section*/app/**/page.tsx` contains a non-default export (`export const metadata = …`, helpers, types, …). Next.js route modules must default-export ONLY — this is the index-signature violation class that cost us a Wave-4 fix cycle. Move extras into a sibling module and import them. |
| `make guard-isolation` | **Section-isolation law** (AGENTS.md layout rules; M13 audit; M17-B3 upgrade) | code in `sectionN/src` imports from `sectionM/` (N≠M) in ANY of four shapes: (1) literal tree imports (`from section1.models import …`, `'../section2/foo'`), (2) **package-name imports** (`from harness_identity.timeutil import …` from outside section1 — the P-BETA C-1 class: 17 production files did this invisibly), (3) **runtime sys.path seams** (`sys.path.append("../sectionN/src")` inside another section's src), or (4) **star-re-export chains** (`from harness_X… import *`). Sections share via `contracts/` alone (`harness_contracts` / `@contracts/*`); cross-section data reads go through raw SQL against frozen table names. |

Behavior both modes share:

- Violations print `file:line` plus the offending source line and the law
  text, then the target exits 1 — CI goes red.
- Exit codes: 0 clean · 1 violations · 2 usage/internal error (a broken or
  missing guard script can never silently pass: every Makefile invocation
  first runs `sh -n` on the script).
- Scope: all `section*/app/**/page.tsx` (routes mode); isolation mode runs
  the literal-tree rule over every source file under `section*/`, while the
  package-name / sys.path / star-re-export rules cover **section src trees**
  (`section*/src`) — tests may reach a sibling's internals to pin cross-
  section parity (the G3 caps-parity and G2 limiter tests do exactly that);
  production coupling may not. `contracts/` itself, `integration/`
  checkpoint tests, `deploy/` (the sanctioned multi-src assembler),
  `scripts/`, `task0/` and `debug_canon/` are outside the isolation guard on
  purpose. A section naming its own package/tree is legal.
- CI wiring: ONE step in the `static-gates` job runs both targets back to
  back; `make static-gates` (and therefore `make gate`) includes them too.

Trip-test evidence for the M17-B3 upgrade (violation planted into
`section6/src/harness_meeting/summary_service.py`, guard run, fixture
restored from backup — real transcripts):

```console
$ printf '\nfrom harness_identity.timeutil import now_utc\n' >> \
    section6/src/harness_meeting/summary_service.py
$ sh section7/scripts/ci_route_module_guard.sh isolation; echo "EXIT=$?"
GUARD[isolation] FAIL: section6/src/harness_meeting/summary_service.py:75: package-name import: from harness_identity.timeutil import now_utc
                this harness_* package belongs to another section — sections depend ONLY on
                harness_contracts. Promote shared code into contracts/ (version bump + TS
                mirror regen) or inline the needed logic locally (M17-B3 / P-BETA C-1).
GUARD[isolation] RESULT: 1 violation(s) across 144 src file(s) in 9 section(s).
EXIT=1

$ printf '\n_s.path.append(str(_R / "section1" / "src"))\n' >> \
    section6/src/harness_meeting/summary_service.py   # after the same imports
$ sh section7/scripts/ci_route_module_guard.sh isolation; echo "EXIT=$?"
GUARD[isolation] FAIL: section6/src/harness_meeting/summary_service.py:78: sys.path seam:     _s.path.append(str(_R / "section1" / "src"))
                runtime sys.path injection of a sibling section's src tree defeats both the
                ...
EXIT=1
```

Both fixtures were reverted immediately after (`cp backup back`; verified by
an immediate green re-run). The pre-M17-B3 literal-tree trip-test in the
console block above remains valid for rule 1.


## Provisioning vs gating

Only *setup* stays native to CI (venv creation, `npm ci`, pip installs,
service containers). That is deliberate: it is environment-specific and cannot
drift semantically. Every command whose result decides red/green lives in the
Makefile.

Local venvs must exist first (one-time per machine). Since M17-B6 a single
target provisions them exactly as CI does — contracts first (path dependency),
then each section with dev extras where declared, plus `npm ci` for section7:

```sh
make bootstrap
```

The equivalent manual loop (kept here so CI setup steps stay auditable):

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
