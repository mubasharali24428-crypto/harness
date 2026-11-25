# Harness monorepo — unified CI/local gates (M13-H3b, closes H-024 + H-025).
#
# PARITY CONTRACT: every command CI runs is defined HERE and nowhere else.
# .github/workflows/ci.yml jobs call these targets verbatim (`make test-section1`,
# `make lint-section3`, ...) so local and CI literally execute the same lines —
# drift between "what passed locally" and "what CI ran" is structurally impossible.
# Provisioning (venv creation, npm ci, pip installs) stays in CI as setup steps;
# the GATES themselves live only in this file. Full mapping: docs/ci-gates.md.
#
#   make bootstrap       one-time contributor setup: per-section venvs + frontend deps
#   make lint            ruff across all backend sections (CI matrix order)
#   make test            pytest matrix, one venv per section (CI matrix order)
#   make frontend        section7 tsc --noEmit + vitest run
#   make static-gates    entrypoint.sh parse + compose config + TS mirror sync
#                        + M15-G6a route/isolation law guards
#   make e2e             section7 Playwright hero-journey (specs: lane H3a)
#   make pg-probe        nightly parity: real-PG integration + S1 suite (needs PG)
#   make gate            EVERYTHING a PR must pass (static-gates lint test frontend
#                        + the mcp and language package gates)
#
# Per-section granular targets (used by CI matrix jobs): make test-section<N>,
# make lint-section<N> for N in 1 2 3 4 5 6 8 10 11. Non-section packages keep
# their own pair: make lint-mcp/test-mcp and make lint-language/test-language.

SECTIONS := section1 section2 section3 section4 section5 section6 section8 section10 section11
FRONTEND := section7

# CI pins python-version "3.13" (ci.yml). Locally, use the newest interpreter
# that satisfies every section's requires-python (>=3.11); fall back to `python3`.
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)

# Nightly probe DSN — identical to the ci.yml service container mapping (5499->5432).
PG_URL ?= postgresql+asyncpg://harness:harness@localhost:5499/harness

.PHONY: help bootstrap clean-venvs guard-script-check \
        gate $(foreach s,$(SECTIONS),lint-$(s) test-$(s)) \
        lint test lint-mcp test-mcp lint-language test-language frontend frontend-tsc frontend-vitest check-ts-dist static-gates e2e e2e-compose \
        pg-probe pg-probe-integration pg-probe-s1 pg-probe-migrate pg-up pip-audit \
        guard-routes guard-isolation guard-route-law \
        drill-oidc drill-pitr drill-native drill-fairness drill-rate-limit drill-routing drill-seat-revocation drill-scim drill-siem ci-local \
        dev-otp test-all

help:
	@echo "Harness gates — single source of truth shared by CI (.github/workflows/ci.yml):"
	@echo "  make bootstrap       one-time contributor setup (venvs + frontend deps)"
	@echo "  make lint            ruff across: $(SECTIONS)"
	@echo "  make test            pytest matrix (same sections, CI order)"
	@echo "  make frontend        section7: tsc --noEmit + vitest run"
	@echo "  make check-ts-dist   clients/ts/dist matches a fresh build (needs npm ci)"
	@echo "  make static-gates    sh -n entrypoint.sh; compose config; TS mirror --check"
	@echo "                       + route/isolation law guards (M15-G6a)"
	@echo "  make drill-oidc      OIDC SSO against a disposable Keycloak (evidence JSON)"
	@echo "  make drill-pitr      WAL-G PITR against a disposable MinIO bucket (evidence JSON)"
	@echo "  make drill-native    container tool drill; requires a native Linux host"
	@echo "  make drill-fairness   W15 admission fairness on the compose Postgres (no spend)"
	@echo "  make drill-rate-limit W15 per-team budget; needs a small deployed team limit"
	@echo "  make drill-routing    W15 routing decision is durable + readable (no spend)"
	@echo "  make drill-seat-revocation W16 revoked seat refused on every live door"
	@echo "  make drill-scim         W16 SCIM deprovision reaches every live door"
	@echo "  make drill-siem         W16 SIEM push: signed delivery, replay, block/resume, verdict"
	@echo "  make guard-routes    app/**/page.tsx default-export-only law (W4 carryover)"
	@echo "  make guard-isolation sections import ONLY contracts/ (M13 isolation law)"
	@echo "  make e2e             section7 Playwright suite (specs land via lane H3a)"
	@echo "  make e2e-compose     zero-key compose journey, 12 checks (needs stack + master key)"
	@echo "  make pg-probe        real-PG probes: integration checkpoint + S1 suite + migrate runner (needs PG :5499)"
	@echo "  make dev-otp         print the latest dev login codes from the identity logs (X-03)"
	@echo "  make test-all        provision + run the flag-gated test groups (PG, Docker P0, promtool, landlock); live/paid stay opt-in (X-02)"
	@echo "  make pip-audit       dependency audit (WARN-posture parity with CI)"
	@echo "  make gate            static-gates + lint + test + lint-mcp/test-mcp + lint-language/test-language + frontend"
	@echo "  make lint-section<N> / test-section<N>   one section, exactly CI's command"
	@echo "  make lint-language / test-language      packages/language (Hx checker), exactly CI's command"

# ------------------------------------------------------------------ bootstrap
# F-1 (M17-B6): a fresh clone ships no .venv anywhere — the very first
# documented gate (`make test-section1`) died on `./.venv/bin/python: No such
# file or directory`. This is the one command that provisions what CI does in
# its setup steps, using CI's own recipe (contracts first — it is a path
# dependency — then the section itself; dev extras when declared). Idempotent:
# re-run any time; pip treats satisfied requirements as no-ops. Frontend deps
# come from the committed lockfile via npm ci.
bootstrap:
	@echo "== bootstrap: python = $(PYTHON) =="
	@for s in $(SECTIONS); do \
		echo "== $$s =="; \
		cd $$s && $(PYTHON) -m venv .venv && \
			./.venv/bin/pip install --quiet --upgrade pip && \
			{ ./.venv/bin/pip install --quiet -e ../contracts && \
			  { ./.venv/bin/pip install --quiet -e ".[dev]" || \
			    ./.venv/bin/pip install --quiet -e .; }; } && cd .. || exit 1; \
	done
	@if [ -f "$(FRONTEND)/package-lock.json" ]; then \
		echo "== $(FRONTEND) (npm ci) =="; \
		cd $(FRONTEND) && npm ci --no-audit --no-fund || exit 1; \
	else \
		echo "== $(FRONTEND): package-lock.json not found, skipping npm ci =="; \
	fi
	@echo "== mcp =="; \
		cd mcp && $(PYTHON) -m venv .venv && \
			./.venv/bin/pip install --quiet --upgrade pip && \
			./.venv/bin/pip install --quiet -e ../contracts && \
			{ ./.venv/bin/pip install --quiet -e ".[dev]" || \
			  ./.venv/bin/pip install --quiet -e .; } && cd .. || exit 1
	@echo "== packages/language (Hx) =="; \
		cd packages/language && $(PYTHON) -m venv .venv && \
			./.venv/bin/pip install --quiet --upgrade pip && \
			./.venv/bin/pip install --quiet -e ../../contracts && \
			{ ./.venv/bin/pip install --quiet -e ".[dev]" || \
			  ./.venv/bin/pip install --quiet -e .; } && cd ../.. || exit 1
	@echo ""
	@echo "Bootstrap complete. Try: make test-section1"

clean-venvs:
	@for s in $(SECTIONS); do rm -rf $$s/.venv; done
	@rm -rf mcp/.venv packages/language/.venv
	@echo "Removed all section/package venvs. Re-run 'make bootstrap' to recreate."

# ---------------------------------------------------------------- static gates
# M15-G6a added the route-module + isolation law guards (guard-route-law) to
# this aggregate so `make gate` locally covers byte-for-byte what the CI
# static-gates JOB enforces on PRs (the job lists the same commands stepwise).
static-gates: guard-route-law
	sh -n deploy/entrypoint.sh
	docker compose -f deploy/docker-compose.yml config -q
	python3 contracts/ts/generate.py --check
	section4/.venv/bin/python scripts/spec_drift.py
	python3 scripts/eval/run_eval.py --check
	python3 scripts/check_env_knobs.py

# ------------------------------------------------------------------------ lint
# Sections WITH a scripts/ tree get the three-path check; sections without
# get src+tests only (ruff exits nonzero on a missing path). Same expression
# locally and in CI — no third variant allowed.
define LINT_RECIPE
cd $(1) && if [ -d scripts ]; then ./.venv/bin/ruff check src tests scripts; else ./.venv/bin/ruff check src tests; fi
endef

lint:
	@for s in $(SECTIONS); do \
		echo "== ruff $$s =="; \
		$(MAKE) --no-print-directory lint-$$s || exit 1; \
	done

lint-section1:
	$(call LINT_RECIPE,section1)
lint-section2:
	$(call LINT_RECIPE,section2)
lint-section3:
	$(call LINT_RECIPE,section3)
lint-section4:
	$(call LINT_RECIPE,section4)
lint-section5:
	$(call LINT_RECIPE,section5)
lint-section6:
	$(call LINT_RECIPE,section6)
lint-section8:
	$(call LINT_RECIPE,section8)
lint-section10:
	$(call LINT_RECIPE,section10)
lint-section11:
	$(call LINT_RECIPE,section11)

# ----------------------------------------------------------------------- tests
# Exactly CI's matrix command per section: the section's OWN venv interpreter
# (editable contracts + section install) running `python -m pytest tests/ -q`.
# Sections run in the ci.yml matrix order; first failure stops the run.
define TEST_RECIPE
cd $(1) && ./.venv/bin/python -m pytest tests/ -q
endef

test:
	@for s in $(SECTIONS); do \
		echo "== pytest $$s =="; \
		$(MAKE) --no-print-directory test-$$s || exit 1; \
	done

test-section1:
	$(call TEST_RECIPE,section1)
test-section2:
	$(call TEST_RECIPE,section2)
test-section3:
	$(call TEST_RECIPE,section3)
test-section4:
	$(call TEST_RECIPE,section4)
test-section5:
	$(call TEST_RECIPE,section5)
test-section6:
	$(call TEST_RECIPE,section6)
test-section8:
	$(call TEST_RECIPE,section8)
test-section10:
	$(call TEST_RECIPE,section10)

test-section11:
	$(call TEST_RECIPE,section11)

# MCP server is not a section: its pyproject already points at contracts.
lint-mcp:
	cd mcp && ./.venv/bin/ruff check src tests
test-mcp:
	cd mcp && ./.venv/bin/python -m pytest tests -q

# Hx language package is not a section either. Its venv installs contracts
# first (path dependency) exactly like mcp and bootstrap.
lint-language:
	cd packages/language && ./.venv/bin/ruff check src tests scripts
test-language:
	cd packages/language && ./.venv/bin/python -m pytest tests -q


# -------------------------------------------------------------------- frontend
# Granular halves so ci.yml steps map 1:1 (tsc and vitest report separately).
frontend-tsc:
	cd $(FRONTEND) && npx tsc --noEmit

frontend-vitest:
	cd $(FRONTEND) && npx vitest run

frontend: frontend-tsc frontend-vitest

# The compiled client shipped in git (clients/ts/dist) is the published
# artifact, so it must be rebuilt whenever its sources change. Requires
# `npm ci -C clients/ts`; exits 2 (loudly, never a silent pass) without it.
check-ts-dist:
	./scripts/sdk-gen/check_ts_dist.sh

# S-02/S-01: offline cache check. Replays a recorded five-attempt run through the
# real executor seam and bench_stats; zero spend, no Docker/PG. It proves the frozen
# prefix bytes and the cache-read arithmetic, NOT a real cache-hit ratio (that needs
# a live paid batch on the owner's Mac). Also runs in CI via
# section4/tests/unit/test_cache_offline_check.py.
cache-offline-check:
	./section4/.venv/bin/python scripts/eval/cache_offline_check.py

# I-04: regenerate the benchmark leaderboard (markdown + self-contained HTML)
# from the raw w7-bench attempt files. Rendering is offline and zero-spend;
# the cells it shows are whatever evidence exists (dry corpus -> no ranked
# rows). Rerun after each P-13 batch lands in docs/briefs/evidence/w7-bench/.
bench-leaderboard:
	./section1/.venv/bin/python scripts/eval/bench_leaderboard.py

# ------------------------------------------------------------------------- e2e
# Pair contract H-P2: H3b defines the target, H3a consumes it. Until H3a lands
# section7/e2e/ + playwright.config.ts, this fails LOUDLY with exit 2 rather
# than pretending to pass. When specs land, this target works unchanged and
# joins `make gate` + the CI recipe H3a wires up.
e2e:
	@if [ ! -f section7/playwright.config.ts ]; then \
		echo "make e2e: section7/playwright.config.ts not present yet."; \
		echo "  Lane H3a owns the spec files; this target is the single entry"; \
		echo "  point CI will call (pair contract H-P2). Nothing to run."; \
		exit 2; \
	fi
	cd section7 && npx playwright test

# ---------------------------------------------------------------- e2e-compose
# OPS-20: full zero-key compose journey (scripts/e2e/compose_journey.py):
# readiness → passwordless login → demo seed (+idempotency) → run aggregate →
# engine truth → event replay → audit → sandbox create/exec/destroy →
# cross-tenant no-oracle → metrics → frontend. Requires the compose stack on
# the default localhost ports and HARNESS_MASTER_KEY in the environment
# (migrate refuses an empty secret by design; the CI e2e-compose job
# generates one via openssl rand -hex 32).
e2e-compose:
	python3 scripts/e2e/compose_journey.py

# ------------------------------------------------------------- dev OTP (X-03)
# 10X Wave A: the README documents `docker compose logs identity | grep
# HARNESS-OTP`; this target is that, guarded, so nobody greps logs by hand.
# Needs the compose stack running and no SMTP configured (codes print to logs).
DEV_OTP_TAIL ?= 5
dev-otp:
	@cid="$$(docker compose -f "$(CURDIR)/deploy/docker-compose.yml" ps -q identity 2>/dev/null)"; \
	if [ -z "$$cid" ]; then \
		echo "dev-otp: identity service is not running — start the stack first:"; \
		echo "  docker compose --env-file .env -f deploy/docker-compose.yml up -d"; \
		exit 1; \
	fi; \
	codes="$$(docker compose -f "$(CURDIR)/deploy/docker-compose.yml" logs identity 2>&1 | grep HARNESS-OTP | tail -n "$(DEV_OTP_TAIL)")"; \
	if [ -z "$$codes" ]; then \
		echo "dev-otp: no HARNESS-OTP codes in the logs yet — request a login code on the UI first."; \
		exit 1; \
	fi; \
	printf '%s\n' "$$codes"

# -------------------------------------------------------------------- drills
# External-validation drills. Each writes evidence under
# docs/briefs/evidence/pilot-prep/ and requires the dev compose stack unless
# noted otherwise. Not part of `make gate` (they pull images / need Linux).
drill-oidc:
	./scripts/oidc_drill.sh

drill-pitr:
	./scripts/pitr_drill.sh

drill-native:
	./scripts/native_linux_drill.sh

# W15: admission fairness on the real PostgreSQL dialect. Creates and drops a
# throwaway database on the compose Postgres (127.0.0.1:5499); no engine run,
# no model call, no spend. Override the DSN with HARNESS_PG_DSN.
drill-fairness:
	section4/.venv/bin/python scripts/e2e/w15_fairness_drill.py

# W15: per-team rate budget on /models/call. PRECONDITION: the models service
# must be booted with a small HARNESS_TEAM_MODEL_CALL_LIMIT (the shipped default
# of 300/min would need 301 live calls). Example:
#   HARNESS_TEAM_MODEL_CALL_LIMIT=3 docker compose -f deploy/docker-compose.yml \
#     up -d --no-deps --force-recreate models
#   make drill-rate-limit W15_TEAM_CALL_LIMIT=3
drill-rate-limit:
	W15_TEAM_CALL_LIMIT=$${W15_TEAM_CALL_LIMIT:-3} python3 scripts/e2e/w15_rate_limit_drill.py

# W15: routing evidence on the live stack. Needs the orchestrator + identity
# running; uses probe model ids that exist in no catalog, so it cannot reach a
# provider or spend money — the decision is what is under test.
drill-routing:
	section4/.venv/bin/python scripts/e2e/w15_routing_drill.py

# W16: seat revocation on the live stack — identity's own door plus a service
# that resolves principals through the shared contract dependency. Touches only
# the throwaway drill user it creates.
drill-seat-revocation:
	python3 scripts/e2e/w16_seat_revocation_drill.py

# W16 slice 3: an IdP-side deprovisioning, proven against the deployed
# containers and a real Postgres (needs the stack up + migration 0070).
drill-scim:
	python3 scripts/e2e/w16_scim_deprovision_drill.py

# W16 slice 4. section1's venv on purpose: Part B seeds a throwaway database
# through the ORM and drives the real delivery loop with sqlalchemy/asyncpg.
drill-siem:
	section1/.venv/bin/python scripts/e2e/w16_siem_push_drill.py

# -------------------------------------------------------------------- pg-probe
# Nightly-pg-probe job parity. Guarded: silently skipping would fake parity.
# Prereqs: Postgres reachable at :5499 (compose stack) with harness/harness creds.
# DSN/container are derived from PG_URL and the compose `db` service (override either), so a
# non-default port or project name needs no test edits.
PG_COMPOSE := docker compose -f "$(CURDIR)/deploy/docker-compose.yml"
PG_ENV = HARNESS_TEST_PG=1 HARNESS_DATABASE_URL="$(PG_URL)" \
	HARNESS_TEST_PG_DSN="$(subst +asyncpg,,$(PG_URL))" \
	HARNESS_TEST_PG_CONTAINER="$${HARNESS_TEST_PG_CONTAINER:-$$($(PG_COMPOSE) ps -q db)}"

# Idempotently start the compose db and wait until it accepts connections.
pg-up:
	@if [ "$${HARNESS_TEST_PG_EXTERNAL:-}" = "1" ]; then \
		echo "pg-up: using the caller-supplied Postgres (HARNESS_TEST_PG_EXTERNAL=1)"; \
	else \
		$(PG_COMPOSE) up -d db; \
		for i in $$(seq 1 60); do $(PG_COMPOSE) exec -T db pg_isready -U harness >/dev/null 2>&1 && exit 0; sleep 1; done; \
		echo "pg-up: db not ready after 60s"; exit 1; \
	fi

pg-probe: pg-probe-integration pg-probe-s1 pg-probe-migrate

pg-probe-integration: pg-up
	@HARNESS_TEST_PG=1 $(MAKE) --no-print-directory pg-guard
	cd integration && $(PG_ENV) ../section1/.venv/bin/python -m pytest test_checkpoint1.py -q

pg-probe-s1: pg-up
	@HARNESS_TEST_PG=1 $(MAKE) --no-print-directory pg-guard
	cd section1 && $(PG_ENV) ./.venv/bin/python -m pytest tests/ -q

# M17-B2 (OPS-15): execute deploy/runners/migrate.py ITSELF against real PG on
# a per-run scratch database — 0001→latest must apply clean and re-run as a
# no-op. This is the gate CI never ran before: backend suites build schemas via
# SQLite Base.metadata.create_all and even the nightly PG probe used create_all,
# so the dollar-quote splitter ($$ DO-blocks), statement ordering, the
# legacy-marking branch and the compose-up re-run path all shipped unexercised.
# Never touches the live cluster: scratch DB only, dropped on exit.
pg-probe-migrate: pg-up
	@HARNESS_TEST_PG=1 $(MAKE) --no-print-directory pg-guard
	$(PG_ENV) ./section1/.venv/bin/python deploy/runners/migrate_selftest.py

pg-guard:
	@test "$${HARNESS_TEST_PG:-}" = "1" || { \
		echo "make pg-probe: refusing to run without HARNESS_TEST_PG=1 (real Postgres required)."; \
		echo "  Start the stack: docker compose -f deploy/docker-compose.yml up -d db"; \
		echo "  Then: HARNESS_TEST_PG=1 make pg-probe"; \
		exit 2; \
	}

# ------------------------------------------------------------- test-all (X-02)
# 10X Wave A: one command for the flag-gated (skipped-by-default) test groups.
# Provisions what it can (compose db, P0 images), runs what is available, and
# prints an honest skip ledger for the rest — it never fakes parity. Explicitly
# opt-in groups are NOT run here, by design: HARNESS_P0_LIVE needs a ~7.6GB
# local model; HARNESS_LIVE_OPENROUTER spends money and needs a key file.
TEST_ALL_DOCKER ?= 1
test-all: pg-probe
	@echo ""
	@echo "== test-all: section4 suite against real PG (PG-gated tests unskipped) =="
	@HARNESS_TEST_PG=1 $(MAKE) --no-print-directory pg-guard
	cd section4 && $(PG_ENV) ./.venv/bin/python -m pytest tests/ -q
	@echo ""
	@echo "== test-all: Docker P0 isolation tests (section4) =="
	@if [ "$(TEST_ALL_DOCKER)" != "1" ]; then \
		echo "SKIP Docker P0 (TEST_ALL_DOCKER=0)"; \
	elif ! docker info >/dev/null 2>&1; then \
		echo "SKIP Docker P0: docker daemon not reachable"; \
	else \
		echo "-- building harness-engine-worker:p0 and :p0-tests (repo-root context) --"; \
		docker build -q -f section4/worker.Dockerfile -t harness-engine-worker:p0 . && \
		docker build -q -f section4/worker.Dockerfile --target isolation-tests -t harness-engine-worker:p0-tests . && \
		cd section4 && HARNESS_P0_DOCKER=1 ./.venv/bin/python -m pytest tests/integration/test_container_p0.py -q; \
	fi
	@echo ""
	@echo "== test-all: promtool-gated section1 tests =="
	@if command -v promtool >/dev/null 2>&1; then \
		cd section1 && ./.venv/bin/python -m pytest tests/test_alert_rules_load.py tests/test_deploy_observability.py -q; \
	else \
		echo "SKIP promtool tests (install: brew install prometheus)"; \
	fi
	@echo ""
	@echo "== test-all: landlock (section5) =="
	@if [ "$$(uname -s)" = "Linux" ]; then \
		cd section5 && ./.venv/bin/python -m pytest tests/unit/test_landlock_confinement.py tests/unit/test_landlock_read_isolation.py tests/unit/test_confinement.py -q; \
	else \
		echo "SKIP landlock tests: linux-only on this host (they do run inside a Linux container; see docs/briefs/evidence/security/)"; \
	fi
	@echo ""
	@echo "== test-all: explicitly opt-in groups NOT run (by design) =="
	@echo "  HARNESS_P0_LIVE=1          real local model (ollama pull qwen3.5:9b, ~7.6GB)"
	@echo "  HARNESS_LIVE_OPENROUTER=1  paid endpoint + key file (see .env.example)"
	@echo "test-all done: PG probes + section4 PG suite + Docker P0 + promtool + landlock dispositioned above."

# ----------------------------------------------------------------- pip-audit
# WARN-posture parity for the security-scan job (continue-on-error in CI).
pip-audit:
	cd section1 && ./.venv/bin/pip install -q pip-audit && ./.venv/bin/pip-audit --progress-spinner off

# ------------------------------------------------------- readiness drift (G-A)
# Is the deployed image set still the reviewed set? CI mode (manifest vs HEAD,
# no Docker) is the default; make readiness-drift-live compares the RUNNING
# stack (default project harness-rc) against the same manifest. Exit 1 = drift.
READINESS_MANIFEST := docs/briefs/evidence/90day/image-manifest-vv90r6-deployed.json
READINESS_PROJECT ?= harness-rc

readiness-drift:
	python3 scripts/ops/readiness_drift.py --ci --manifest $(READINESS_MANIFEST)

readiness-drift-live:
	python3 scripts/ops/readiness_drift.py --manifest $(READINESS_MANIFEST) --project $(READINESS_PROJECT)

readiness-manifest:
	python3 scripts/ops/readiness_drift.py --write-manifest $(READINESS_MANIFEST) --project $(READINESS_PROJECT)

# ------------------------------------------------------------- route law (M15-G6a)
# W4-carryover architectural laws as CI gates: route modules export default
# ONLY; sections import only contracts/. Both modes run in ONE static-gates
# step via the aggregator; granular targets exist so a red run names its law.
GUARD_SCRIPT := section7/scripts/ci_route_module_guard.sh

guard-routes:
	@$(MAKE) --no-print-directory guard-script-check
	@bash $(GUARD_SCRIPT) routes

guard-isolation:
	@$(MAKE) --no-print-directory guard-script-check
	@bash $(GUARD_SCRIPT) isolation

guard-route-law: guard-routes guard-isolation

# sh -n parse gate for the guard itself — a broken guard must fail LOUDLY,
# never silently skip. Same posture as entrypoint.sh's static gate.
guard-script-check:
	sh -n $(GUARD_SCRIPT)


# ----------------------------------------------------------------------- gate
# Everything branch protection blocks a PR on. (e2e joins here when H3a's
# specs land; nightly pg-probe and security-scan stay schedule/push-scoped.)
gate: static-gates lint lint-mcp test test-mcp lint-language test-language frontend
	@echo ""
	@echo "GATE GREEN — static-gates + lint + test + lint-mcp/test-mcp + lint-language/test-language + frontend all passed."
	@echo "This is byte-for-byte what ci.yml enforces on PRs."

# ------------------------------------------------------------------- ci-local
# TE-1: run ci.yml's gate steps on this machine when GitHub Actions cannot
# (2026-11-21: account billing). The job list is READ from ci.yml by
# scripts/ci_local.py, never copied: same jobs, same order, same `if`s and
# path filters; skipped jobs/steps are reported as skipped with a reason.
# Writes a dated junit + markdown summary under docs/briefs/evidence/quality/ci-local/.
# Service-Postgres jobs need a throwaway container, never the compose db:
#   make ci-local CI_LOCAL_ARGS="--pg-container <name> --pg-port <port>"
# PR mode: CI_LOCAL_ARGS="--event pull_request --base origin/master"
ci-local:
	section4/.venv/bin/python scripts/ci_local.py $(CI_LOCAL_ARGS)
