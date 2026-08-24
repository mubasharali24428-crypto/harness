# Harness monorepo — unified CI/local gates (M13-H3b, closes H-024 + H-025).
#
# PARITY CONTRACT: every command CI runs is defined HERE and nowhere else.
# .github/workflows/ci.yml jobs call these targets verbatim (`make test-section1`,
# `make lint-section3`, ...) so local and CI literally execute the same lines —
# drift between "what passed locally" and "what CI ran" is structurally impossible.
# Provisioning (venv creation, npm ci, pip installs) stays in CI as setup steps;
# the GATES themselves live only in this file. Full mapping: docs/ci-gates.md.
#
#   make lint            ruff across all backend sections (CI matrix order)
#   make test            pytest matrix, one venv per section (CI matrix order)
#   make frontend        section7 tsc --noEmit + vitest run
#   make static-gates    entrypoint.sh parse + compose config + TS mirror sync
#                        + M15-G6a route/isolation law guards
#   make e2e             section7 Playwright hero-journey (specs: lane H3a)
#   make pg-probe        nightly parity: real-PG integration + S1 suite (needs PG)
#   make gate            EVERYTHING a PR must pass (static-gates lint test frontend)
#
# Per-section granular targets (used by CI matrix jobs): make test-section<N>,
# make lint-section<N> for N in 1 2 3 4 5 6 8 10.

SECTIONS := section1 section2 section3 section4 section5 section6 section8 section10

# Nightly probe DSN — identical to the ci.yml service container mapping (5499->5432).
PG_URL ?= postgresql+asyncpg://harness:harness@localhost:5499/harness

.PHONY: gate help $(foreach s,$(SECTIONS),lint-$(s) test-$(s)) \
        lint test frontend frontend-tsc frontend-vitest static-gates e2e \
        pg-probe pg-probe-integration pg-probe-s1 pip-audit \
        guard-routes guard-isolation guard-route-law

help:
	@echo "Harness gates — single source of truth shared by CI (.github/workflows/ci.yml):"
	@echo "  make lint            ruff across: $(SECTIONS)"
	@echo "  make test            pytest matrix (same sections, CI order)"
	@echo "  make frontend        section7: tsc --noEmit + vitest run"
	@echo "  make static-gates    sh -n entrypoint.sh; compose config; TS mirror --check"
	@echo "                       + route/isolation law guards (M15-G6a)"
	@echo "  make guard-routes    app/**/page.tsx default-export-only law (W4 carryover)"
	@echo "  make guard-isolation sections import ONLY contracts/ (M13 isolation law)"
	@echo "  make e2e             section7 Playwright suite (specs land via lane H3a)"
	@echo "  make pg-probe        real-PG probe: integration checkpoint + S1 suite (needs PG :5499)"
	@echo "  make pip-audit       dependency audit (WARN-posture parity with CI)"
	@echo "  make gate            static-gates + lint + test + frontend"
	@echo "  make lint-section<N> / test-section<N>   one section, exactly CI's command"

# ---------------------------------------------------------------- static gates
# M15-G6a added the route-module + isolation law guards (guard-route-law) to
# this aggregate so `make gate` locally covers byte-for-byte what the CI
# static-gates JOB enforces on PRs (the job lists the same commands stepwise).
static-gates: guard-route-law
	sh -n deploy/entrypoint.sh
	docker compose -f deploy/docker-compose.yml config -q
	python3 contracts/ts/generate.py --check

# ------------------------------------------------------------------------ lint
# The 'scripts || no-scripts' fallback mirrors the original CI line verbatim:
# not every section carries a scripts/ tree, and ruff exits nonzero on a
# missing path. Same expression locally and in CI — no third variant allowed.
define LINT_RECIPE
cd $(1) && ./.venv/bin/ruff check src tests scripts || ./.venv/bin/ruff check src tests
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

# -------------------------------------------------------------------- frontend
# Granular halves so ci.yml steps map 1:1 (tsc and vitest report separately).
frontend-tsc:
	cd section7 && npx tsc --noEmit

frontend-vitest:
	cd section7 && npx vitest run

frontend: frontend-tsc frontend-vitest

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

# -------------------------------------------------------------------- pg-probe
# Nightly-pg-probe job parity. Guarded: silently skipping would fake parity.
# Prereqs: Postgres reachable at :5499 (compose stack) with harness/harness creds.
PG_ENV := HARNESS_TEST_PG=1 HARNESS_DATABASE_URL="$(PG_URL)"

pg-probe: pg-probe-integration pg-probe-s1

pg-probe-integration:
	@$(MAKE) --no-print-directory pg-guard
	cd integration && $(PG_ENV) ../section1/.venv/bin/python -m pytest test_checkpoint1.py -q

pg-probe-s1:
	@$(MAKE) --no-print-directory pg-guard
	cd section1 && $(PG_ENV) ./.venv/bin/python -m pytest tests/ -q

pg-guard:
	@test "$${HARNESS_TEST_PG:-}" = "1" || { \
		echo "make pg-probe: refusing to run without HARNESS_TEST_PG=1 (real Postgres required)."; \
		echo "  Start the stack: docker compose -f deploy/docker-compose.yml up -d db"; \
		echo "  Then: HARNESS_TEST_PG=1 make pg-probe"; \
		exit 2; \
	}

# ----------------------------------------------------------------- pip-audit
# WARN-posture parity for the security-scan job (continue-on-error in CI).
pip-audit:
	cd section1 && ./.venv/bin/pip install -q pip-audit && ./.venv/bin/pip-audit --progress-spinner off

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
gate: static-gates lint test frontend
	@echo ""
	@echo "GATE GREEN — static-gates + lint + test + frontend all passed."
	@echo "This is byte-for-byte what ci.yml enforces on PRs."
