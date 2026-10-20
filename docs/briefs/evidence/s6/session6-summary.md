# Session 6 close-out — plans, execution, artifacts, search and export

## Scope and matrix

- Part 44 was **partial** at Session 6 close (no route approved a native plan version). It was closed locally
  afterwards by `00a1412`: see `part44-version-approval.md`.
- Parts 48 and 50 are **verified** against their documented scope and explicit limits. Matrix rows
  cite lane evidence and docs.
- Section 7 now exposes the Session 6 workbench on run details. Native controls use labeled form
  elements, buttons, safe text preview and the shared live-announcer pattern.

## Commands and results

| Check | Result |
|---|---|
| PL tests | 53 passed |
| EX tests | 66 passed |
| AR tests | 93 passed |
| SE tests | 74 passed |
| Integrated policy/spec tests | 8 passed in addition to the SE tests; specs/UI calls match |
| Session 6 PostgreSQL probe | skipped; opt-in test added, Docker socket unavailable and port 5499 refused connections |
| Section 7 typecheck | `npm run typecheck` passed |
| Section 7 UI test | 1 passed for workbench labels, native keyboard controls and live regions |
| Section 7 full Vitest | 817 passed (68 files), including the workbench test |
| Full Section 1 suite | 923 passed, 29 skipped, 21 setup errors; SMTP/OIDC tests could not bind localhost sockets in this sandbox |
| Full Section 4 suite | incomplete: 1353 passed, 37 skipped, 3 failures observed, then stalled at 93% and was interrupted at 159 s. Failures were one 1.011 s cost-rollup threshold and two macOS Seatbelt `sandbox_apply` failures |
| Lane-file Ruff | all Session 6 implementation/test files passed after formatting |
| Full Section 1 Ruff | failed on three existing findings in untracked `section1/scripts/verify_audit_chain.py` |
| Full Section 4 Ruff | `cd section4 && .venv/bin/ruff check src tests` — all checks passed |
| Full Section 1 Ruff | failed on three findings in the unrelated untracked `scripts/verify_audit_chain.py` |
| `spec_drift.py --write`, SDK Python/TS generation, spec drift check | passed; recorded specs and UI calls match |
| `make pg-probe-migrate` | not run to completion: Docker daemon socket absent |
| OTP smoke | attempted once; not run because port 8101 was occupied. The script declined to stop the process |

The per-lane test commands and invariant coverage are in `lane-pl.md`, `lane-ex.md`, `lane-ar.md`,
and `lane-se.md`. No real-browser keyboard or axe test was run.

## Open limits

- (Superseded by `part44-version-approval.md`: native versions are approvable and run by the engine; the limit sweep is
  scheduled; the tool-call cap counts reported calls.) In-flight model/cost usage is still invisible until a call completes.
- Artifact bytes and export files use local disk stores. Retention and search-index workers have no
  deploy schedule.
- Git state is caller-recorded, merge approval is not a GitHub branch-protection check, and the
  artifact classifier is not antivirus.
- PostgreSQL migrations, full-text search, and cross-connection races remain unverified here.
- `make gate`, other `make e2e*`, `pip-audit`, `run_smoke.py --real`, Linux Landlock, Section 5
  tests, multi-process races and browser keyboard/axe tests were not run.

## Next

Prepare Session 7 around parts 49, 51 and 57. Part 57 remains capped at 8 pending live Stripe and
OIDC provider evidence. Session 7 must not imply that Session 6's part 44 gap was closed.
