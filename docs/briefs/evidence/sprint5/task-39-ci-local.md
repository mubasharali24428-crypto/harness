# Task 39 — `make ci-local` snapshot + C-07 template

- Date (UTC): 2026-11-25
- Tree: `f73ada2` (Task 38) on `w28-s2-sprints1-4`
- Host: cloud container (Linux, root, **no Docker daemon**), venvs from `make bootstrap`
- Command: `make ci-local` (event `dispatch`, no `--pg-*` flags), 6m41s wall clock
- Overall result: **FAILED** — 6 failed steps, all explained below as environment; none investigated as product defects.
- Raw summary committed: `docs/briefs/evidence/quality/ci-local/2026-11-25T0354Z-f73ada26/summary.md` (+ `summary.xml`).
  Per-step logs and pytest junit were not committed (3.7 MB, very long lines).
- `scripts/ci_local.py` needed no fix.

## Passed (ran for real)

static-gates (all 6 steps), ruff for every section, pytest for section2 (61), section3 (149), section6 (123),
section8 (111), section10 (96), section11 (58 + 2 skipped), mcp (18), language (367), frontend `tsc` and `vitest`.

## Failed / blocked (not PASS)

| Step | Result | Reason |
|---|---|---|
| python-tests[section1] pytest | FAILED (1290 pass, 8 fail, 63 skip) | 8 errors in `tests/test_runs_index_tuning.py` (the Postgres/`docker exec` tuning tests, same family as Task 38 G2) plus sqlite "too many SQL variables" in this container. Needs Docker + PG → owner Mac |
| python-tests[section4] pytest | FAILED (2457 pass, 4 fail, 62 skip) | The same 4 env failures as Task 38 G4: two real-container tests (no daemon), root launcher uid, zombie after group kill |
| python-tests[section5] pytest | FAILED (471 pass, 32 fail, 62 skip) | `SecurityPolicyViolationError: sandbox confinement 'auto' found no usable OS boundary` (Firecracker kernel, no Landlock) — checkpoint, authz, stream-event and tree-API tests. Needs a host with a usable boundary → owner Mac |
| frontend check-ts-dist | FAILED | `clients/ts` dependencies not installed (`npm ci -C clients/ts` was not run by bootstrap). Not retried |
| readiness-drift | FAILED | Real finding, expected: migrations on disk 0081 vs deployed manifest 0076 (vv90r6). Clears on redeploy + `--write-manifest` (owner lane) |
| security-scan gitleaks | FAILED | Needs the docker API; no daemon |
| security-scan pip-audit | WARN only | `pip-audit` not installed (continue-on-error in CI) |

## Skipped by design

e2e-compose, nightly-pg-probe, migrations-pg (no service PG passed), native-linux-container: reported SKIPPED by ci_local, not run.

## Conclusion

Do not read this as a green gate. Static gates, lint and most suites pass; section1/4/5, gitleaks and
check-ts-dist remain to be re-run on the owner Mac (Docker, usable sandbox boundary). Readiness drift needs a redeploy.

## C-07 template

`docs/briefs/evidence/sprint5/c07-pilot-feedback-template.md`.
