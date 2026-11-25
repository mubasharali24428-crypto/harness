# ci-local 2026-11-25T0354Z

- revision: `f73ada2697e065e19ba87fc577a6dbdc824ff234`
- event: `dispatch`
- path filters matched: section1, section2, section3, section4, section5, section6, section8, section10, section11, mcp, language, e2e, migrations
- result: **FAILED** (6 failed step(s))

| Job | Step | Result | s | Note |
|---|---|---|---|---|
| changes | paths-filter | ok | 0 | event=dispatch; filters=['section1', 'section2', 'section3', 'section4', 'section5', 'section6', 'section8', 'section10', 'section11', 'mcp', 'language', 'e2e', 'migrations'] |
| static-gates | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| static-gates | entrypoint.sh parses (GAP-F1 regression guard) [make static-gates] | ok | 0 |  |
| static-gates | compose file valid [make static-gates] | ok | 0 |  |
| static-gates | eval corpus contract holds (W14 gate) [make static-gates] | ok | 0 |  |
| static-gates | every runtime env knob is documented (W15 gate) [make static-gates] | ok | 0 |  |
| static-gates | TS contract mirror is current (E-codegen drift guard) [make static-gates] | ok | 0 |  |
| static-gates | Route-module + isolation laws (M15-G6a) [make guard-routes + make guard-isolation] | ok | 1 |  |
| python-tests[section1] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section1] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section1] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section1] | ruff [make lint-section1] | ok | 0 |  |
| python-tests[section1] | pytest [make test-section1] | FAILED | 144 | exit 2 |
| python-tests[section2] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section2] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section2] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section2] | ruff [make lint-section2] | ok | 0 |  |
| python-tests[section2] | pytest [make test-section2] | ok | 4 |  |
| python-tests[section3] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section3] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section3] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section3] | ruff [make lint-section3] | ok | 0 |  |
| python-tests[section3] | pytest [make test-section3] | ok | 3 |  |
| python-tests[section4] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section4] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section4] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section4] | ruff [make lint-section4] | ok | 0 |  |
| python-tests[section4] | pytest [make test-section4] | FAILED | 183 | exit 2 |
| python-tests[section5] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section5] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section5] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section5] | ruff [make lint-section5] | ok | 0 |  |
| python-tests[section5] | pytest [make test-section5] | FAILED | 32 | exit 2 |
| python-tests[section6] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section6] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section6] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section6] | ruff [make lint-section6] | ok | 0 |  |
| python-tests[section6] | pytest [make test-section6] | ok | 6 |  |
| python-tests[section8] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section8] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section8] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section8] | ruff [make lint-section8] | ok | 0 |  |
| python-tests[section8] | pytest [make test-section8] | ok | 5 |  |
| python-tests[section10] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section10] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section10] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section10] | ruff [make lint-section10] | ok | 0 |  |
| python-tests[section10] | pytest [make test-section10] | ok | 3 |  |
| python-tests[section11] | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| python-tests[section11] | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| python-tests[section11] | Install deps (contracts + section, shared venv) | provisioning | 0 | not a gate step |
| python-tests[section11] | ruff [make lint-section11] | ok | 0 |  |
| python-tests[section11] | pytest [make test-section11] | ok | 2 |  |
| mcp | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| mcp | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| mcp | Install mcp (contracts first, same as make bootstrap) | provisioning | 0 | not a gate step |
| mcp | ruff [make lint-mcp] | ok | 0 |  |
| mcp | pytest [make test-mcp] | ok | 2 |  |
| language | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| language | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| language | Install harness-hx (contracts first, same as make bootstrap) | provisioning | 0 | not a gate step |
| language | ruff [make lint-language] | ok | 0 |  |
| language | pytest [make test-language] | ok | 1 |  |
| frontend | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| frontend | actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 | provisioning | 0 | not a gate step |
| frontend | npm ci --no-audit --no-fund | provisioning | 0 | not a gate step |
| frontend | tsc [make frontend-tsc] | ok | 6 |  |
| frontend | vitest [make frontend-vitest] | ok | 9 |  |
| frontend | npm ci (clients/ts) | provisioning | 0 | not a gate step |
| frontend | published TS dist is current [make check-ts-dist] | FAILED | 0 | exit 2 |
| e2e-compose | (job) | SKIPPED | 0 | opt-in locally: boots the full compose stack on the default ports (the demo stack's) and ends with `down -v` (pass --allow e2e-compose) |
| nightly-pg-probe | (job) | SKIPPED | 0 | needs a service Postgres: pass --pg-container and --pg-port |
| migrations-pg | (job) | SKIPPED | 0 | needs a service Postgres: pass --pg-container and --pg-port |
| readiness-drift | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| readiness-drift | manifest coherence [make readiness-drift] | FAILED | 0 | exit 2 |
| security-scan | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| security-scan | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| security-scan | Install section1 env (audit target) | provisioning | 0 | not a gate step |
| security-scan | pip-audit (WARN only this wave) [make pip-audit] | WARN | 0 | exit 127, continue-on-error |
| security-scan | gitleaks (official action, blocking) | FAILED | 0 | exit 1 |
| native-linux-container | (job) | SKIPPED | 0 | opt-in locally: Landlock cannot apply on a macOS bind mount; this drill needs a Linux kernel (pass --allow native-linux-container) |

## pytest counts per step

| Step | passed | failed | skipped |
|---|---|---|---|
| language-4 | 367 | 0 | 0 |
| mcp-4 | 18 | 0 | 0 |
| python-tests[section10]-4 | 96 | 0 | 0 |
| python-tests[section11]-4 | 58 | 0 | 2 |
| python-tests[section1]-4 | 1290 | 8 | 63 |
| python-tests[section2]-4 | 61 | 0 | 0 |
| python-tests[section3]-4 | 149 | 0 | 0 |
| python-tests[section4]-4 | 2457 | 4 | 62 |
| python-tests[section5]-4 | 471 | 32 | 62 |
| python-tests[section6]-4 | 123 | 0 | 0 |
| python-tests[section8]-4 | 111 | 0 | 0 |

## Local adaptations

- gitleaks: local zricethezav/gitleaks image (history reachable from HEAD) instead of gitleaks-action
- pip-audit: audits section1/.venv by --path (does not install into the shared venv)
