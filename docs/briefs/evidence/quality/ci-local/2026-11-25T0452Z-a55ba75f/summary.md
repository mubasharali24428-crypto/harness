# ci-local 2026-11-25T0452Z

- revision: `a55ba75f44947ca2ddc43582618febda02b529ee`
- event: `dispatch`
- path filters matched: section1, section2, section3, section4, section5, section6, section8, section10, section11, mcp, language, e2e, migrations
- result: **GREEN** (0 failed step(s))

| Job | Step | Result | s | Note |
|---|---|---|---|---|
| changes | paths-filter | ok | 0 | event=dispatch; filters=['section1', 'section2', 'section3', 'section4', 'section5', 'section6', 'section8', 'section10', 'section11', 'mcp', 'language', 'e2e', 'migrations'] |
| static-gates | (job) | SKIPPED | 0 | not selected (--only) |
| python-tests | (job) | SKIPPED | 0 | not selected (--only) |
| mcp | (job) | SKIPPED | 0 | not selected (--only) |
| language | (job) | SKIPPED | 0 | not selected (--only) |
| frontend | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| frontend | actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 | provisioning | 0 | not a gate step |
| frontend | npm ci --no-audit --no-fund | provisioning | 0 | not a gate step |
| frontend | tsc [make frontend-tsc] | ok | 2 |  |
| frontend | vitest [make frontend-vitest] | ok | 5 |  |
| frontend | npm ci (clients/ts) | provisioning | 0 | not a gate step |
| frontend | published TS dist is current [make check-ts-dist] | ok | 1 |  |
| e2e-compose | (job) | SKIPPED | 0 | not selected (--only) |
| nightly-pg-probe | (job) | SKIPPED | 0 | not selected (--only) |
| migrations-pg | (job) | SKIPPED | 0 | not selected (--only) |
| readiness-drift | (job) | SKIPPED | 0 | not selected (--only) |
| security-scan | actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 | provisioning | 0 | not a gate step |
| security-scan | actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38 | provisioning | 0 | not a gate step |
| security-scan | Install section1 env (audit target) | provisioning | 0 | not a gate step |
| security-scan | pip-audit (WARN only this wave) [make pip-audit] | WARN | 11 | exit 1, continue-on-error |
| security-scan | gitleaks (official action, blocking) | ok | 5 |  |
| native-linux-container | (job) | SKIPPED | 0 | not selected (--only) |

## pytest counts per step

| Step | passed | failed | skipped |
|---|---|---|---|

## Local adaptations

- gitleaks: local zricethezav/gitleaks image (history reachable from HEAD) instead of gitleaks-action
- pip-audit: audits section1/.venv by --path (does not install into the shared venv)
